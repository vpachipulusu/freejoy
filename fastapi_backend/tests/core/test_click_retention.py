"""The first UTC day all of whose click rows are still kept (shared by the admin report and the partner API)."""

from datetime import UTC, date, datetime, timedelta, timezone

from app.core.click_retention import CLICK_RETENTION_DAYS, earliest_complete_date

MIDNIGHT = datetime(2026, 6, 1, tzinfo=UTC)


def test_a_cutoff_exactly_on_midnight_gives_that_day() -> None:
    now = MIDNIGHT + timedelta(days=CLICK_RETENTION_DAYS)

    assert earliest_complete_date(now) == date(2026, 6, 1)


def test_a_cutoff_one_minute_after_midnight_gives_the_next_day() -> None:
    now = MIDNIGHT + timedelta(days=CLICK_RETENTION_DAYS, minutes=1)

    assert earliest_complete_date(now) == date(2026, 6, 2)


def test_a_cutoff_one_minute_before_midnight_gives_the_day_after_it() -> None:
    now = MIDNIGHT + timedelta(days=CLICK_RETENTION_DAYS, minutes=-1)

    assert earliest_complete_date(now) == date(2026, 6, 1)


def test_an_aware_clock_in_another_zone_gives_the_same_day_as_its_utc_equivalent() -> (
    None
):
    utc_now = datetime(2027, 7, 4, 23, 30, tzinfo=UTC)
    plus_two = utc_now.astimezone(timezone(timedelta(hours=2)))

    assert plus_two.date() != utc_now.date()  # the zones disagree about the date
    assert earliest_complete_date(plus_two) == earliest_complete_date(utc_now)
