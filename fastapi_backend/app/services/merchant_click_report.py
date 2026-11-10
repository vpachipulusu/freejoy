"""The per-merchant click report: clicks per offer per UTC day over a date range.

Built for sending to a retailer who has no network report. A click is one visitor sent through a Freejoy
link to the destination: not a sale, not a unique person, and clicks the site classed as bots are left
out (see ``app.repositories.merchant_clicks``). Click rows are deleted after ``CLICK_RETENTION_DAYS``, so
the report says which day is the first one it can still cover completely.

Bounds: a range is at most ``MAX_REPORT_DAYS`` days, at most ``MAX_REPORT_OFFERS`` offers are returned
(busiest first, and the report says when more exist), and only days with clicks carry a row.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Protocol

from app.core.click_retention import CLICK_RETENTION_DAYS
from app.repositories.merchant_clicks import (
    DayClicks,
    MerchantClickTotals,
    MerchantRef,
    OfferClicksPage,
)
from app.services.admin_catalogue_errors import CatalogueNotFound

MAX_REPORT_DAYS = 366
MAX_REPORT_OFFERS = 100
# Dates outside this window are refused, which also keeps the exclusive end of the range representable.
MIN_REPORT_DATE = date(2000, 1, 1)
MAX_REPORT_DATE = date(2999, 12, 31)

RANGE_MESSAGE = (
    f"The range must start on or before its end, span at most {MAX_REPORT_DAYS} days "
    f"and lie between {MIN_REPORT_DATE.year} and {MAX_REPORT_DATE.year}"
)


class InvalidClickRange(Exception):
    """The requested dates are reversed, too long or out of bounds (422, code ``invalid_range``)."""


@dataclass(frozen=True)
class OfferDays:
    offer_id: str
    slug: str
    title: str
    total_clicks: int
    days: tuple[DayClicks, ...]


@dataclass(frozen=True)
class ClickReport:
    merchant: MerchantRef
    from_date: date
    to_date: date
    total_clicks: int
    excluded_bot_clicks: int
    offers_total: int
    max_offers: int
    offers: tuple[OfferDays, ...]
    retention_days: int
    # The first UTC day all of whose clicks are still kept.
    earliest_complete_date: date

    @property
    def offers_truncated(self) -> bool:
        return self.offers_total > len(self.offers)

    @property
    def starts_before_retention(self) -> bool:
        return self.from_date < self.earliest_complete_date


class ClickStore(Protocol):
    async def get_merchant(self, merchant_id: str) -> MerchantRef | None: ...
    async def totals(
        self, merchant_id: str, start: datetime, end: datetime
    ) -> MerchantClickTotals: ...
    async def offers(
        self, merchant_id: str, start: datetime, end: datetime, limit: int
    ) -> OfferClicksPage: ...
    async def daily(
        self, offer_ids: list[str], start: datetime, end: datetime
    ) -> list[DayClicks]: ...


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _midnight(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=UTC)


def _validate(date_from: date, date_to: date) -> None:
    days = (date_to - date_from).days + 1
    if not (
        MIN_REPORT_DATE <= date_from <= date_to <= MAX_REPORT_DATE
        and days <= MAX_REPORT_DAYS
    ):
        raise InvalidClickRange(RANGE_MESSAGE)


class MerchantClickReportService:
    def __init__(
        self, store: ClickStore, clock: Callable[[], datetime] = _utc_now
    ) -> None:
        self.store, self.clock = store, clock

    def _earliest_complete_date(self) -> date:
        cutoff = self.clock() - timedelta(days=CLICK_RETENTION_DAYS)
        # The job deletes rows older than the cutoff, so the day holding the cutoff is whole only
        # when the cutoff falls exactly on midnight.
        if cutoff == _midnight(cutoff.date()):
            return cutoff.date()
        return cutoff.date() + timedelta(days=1)

    async def report(
        self, merchant_id: str, date_from: date, date_to: date
    ) -> ClickReport:
        _validate(date_from, date_to)
        merchant = await self.store.get_merchant(merchant_id)
        if merchant is None:
            raise CatalogueNotFound("merchant")
        start, end = _midnight(date_from), _midnight(date_to + timedelta(days=1))
        totals = await self.store.totals(merchant.id, start, end)
        page = await self.store.offers(merchant.id, start, end, MAX_REPORT_OFFERS)
        days = await self.store.daily([o.offer_id for o in page.offers], start, end)
        by_offer: dict[str, list[DayClicks]] = {}
        for item in days:
            by_offer.setdefault(item.offer_id, []).append(item)
        offers = tuple(
            OfferDays(
                o.offer_id,
                o.slug,
                o.title,
                sum(d.clicks for d in by_offer.get(o.offer_id, [])),
                tuple(by_offer.get(o.offer_id, [])),
            )
            for o in page.offers
        )
        return ClickReport(
            merchant=merchant,
            from_date=date_from,
            to_date=date_to,
            total_clicks=totals.clicks,
            excluded_bot_clicks=totals.bot_clicks,
            offers_total=page.total_offers,
            max_offers=MAX_REPORT_OFFERS,
            offers=offers,
            retention_days=CLICK_RETENTION_DAYS,
            earliest_complete_date=self._earliest_complete_date(),
        )
