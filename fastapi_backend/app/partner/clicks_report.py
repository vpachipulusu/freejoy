"""The partner click report: clicks per offer per UTC day over a bounded range, for one merchant (FREEJ-59).

The rules, in one place and without HTTP:

* ``from`` and ``to`` are UTC calendar days, both included, ``from <= to``, at most ``MAX_RANGE_DAYS`` days and
  between the years 2000 and 2999 (which also keeps the exclusive end of the range representable);
* at most ``MAX_OFFERS`` offers are returned, busiest first (then title, then internal id), and the report says when
  more exist; only days that had a click carry a row, so the largest answer is ``MAX_OFFERS * MAX_RANGE_DAYS`` rows;
* click rows are deleted after ``CLICK_RETENTION_DAYS``, so the report says which day is the first it can still
  cover completely and whether the range starts before it.

What a click is, and what is left out, is the fixed text ``CLICK_DEFINITION``. The merchant comes from the caller's
key (``PartnerContext``), is passed in by the route, and is never a request parameter.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Protocol

from app.core.click_retention import CLICK_RETENTION_DAYS, earliest_complete_date
from app.core.rate_limit import OUTBOUND_OFFER_POLICY
from app.partner.clicks_store import (
    PartnerDayClicks,
    PartnerOffer,
    PartnerOfferPage,
)

MAX_RANGE_DAYS = 92
MAX_OFFERS = 100
MIN_DATE = date(2000, 1, 1)
MAX_DATE = date(2999, 12, 31)

# One fixed text for every caller. The cap is read from the policy the click endpoint enforces, so the sentence
# cannot say a number the code no longer uses (the committed partner schema carries this text, and its freshness
# check fails when it changes).
CLICK_DEFINITION = (
    "A click is one visitor sent from Freejoy to the offer's destination. It is not a sale, a sign-up or a claim, "
    "and it does not count different people: a person who clicks twice counts twice. Clicks from visitors "
    "identified as automated are left out and nothing else is filtered. Days are UTC days. Recording is "
    f"limited to {OUTBOUND_OFFER_POLICY.limit} clicks for one offer in a minute: past that a visitor is still "
    "sent on but the click is not recorded, so a very busy minute is undercounted. Click records are deleted "
    f"after {CLICK_RETENTION_DAYS} days, so an earlier day can be incomplete. Counts only: Freejoy keeps no "
    "visitor identity."
)


class InvalidClickRange(Exception):
    """The requested dates are reversed, too long or out of bounds (422, code ``invalid_range``)."""


@dataclass(frozen=True)
class OfferDays:
    slug: str
    title: str
    total_clicks: int
    days: tuple[PartnerDayClicks, ...]


@dataclass(frozen=True)
class PartnerClickReport:
    from_date: date
    to_date: date
    offers: tuple[OfferDays, ...]
    # Listed offers of the merchant, before the limit: the report only says whether it cut some.
    offers_truncated: bool
    earliest_complete_date: date

    @property
    def starts_before_retention(self) -> bool:
        return self.from_date < self.earliest_complete_date


class PartnerClickReader(Protocol):
    async def offers(
        self, merchant_id: str, start: datetime, end: datetime, limit: int
    ) -> PartnerOfferPage: ...
    async def daily(
        self,
        merchant_id: str,
        offer_ids: list[str],
        start: datetime,
        end: datetime,
    ) -> list[PartnerDayClicks]: ...


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _midnight(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=UTC)


def validate_range(date_from: date, date_to: date) -> None:
    days = (date_to - date_from).days + 1
    if not (MIN_DATE <= date_from <= date_to <= MAX_DATE and days <= MAX_RANGE_DAYS):
        raise InvalidClickRange


def _group_days(days: list[PartnerDayClicks]) -> dict[str, list[PartnerDayClicks]]:
    by_offer: dict[str, list[PartnerDayClicks]] = {}
    for item in days:
        by_offer.setdefault(item.offer_id, []).append(item)
    return by_offer


def _offer_days(
    offer: PartnerOffer, by_offer: dict[str, list[PartnerDayClicks]]
) -> OfferDays:
    days = tuple(by_offer.get(offer.offer_id, []))
    return OfferDays(offer.slug, offer.title, sum(d.clicks for d in days), days)


class PartnerClickReportService:
    def __init__(
        self, store: PartnerClickReader, clock: Callable[[], datetime] = _utc_now
    ) -> None:
        self._store, self._clock = store, clock

    async def report(
        self, merchant_id: str, date_from: date, date_to: date
    ) -> PartnerClickReport:
        validate_range(date_from, date_to)
        start, end = _midnight(date_from), _midnight(date_to + timedelta(days=1))
        page = await self._store.offers(merchant_id, start, end, MAX_OFFERS)
        days = await self._store.daily(
            merchant_id, [o.offer_id for o in page.offers], start, end
        )
        by_offer = _group_days(days)
        return PartnerClickReport(
            from_date=date_from,
            to_date=date_to,
            offers=tuple(_offer_days(o, by_offer) for o in page.offers),
            offers_truncated=page.total_offers > len(page.offers),
            earliest_complete_date=earliest_complete_date(self._clock()),
        )
