"""The partner click report rules without HTTP or a database: the range, the cap, the retention values (FREEJ-59)."""

from datetime import UTC, date, datetime, timedelta

import pytest

from app.core.click_retention import CLICK_RETENTION_DAYS
from app.partner.clicks_report import (
    MAX_DATE,
    MAX_OFFERS,
    MAX_RANGE_DAYS,
    MIN_DATE,
    InvalidClickRange,
    PartnerClickReportService,
    validate_range,
)
from tests.fakes_partner import FakeClickStore

MERCHANT = "merchant000000000000000a1"
NOW = datetime(2026, 11, 11, 15, 30, tzinfo=UTC)


def service(store: FakeClickStore) -> PartnerClickReportService:
    return PartnerClickReportService(store, clock=lambda: NOW)


@pytest.mark.parametrize(
    ("first", "last"),
    [
        (date(2026, 3, 1), date(2026, 3, 1)),
        (date(2026, 1, 1), date(2026, 1, 1) + timedelta(days=MAX_RANGE_DAYS - 1)),
        (MIN_DATE, MIN_DATE + timedelta(days=1)),
        (MAX_DATE - timedelta(days=1), MAX_DATE),
    ],
)
def test_ranges_inside_the_rules_are_accepted(first: date, last: date) -> None:
    validate_range(first, last)


@pytest.mark.parametrize(
    ("first", "last"),
    [
        (date(2026, 3, 2), date(2026, 3, 1)),
        (date(2026, 1, 1), date(2026, 1, 1) + timedelta(days=MAX_RANGE_DAYS)),
        (date(1999, 12, 31), date(2000, 1, 1)),
        (MAX_DATE, MAX_DATE + timedelta(days=1)),
    ],
)
def test_ranges_outside_the_rules_are_refused(first: date, last: date) -> None:
    with pytest.raises(InvalidClickRange):
        validate_range(first, last)


async def test_the_retention_values_come_from_the_injected_clock() -> None:
    report = await service(FakeClickStore()).report(
        MERCHANT, date(2026, 11, 1), date(2026, 11, 10)
    )

    cutoff = NOW - timedelta(days=CLICK_RETENTION_DAYS)
    assert report.earliest_complete_date == cutoff.date() + timedelta(days=1)
    assert report.starts_before_retention is False


async def test_a_range_starting_on_the_earliest_complete_day_is_not_before_retention() -> (
    None
):
    store = FakeClickStore()
    earliest = (
        await service(store).report(MERCHANT, date(2026, 11, 1), date(2026, 11, 2))
    ).earliest_complete_date

    on = await service(store).report(MERCHANT, earliest, earliest + timedelta(days=5))
    before = await service(store).report(
        MERCHANT, earliest - timedelta(days=1), earliest + timedelta(days=5)
    )

    assert on.starts_before_retention is False
    assert before.starts_before_retention is True


async def test_the_offers_are_cut_at_the_cap_and_the_cut_is_reported() -> None:
    store = FakeClickStore()
    for number in range(MAX_OFFERS + 1):
        store.add(MERCHANT, f"offer-{number:03d}", {date(2026, 11, 2): number})

    report = await service(store).report(MERCHANT, date(2026, 11, 1), date(2026, 11, 3))

    assert len(report.offers) == MAX_OFFERS
    assert report.offers_truncated is True
    assert report.offers[0].total_clicks == MAX_OFFERS
    assert store.calls[0][4] == MAX_OFFERS


async def test_an_offer_total_is_the_sum_of_its_days_and_offers_with_none_have_no_days() -> (
    None
):
    store = FakeClickStore()
    store.add(MERCHANT, "busy", {date(2026, 11, 2): 2, date(2026, 11, 3): 5})
    store.add(MERCHANT, "idle")

    report = await service(store).report(MERCHANT, date(2026, 11, 1), date(2026, 11, 5))

    assert [(o.slug, o.total_clicks, len(o.days)) for o in report.offers] == [
        ("busy", 7, 2),
        ("idle", 0, 0),
    ]
    assert report.offers_truncated is False
