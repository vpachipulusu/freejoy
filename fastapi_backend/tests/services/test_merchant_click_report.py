"""The merchant click report service: range rules, bounds, caps, retention and report shape."""

from datetime import UTC, date, datetime, timedelta

import pytest

from app.core.click_retention import CLICK_RETENTION_DAYS
from app.repositories.merchant_clicks import (
    DayClicks,
    MerchantClickTotals,
    MerchantRef,
    OfferClicks,
    OfferClicksPage,
)
from app.services.admin_catalogue_errors import CatalogueNotFound
from app.services.merchant_click_report import (
    MAX_REPORT_DAYS,
    MAX_REPORT_OFFERS,
    InvalidClickRange,
    MerchantClickReportService,
)

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)
MERCHANT = MerchantRef("m1", "Acme", "acme")


class FakeStore:
    def __init__(
        self,
        merchant: MerchantRef | None = MERCHANT,
        offers: list[OfferClicks] | None = None,
        total_offers: int | None = None,
        days: list[DayClicks] | None = None,
        totals: MerchantClickTotals | None = None,
    ) -> None:
        self.merchant = merchant
        self.offer_rows = offers or []
        self.total_offers = (
            len(self.offer_rows) if total_offers is None else total_offers
        )
        self.day_rows = days or []
        self.totals_value = totals or MerchantClickTotals(0, 0)
        self.calls: list[tuple] = []

    async def get_merchant(self, merchant_id: str) -> MerchantRef | None:
        self.calls.append(("merchant", merchant_id))
        return self.merchant

    async def totals(self, merchant_id, start, end):  # type: ignore[no-untyped-def]
        self.calls.append(("totals", merchant_id, start, end))
        return self.totals_value

    async def offers(self, merchant_id, start, end, limit):  # type: ignore[no-untyped-def]
        self.calls.append(("offers", merchant_id, start, end, limit))
        return OfferClicksPage(self.offer_rows, self.total_offers)

    async def daily(self, offer_ids, start, end):  # type: ignore[no-untyped-def]
        self.calls.append(("daily", list(offer_ids), start, end))
        return self.day_rows


def service(store: FakeStore) -> MerchantClickReportService:
    return MerchantClickReportService(store, clock=lambda: NOW)


async def test_the_range_is_whole_utc_days_with_an_exclusive_end():
    store = FakeStore()

    await service(store).report("m1", date(2026, 3, 10), date(2026, 3, 11))

    start, end = store.calls[2][2:4]
    assert start == datetime(2026, 3, 10, 0, 0, tzinfo=UTC)
    assert end == datetime(2026, 3, 12, 0, 0, tzinfo=UTC)


async def test_offer_totals_are_the_sum_of_their_days_and_days_are_attached():
    store = FakeStore(
        offers=[
            OfferClicks("o1", "soap", "Soap", 3),
            OfferClicks("o2", "balm", "Balm", 0),
        ],
        days=[
            DayClicks("o1", date(2026, 3, 10), 2),
            DayClicks("o1", date(2026, 3, 11), 1),
        ],
        totals=MerchantClickTotals(3, 4),
    )

    report = await service(store).report("m1", date(2026, 3, 10), date(2026, 3, 11))

    assert [(o.slug, o.total_clicks) for o in report.offers] == [
        ("soap", 3),
        ("balm", 0),
    ]
    assert [(d.day, d.clicks) for d in report.offers[0].days] == [
        (date(2026, 3, 10), 2),
        (date(2026, 3, 11), 1),
    ]
    assert report.offers[1].days == ()
    assert (report.total_clicks, report.excluded_bot_clicks) == (3, 4)
    assert report.merchant == MERCHANT


async def test_the_offer_limit_is_passed_down_and_the_cut_is_reported():
    store = FakeStore(
        offers=[OfferClicks("o1", "soap", "Soap", 1)],
        total_offers=MAX_REPORT_OFFERS + 5,
    )

    report = await service(store).report("m1", date(2026, 3, 10), date(2026, 3, 11))

    assert store.calls[2][4] == MAX_REPORT_OFFERS
    assert report.offers_total == MAX_REPORT_OFFERS + 5
    assert report.offers_truncated is True
    assert report.max_offers == MAX_REPORT_OFFERS


async def test_nothing_is_cut_when_every_offer_fits():
    store = FakeStore(offers=[OfferClicks("o1", "soap", "Soap", 1)])

    report = await service(store).report("m1", date(2026, 3, 10), date(2026, 3, 11))

    assert report.offers_truncated is False


async def test_an_unknown_merchant_is_not_found_and_nothing_else_is_queried():
    store = FakeStore(merchant=None)

    with pytest.raises(CatalogueNotFound):
        await service(store).report("nope", date(2026, 3, 10), date(2026, 3, 11))

    assert store.calls == [("merchant", "nope")]


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (date(2026, 3, 11), date(2026, 3, 10)),
        (date(2025, 1, 1), date(2026, 1, 2)),
        (date(1999, 12, 31), date(2000, 1, 2)),
        (date(2999, 12, 31), date(3000, 1, 1)),
    ],
)
async def test_a_bad_range_is_refused_before_any_query(start, end):
    store = FakeStore()

    with pytest.raises(InvalidClickRange):
        await service(store).report("m1", start, end)

    assert store.calls == []


async def test_a_range_of_exactly_366_days_is_accepted_and_367_is_not():
    start = date(2025, 1, 1)

    await service(FakeStore()).report(
        "m1", start, start + timedelta(days=MAX_REPORT_DAYS - 1)
    )
    with pytest.raises(InvalidClickRange):
        await service(FakeStore()).report(
            "m1", start, start + timedelta(days=MAX_REPORT_DAYS)
        )


async def test_a_single_day_range_is_accepted():
    report = await service(FakeStore()).report(
        "m1", date(2026, 3, 10), date(2026, 3, 10)
    )

    assert (report.from_date, report.to_date) == (date(2026, 3, 10), date(2026, 3, 10))


async def test_retention_reports_the_first_fully_kept_day():
    report = await service(FakeStore()).report(
        "m1", date(2026, 10, 1), date(2026, 10, 2)
    )

    cutoff = NOW - timedelta(days=CLICK_RETENTION_DAYS)
    assert report.retention_days == CLICK_RETENTION_DAYS
    assert report.earliest_complete_date == cutoff.date() + timedelta(days=1)
    assert report.starts_before_retention is False


async def test_a_range_that_starts_before_the_first_kept_day_is_flagged():
    earliest = NOW - timedelta(days=CLICK_RETENTION_DAYS)

    report = await service(FakeStore()).report(
        "m1", earliest.date(), earliest.date() + timedelta(days=3)
    )

    assert report.starts_before_retention is True
    exact = await service(FakeStore()).report(
        "m1", report.earliest_complete_date, report.earliest_complete_date
    )
    assert exact.starts_before_retention is False


async def test_a_cutoff_exactly_at_midnight_keeps_that_whole_day():
    midnight = datetime(2026, 10, 17, 0, 0, tzinfo=UTC)
    svc = MerchantClickReportService(FakeStore(), clock=lambda: midnight)

    report = await svc.report("m1", date(2026, 10, 1), date(2026, 10, 2))

    assert (
        report.earliest_complete_date
        == (midnight - timedelta(days=CLICK_RETENTION_DAYS)).date()
    )
