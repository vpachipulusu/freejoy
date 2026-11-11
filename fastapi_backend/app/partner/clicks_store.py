"""How the partner surface reads click counts (ADR-013, FREEJ-59).

``app/partner`` may import only ``app.core`` and itself, so this module cannot use the click repository the admin
report is built on (``app/repositories/merchant_clicks.py``). It describes the two tables it reads with SQLAlchemy
Core and states the same query: grouped in SQL, half open UTC ranges, ``BOT`` rows left out, offers ordered by
clicks, then title, then id. ``tests/api/partner/test_partner_clicks_e2e.py`` seeds one data set and proves both
give the same per offer per day counts for the offers a partner may see.

Differences from the admin report, all on purpose:

* every statement carries the merchant predicate itself (``offers.merchant_id``); none relies on a list of offer ids
  fetched earlier, so a statement given another merchant's offer ids still returns nothing of theirs;
* only offers in ``LISTED_STATUSES`` are read. A draft or an archived offer is never named, even if it has clicks
  (the admin report lists those with clicks; a retailer must not see the title or slug of an unpublished offer);
* it selects the columns the restricted role may read and no others (``commands/sql/partner_role.sql`` grants
  exactly ``offers (id, merchant_id, title, slug, status)`` and ``click_events (offer_id, occurred_at,
  user_agent_class)``; ``test_partner_role_script.py`` ties the grants to the definitions below).

Time ranges are half open (``start <= occurred_at < end``) on UTC midnights, served by
``ix_offers_merchant_id`` and ``ix_click_events_offer_id_occurred_at``. Counts only: nothing here reads a referrer,
a link or anything about a visitor.
"""

from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import DateTime, Enum, Text, and_, column, func, select, table
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

# The same stored values as ``OfferStatus`` and ``UserAgentClass`` (the drift test compares them with the models).
_ENUM_LENGTH = 16
offers = table(
    "offers",
    column("id", Text),
    column("merchant_id", Text),
    column("title", Text),
    column("slug", Text),
    column(
        "status",
        Enum(
            "DRAFT",
            "PUBLISHED",
            "EXPIRED",
            "ARCHIVED",
            name="offer_status",
            native_enum=False,
            create_constraint=False,
            length=_ENUM_LENGTH,
        ),
    ),
)
click_events = table(
    "click_events",
    column("offer_id", Text),
    column("occurred_at", DateTime(timezone=True)),
    column(
        "user_agent_class",
        Enum(
            "DESKTOP",
            "MOBILE",
            "TABLET",
            "BOT",
            "OTHER",
            name="user_agent_class",
            native_enum=False,
            create_constraint=False,
            length=_ENUM_LENGTH,
        ),
    ),
)

# An offer that is or was live is part of the retailer's picture even with no clicks. Same statuses as the admin
# report's ``LISTED_STATUSES``, and here the ONLY ones: a draft or archived offer is never read.
LISTED_STATUSES = ("PUBLISHED", "EXPIRED")


@dataclass(frozen=True)
class PartnerOffer:
    """An offer as the partner sees it. ``offer_id`` is internal: used to join the days, never sent out."""

    offer_id: str
    slug: str
    title: str
    clicks: int


@dataclass(frozen=True)
class PartnerOfferPage:
    offers: list[PartnerOffer]
    # Every listed offer of the merchant, before the limit.
    total_offers: int


@dataclass(frozen=True)
class PartnerDayClicks:
    offer_id: str
    day: date
    clicks: int


def _in_range(start: datetime, end: datetime) -> ColumnElement[bool]:
    return and_(click_events.c.occurred_at >= start, click_events.c.occurred_at < end)


def _counted() -> ColumnElement[bool]:
    return click_events.c.user_agent_class.is_distinct_from("BOT")


def _listed() -> ColumnElement[bool]:
    return offers.c.status.in_(LISTED_STATUSES)


class PartnerClickStore:
    """Click counts on the partner role's session. Read only; the caller owns the session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def offers(
        self, merchant_id: str, start: datetime, end: datetime, limit: int
    ) -> PartnerOfferPage:
        """The merchant's listed offers, most clicked first (then title, then id), at most ``limit``."""
        clicks = func.count(click_events.c.offer_id).filter(_counted())
        statement = (
            select(
                offers.c.id,
                offers.c.slug,
                offers.c.title,
                clicks.label("clicks"),
                func.count().over().label("total_offers"),
            )
            .select_from(offers)
            .outerjoin(
                click_events,
                and_(click_events.c.offer_id == offers.c.id, _in_range(start, end)),
            )
            .where(offers.c.merchant_id == merchant_id, _listed())
            .group_by(offers.c.id, offers.c.slug, offers.c.title)
            .order_by(clicks.desc(), offers.c.title, offers.c.id)
            .limit(limit)
        )
        rows = (await self._session.execute(statement)).all()
        return PartnerOfferPage(
            offers=[PartnerOffer(r[0], r[1], r[2], r[3]) for r in rows],
            total_offers=rows[0][4] if rows else 0,
        )

    async def daily(
        self,
        merchant_id: str,
        offer_ids: list[str],
        start: datetime,
        end: datetime,
    ) -> list[PartnerDayClicks]:
        """Counted clicks per offer per UTC day, only for days that had any.

        ``offer_ids`` only narrows the read to the offers already listed. The merchant predicate is in the
        statement itself, so an id of another merchant's offer (or of an unlisted one) matches nothing.
        """
        if not offer_ids:
            return []
        day = func.date(func.timezone("UTC", click_events.c.occurred_at)).label("day")
        rows = (
            await self._session.execute(
                select(click_events.c.offer_id, day, func.count())
                .select_from(click_events)
                .join(offers, offers.c.id == click_events.c.offer_id)
                .where(
                    offers.c.merchant_id == merchant_id,
                    _listed(),
                    click_events.c.offer_id.in_(offer_ids),
                    _in_range(start, end),
                    _counted(),
                )
                .group_by(click_events.c.offer_id, day)
                .order_by(click_events.c.offer_id, day)
            )
        ).all()
        return [PartnerDayClicks(offer_id, d, n) for offer_id, d, n in rows]
