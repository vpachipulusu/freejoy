"""Read-only queries behind the per-merchant click report.

Everything is grouped in SQL: one statement for the merchant totals, one for the offers (with their click
counts and the full offer count) and one for the per-day counts of the offers returned, so the cost does
not grow with the number of rows. Time ranges are half open (``start <= occurred_at < end``) on UTC
midnights, served by ``ix_click_events_offer_id_occurred_at`` after ``ix_offers_merchant_id``. Rows hold
no visitor data, and nothing here returns any: counts only.

Bot rule: a click whose ``user_agent_class`` is ``BOT`` is left out of every count and reported apart as
``bot_clicks``. A click with no class (``NULL``) is counted, because the class is a best-effort label.
"""

from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import and_, func, or_, select
from sqlalchemy.sql.elements import ColumnElement

from app.models.platform.click_event import ClickEvent, UserAgentClass
from app.models.platform.merchant import Merchant
from app.models.platform.offer import Offer, OfferStatus
from app.repositories.base import BaseRepository

# An offer that is or was live is part of the retailer's picture even with no clicks; a draft or an
# archived offer appears only when it did receive clicks in the range.
LISTED_STATUSES = (OfferStatus.PUBLISHED, OfferStatus.EXPIRED)


@dataclass(frozen=True)
class MerchantRef:
    id: str
    name: str
    slug: str


@dataclass(frozen=True)
class MerchantClickTotals:
    clicks: int
    bot_clicks: int


@dataclass(frozen=True)
class OfferClicks:
    offer_id: str
    slug: str
    title: str
    clicks: int


@dataclass(frozen=True)
class OfferClicksPage:
    offers: list[OfferClicks]
    # Every offer that qualifies for the report, before the limit.
    total_offers: int


@dataclass(frozen=True)
class DayClicks:
    offer_id: str
    day: date
    clicks: int


def _in_range(start: datetime, end: datetime) -> ColumnElement[bool]:
    return and_(ClickEvent.occurred_at >= start, ClickEvent.occurred_at < end)


def _counted() -> ColumnElement[bool]:
    return ClickEvent.user_agent_class.is_distinct_from(UserAgentClass.BOT)


def _bot() -> ColumnElement[bool]:
    return ClickEvent.user_agent_class == UserAgentClass.BOT


class MerchantClickRepository(BaseRepository):
    async def get_merchant(self, merchant_id: str) -> MerchantRef | None:
        row = (
            await self._session.execute(
                select(Merchant.id, Merchant.name, Merchant.slug).where(
                    Merchant.id == merchant_id
                )
            )
        ).first()
        return MerchantRef(*row) if row else None

    async def totals(
        self, merchant_id: str, start: datetime, end: datetime
    ) -> MerchantClickTotals:
        """Clicks and bot clicks over every offer of the merchant, whatever its status."""
        row = (
            await self._session.execute(
                select(
                    func.count().filter(_counted()),
                    func.count().filter(_bot()),
                )
                .select_from(ClickEvent)
                .join(Offer, Offer.id == ClickEvent.offer_id)
                .where(Offer.merchant_id == merchant_id, _in_range(start, end))
            )
        ).one()
        return MerchantClickTotals(clicks=row[0], bot_clicks=row[1])

    async def offers(
        self, merchant_id: str, start: datetime, end: datetime, limit: int
    ) -> OfferClicksPage:
        """Offers of the merchant, most clicked first, at most ``limit`` of them."""
        clicks = func.count(ClickEvent.id).filter(_counted())
        statement = (
            select(
                Offer.id,
                Offer.slug,
                Offer.title,
                clicks.label("clicks"),
                func.count().over().label("total_offers"),
            )
            .select_from(Offer)
            .outerjoin(
                ClickEvent,
                and_(ClickEvent.offer_id == Offer.id, _in_range(start, end)),
            )
            .where(Offer.merchant_id == merchant_id)
            .group_by(Offer.id)
            .having(or_(Offer.status.in_(LISTED_STATUSES), clicks > 0))
            .order_by(clicks.desc(), Offer.title, Offer.id)
            .limit(limit)
        )
        rows = (await self._session.execute(statement)).all()
        return OfferClicksPage(
            offers=[OfferClicks(r[0], r[1], r[2], r[3]) for r in rows],
            total_offers=rows[0][4] if rows else 0,
        )

    async def daily(
        self, offer_ids: list[str], start: datetime, end: datetime
    ) -> list[DayClicks]:
        """Counted clicks per offer per UTC day, only for days that had any."""
        if not offer_ids:
            return []
        day = func.date(func.timezone("UTC", ClickEvent.occurred_at)).label("day")
        rows = (
            await self._session.execute(
                select(ClickEvent.offer_id, day, func.count())
                .where(
                    ClickEvent.offer_id.in_(offer_ids),
                    _in_range(start, end),
                    _counted(),
                )
                .group_by(ClickEvent.offer_id, day)
                .order_by(ClickEvent.offer_id, day)
            )
        ).all()
        return [DayClicks(offer_id, d, n) for offer_id, d, n in rows]
