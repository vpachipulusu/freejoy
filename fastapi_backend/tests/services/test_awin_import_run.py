"""``run_import``: paging, limits, validation counts, batching and what a failure leaves behind."""

import logging
from datetime import date
from typing import Any

import pytest

import app.services.awin_import_run as module
from app.core.awin_client import AwinError, AwinReason
from app.services.awin_import_run import (
    ImportFailed,
    RunOptions,
    RunReport,
    run_import,
)
from tests.awin_samples import CODE, NOW, raw_item, raw_page
from tests.fakes_awin import FakeAwinClient
from tests.fakes_awin_import import FakeDatabase


@pytest.fixture
def small_pages(monkeypatch: pytest.MonkeyPatch) -> int:
    """Pages of 5 and batches of 3, so a handful of items exercises paging and batching."""
    monkeypatch.setattr(module, "MIN_PAGE_SIZE", 1)
    monkeypatch.setattr(module, "MAX_PAGE_SIZE", 5)
    monkeypatch.setattr(module, "BATCH_SIZE", 3)
    return 5


async def go(
    client: FakeAwinClient, db: FakeDatabase | None = None, **options: Any
) -> RunReport:
    database = db or FakeDatabase()
    return await run_import(client, database.unit, RunOptions(**options), NOW)


async def failing(
    client: FakeAwinClient, db: FakeDatabase | None = None, **options: Any
) -> ImportFailed:
    with pytest.raises(ImportFailed) as caught:
        await go(client, db, **options)
    return caught.value


# --- paging --------------------------------------------------------------------------------


async def test_pages_are_read_until_a_short_page(small_pages: int) -> None:
    db = FakeDatabase()
    client = FakeAwinClient([raw_page(1, 5), raw_page(6, 5), raw_page(11, 2)])

    report = await go(client, db, limit=100)

    assert [page for _, page, _ in client.requests] == [1, 2, 3]
    assert {size for _, _, size in client.requests} == {small_pages}
    assert (report.pages, report.received, report.valid) == (3, 12, 12)
    assert report.tally.created == 12 and report.stopped_early is None
    assert len(db.committed.offers) == 12


async def test_an_empty_first_page_is_a_finished_run() -> None:
    client = FakeAwinClient([[]])

    report = await go(client)

    assert report.pages == 1 and report.received == 0 and report.tally.processed == 0
    assert report.stopped_early is None


async def test_the_total_pages_in_the_response_stops_the_run(small_pages: int) -> None:
    client = FakeAwinClient(
        [raw_page(1, 5), raw_page(6, 5), raw_page(11, 5)], total_pages=2
    )

    report = await go(client, limit=100)

    assert len(client.requests) == 2 and report.pages == 2
    assert report.stopped_early is None


async def test_the_limit_stops_the_run_and_says_more_may_exist(
    small_pages: int,
) -> None:
    db = FakeDatabase()
    client = FakeAwinClient([raw_page(1, 5), raw_page(6, 5), raw_page(11, 5)])

    report = await go(client, db, limit=7)

    assert len(client.requests) == 2
    assert report.received == 7 and report.stopped_early == "limit"
    assert len(db.committed.offers) == 7


async def test_max_pages_stops_the_run_when_the_last_page_was_full(
    small_pages: int,
) -> None:
    client = FakeAwinClient([raw_page(1, 5), raw_page(6, 5), raw_page(11, 5)])

    report = await go(client, limit=100, max_pages=2)

    assert len(client.requests) == 2 and report.stopped_early == "max_pages"


async def test_a_run_that_ends_exactly_on_the_last_page_does_not_claim_more_exist(
    small_pages: int,
) -> None:
    client = FakeAwinClient([raw_page(1, 5), raw_page(6, 3)])

    report = await go(client, limit=100, max_pages=2)

    assert report.stopped_early is None


@pytest.mark.parametrize(("limit", "size"), [(5, 10), (100, 100), (5000, 200)])
async def test_the_page_size_follows_the_limit_within_awins_range(
    limit: int, size: int
) -> None:
    client = FakeAwinClient([[]])

    await go(client, limit=limit)

    assert client.requests[0][2] == size


async def test_the_filters_carry_the_updated_since_date() -> None:
    client = FakeAwinClient([[]])

    await go(client, updated_since=date(2026, 11, 1))

    assert client.requests[0][0].updated_since == date(2026, 11, 1)


async def test_a_page_that_repeats_the_earlier_ones_is_not_trusted(
    small_pages: int,
) -> None:
    db = FakeDatabase()
    client = FakeAwinClient([raw_page(1, 5), raw_page(1, 5)])

    failure = await failing(client, db, limit=100)

    assert failure.cause == "pagination_repeat"
    assert failure.report.tally.created == 5 and len(db.committed.offers) == 5


# --- validation counts ---------------------------------------------------------------------


async def test_rejected_items_are_counted_by_reason_and_the_rest_are_imported() -> None:
    db = FakeDatabase()
    items = [
        raw_item(1),
        raw_item(2, advertiser={"id": 9, "name": "Other", "joined": False}),
        raw_item(3, urlTracking="https://evil.example/x"),
        raw_item(4, type="cashback"),
        "not an object",
        raw_item(5),
    ]

    report = await go(FakeAwinClient([items]), db, limit=100)

    assert report.tally.created == 2 and report.valid == 2
    assert report.tally.skipped == {
        "not_joined": 1,
        "tracking_host_not_allowed": 1,
        "unsupported_type": 1,
        "invalid_item": 1,
    }
    assert report.received == 6 == report.tally.processed


async def test_a_promotion_listed_twice_is_imported_once() -> None:
    db = FakeDatabase()

    report = await go(FakeAwinClient([[raw_item(1), raw_item(1), raw_item(2)]]), db)

    assert report.tally.created == 2
    assert report.tally.skipped == {"duplicate_in_feed": 1}


async def test_a_skipped_item_is_logged_as_an_id_and_a_reason_only(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    bad = raw_item(
        42, title="SECRET HEADLINE", urlTracking="https://evil.example/?token=abc"
    )

    await go(FakeAwinClient([[bad, "x"]]))

    assert "promotion_id=42 reason=tracking_host_not_allowed" in caplog.text
    assert "promotion_id=- reason=invalid_item" in caplog.text
    for secret in ("SECRET HEADLINE", "evil.example", "token=abc", CODE):
        assert secret not in caplog.text


async def test_skip_logging_is_capped(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(module, "MAX_LOGGED_SKIPS", 3)
    caplog.set_level(logging.WARNING)
    items = [raw_item(n, type="cashback") for n in range(1, 11)]

    report = await go(FakeAwinClient([items]))

    assert caplog.text.count("awin item skipped") == 3
    assert report.tally.skipped == {"unsupported_type": 10}


# --- batching and failure ------------------------------------------------------------------


async def test_valid_items_are_written_in_committed_batches() -> None:
    db = FakeDatabase()
    client = FakeAwinClient([raw_page(1, 120)])

    report = await go(client, db, limit=200)

    assert report.tally.created == 120
    assert db.units_opened == 3 and db.commits == 3  # 50 + 50 + 20


async def test_a_database_failure_keeps_earlier_batches_and_reports_the_count(
    small_pages: int,
) -> None:
    db = FakeDatabase(fail_insert_offer_at=7)
    client = FakeAwinClient([raw_page(1, 5), raw_page(6, 5)])

    failure = await failing(client, db, limit=100)

    assert failure.cause == "RuntimeError" and failure.stage == module.STAGE_WRITE
    # page 1 is committed as batches of 3 and 2; the batch holding offers 6 to 8 fails at the 7th
    assert failure.report.tally.created == 5
    assert len(db.committed.offers) == 5 and db.commits == 2
    assert failure.__cause__ is None
    assert "simulated" not in str(failure)


async def test_a_run_after_a_failure_carries_on_without_duplicating(
    small_pages: int,
) -> None:
    db = FakeDatabase(fail_insert_offer_at=4)
    pages = [raw_page(1, 5), raw_page(6, 2)]
    await failing(FakeAwinClient(pages), db, limit=100)
    db.fail_insert_offer_at = None

    report = await go(FakeAwinClient(pages), db, limit=100)

    assert report.tally.created == 4 and report.tally.unchanged == 3
    assert len(db.committed.offers) == 7


async def test_a_failed_fetch_keeps_what_the_earlier_pages_wrote(
    small_pages: int,
) -> None:
    db = FakeDatabase()
    client = FakeAwinClient([raw_page(1, 5), AwinError(AwinReason.RATE_LIMITED)])

    failure = await failing(client, db, limit=100)

    assert failure.cause == "rate_limited" and failure.stage == module.STAGE_FETCH
    assert failure.report.tally.created == 5 and failure.report.pages == 1
    assert len(db.committed.offers) == 5


async def test_an_authentication_failure_on_the_first_call_has_done_nothing() -> None:
    db = FakeDatabase()

    failure = await failing(FakeAwinClient([AwinError(AwinReason.AUTH_FAILED)]), db)

    assert failure.cause == "auth_failed"
    assert failure.report == RunReport() and db.units_opened == 0


# --- dry run -------------------------------------------------------------------------------


async def test_a_dry_run_fetches_and_validates_but_opens_no_database_unit() -> None:
    db = FakeDatabase()
    items = [raw_item(1), raw_item(2, type="cashback"), raw_item(3)]

    report = await go(FakeAwinClient([items]), db, dry_run=True)

    assert (report.received, report.valid) == (3, 2)
    assert report.tally.created == 0 and report.tally.skipped == {"unsupported_type": 1}
    assert db.units_opened == 0 and db.committed.offers == {}


async def test_a_dry_run_needs_no_database_at_all() -> None:
    client = FakeAwinClient([[raw_item(1)]])

    report = await run_import(client, None, RunOptions(dry_run=True), NOW)

    assert report.valid == 1
