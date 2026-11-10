"""``run_report_import``: windows, paging, limits, failure accounting, the audit row, the dry run."""

import asyncio
import itertools
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from app.core.awin_client import AwinError, AwinReason, TransactionsPage
from app.services.network_report_run import (
    PAGE_SIZE,
    ReportRunFailed,
    RunOptions,
    RunReport,
    report_windows,
    run_report_import,
)
from tests.awin_report_samples import NOW, raw_row, raw_rows
from tests.fakes_awin import FakeAwinClient
from tests.fakes_network_reports import FakeDriverError, FakeLink, FakeReportDatabase

DAY = date(2026, 11, 3)


def options(**changes: Any) -> RunOptions:
    values: dict[str, Any] = {"start": DAY, "end": DAY}
    return RunOptions(**{**values, **changes})


async def run(
    pages: list[Any],
    database: FakeReportDatabase | None = None,
    **changes: Any,
) -> RunReport:
    database = database or FakeReportDatabase()
    client = FakeAwinClient(transaction_pages=pages)
    return await run_report_import(client, database.unit, options(**changes), NOW)


async def failure(
    client: Any, database: FakeReportDatabase | None, **changes: Any
) -> ReportRunFailed:
    units = database.unit if database else None
    with pytest.raises(ReportRunFailed) as caught:
        await run_report_import(client, units, options(**changes), NOW)
    return caught.value


# --- the windows ---------------------------------------------------------------------------


def test_one_day_is_one_window_from_midnight_to_the_last_second() -> None:
    assert report_windows(DAY, DAY) == [
        (
            datetime(2026, 11, 3, tzinfo=UTC),
            datetime(2026, 11, 3, 23, 59, 59, tzinfo=UTC),
        )
    ]


def test_thirty_one_days_inclusive_is_one_window_within_awins_limit() -> None:
    windows = report_windows(date(2026, 10, 1), date(2026, 10, 31))

    assert windows == [
        (
            datetime(2026, 10, 1, tzinfo=UTC),
            datetime(2026, 10, 31, 23, 59, 59, tzinfo=UTC),
        )
    ]
    assert windows[0][1] - windows[0][0] <= timedelta(days=31)


def test_a_longer_range_is_cut_into_consecutive_windows_with_no_gap_or_overlap() -> (
    None
):
    windows = report_windows(date(2026, 9, 1), date(2026, 11, 10))  # 71 days

    assert len(windows) == 3
    assert windows[0][0] == datetime(2026, 9, 1, tzinfo=UTC)
    assert windows[-1][1] == datetime(2026, 11, 10, 23, 59, 59, tzinfo=UTC)
    for (_, previous_end), (next_start, _) in itertools.pairwise(windows):
        assert next_start - previous_end == timedelta(seconds=1)
    assert all(end - start <= timedelta(days=31) for start, end in windows)


# --- a run ---------------------------------------------------------------------------------


async def test_a_run_writes_the_valid_rows_counts_the_rest_and_writes_one_audit_row() -> (
    None
):
    database = FakeReportDatabase()
    database.add_link(FakeLink("l1", "o1", "https://x.example", "ref-1"))
    page = [
        raw_row(1),
        raw_row(2, commissionStatus="refunded"),
        raw_row(3, clickRefs=None),
        "garbage",
        raw_row(1),  # the same id twice
    ]

    report = await run([page], database)

    assert (report.received, report.valid, report.pages) == (5, 2, 1)
    assert (report.tally.inserted, report.tally.unmatched) == (2, 1)
    assert dict(report.tally.skipped) == {
        "unknown_status": 1,
        "invalid_item": 1,
        "duplicate_in_feed": 1,
    }
    assert len(database.committed.rows) == 2
    (audit,) = database.committed.audits
    assert audit["source"] == "commands.import_awin_reports"
    assert audit["action"] == "admin.reports.imported"
    assert audit["resource"] == "network_report_rows" and audit["resource_id"] is None
    assert audit["details"]["status"] == "completed"
    assert audit["details"]["inserted"] == 2 and audit["details"]["unmatched"] == 1
    assert audit["details"]["skipped"] == {
        "unknown_status": 1,
        "invalid_item": 1,
        "duplicate_in_feed": 1,
    }


async def test_the_audit_row_holds_counts_and_dates_only() -> None:
    database = FakeReportDatabase()

    await run([raw_rows(1, 3)], database)

    (audit,) = database.committed.audits
    details = audit["details"]
    assert set(details) == {
        "status",
        "from",
        "to",
        "pages",
        "fetched",
        "valid",
        "inserted",
        "updated",
        "unchanged",
        "unmatched",
        "skipped",
        "stopped_early",
    }
    assert details["from"] == "2026-11-03" and details["to"] == "2026-11-03"
    text = repr(audit)
    for fragment in ("ref-", "GBP", "5001", "approved"):
        assert fragment not in text


async def test_a_second_run_over_the_same_report_changes_no_row_and_counts_unchanged() -> (
    None
):
    database = FakeReportDatabase()
    await run([raw_rows(1, 3)], database)
    before = {key: dict(value) for key, value in database.committed.rows.items()}

    report = await run([raw_rows(1, 3)], database)

    assert (report.tally.inserted, report.tally.updated, report.tally.unchanged) == (
        0,
        0,
        3,
    )
    after = database.committed.rows
    assert {k: v["report_hash"] for k, v in after.items()} == {
        k: v["report_hash"] for k, v in before.items()
    }
    assert len(database.committed.audits) == 2  # one per run


async def test_an_empty_report_is_a_completed_run_with_an_audit_row() -> None:
    database = FakeReportDatabase()

    report = await run([[]], database)

    assert report.received == 0 and report.stopped_early is None
    assert len(database.committed.audits) == 1 and database.committed.rows == {}


# --- paging --------------------------------------------------------------------------------


async def test_a_full_page_asks_for_the_next_and_a_short_page_ends_the_run() -> None:
    database = FakeReportDatabase()
    client = FakeAwinClient(
        transaction_pages=[raw_rows(1, PAGE_SIZE), raw_rows(1000, 3)]
    )

    report = await run_report_import(client, database.unit, options(), NOW)

    assert report.pages == 2 and report.received == PAGE_SIZE + 3
    assert [request[2] for request in client.transaction_requests] == [1, 2]
    assert {request[3] for request in client.transaction_requests} == {PAGE_SIZE}
    assert database.commits == 3  # two pages and the audit row


async def test_the_total_the_response_gives_is_believed_over_a_short_page() -> None:
    client = FakeAwinClient(
        transaction_pages=[raw_rows(1, 3), raw_rows(10, 3)], total_pages=2
    )

    report = await run_report_import(client, FakeReportDatabase().unit, options(), NOW)

    assert report.pages == 2 and len(client.transaction_requests) == 2


async def test_max_pages_stops_the_run_and_says_more_may_exist() -> None:
    client = FakeAwinClient(
        transaction_pages=[raw_rows(n * 1000, PAGE_SIZE) for n in range(1, 4)]
    )

    report = await run_report_import(
        client, FakeReportDatabase().unit, options(max_pages=2), NOW
    )

    assert report.pages == 2 and report.stopped_early == "max_pages"
    assert len(client.transaction_requests) == 2


async def test_max_pages_is_not_reported_when_the_last_page_was_short() -> None:
    client = FakeAwinClient(transaction_pages=[raw_rows(1, 5)])

    report = await run_report_import(
        client, FakeReportDatabase().unit, options(max_pages=1), NOW
    )

    assert report.stopped_early is None


async def test_every_window_is_fetched_and_max_pages_counts_across_them() -> None:
    client = FakeAwinClient(transaction_pages=[raw_rows(1, 2), raw_rows(10, 2)])
    wide = {"start": date(2026, 9, 1), "end": date(2026, 10, 15)}  # two windows

    report = await run_report_import(
        client, FakeReportDatabase().unit, options(**wide), NOW
    )

    starts = [request[0] for request in client.transaction_requests]
    assert starts == [
        datetime(2026, 9, 1, tzinfo=UTC),
        datetime(2026, 10, 2, tzinfo=UTC),
    ]
    assert report.pages == 2 and report.tally.inserted == 4

    capped = FakeAwinClient(transaction_pages=[raw_rows(1, 2), raw_rows(10, 2)])
    stopped = await run_report_import(
        capped, FakeReportDatabase().unit, options(max_pages=1, **wide), NOW
    )
    assert stopped.pages == 1 and stopped.stopped_early == "max_pages"


async def test_a_page_of_ids_all_seen_before_fails_the_run() -> None:
    client = FakeAwinClient(
        transaction_pages=[raw_rows(1, PAGE_SIZE), raw_rows(1, PAGE_SIZE)]
    )

    stopped = await failure(client, FakeReportDatabase())

    assert stopped.cause == "pagination_repeat"
    assert stopped.report.pages == 1 and stopped.report.tally.inserted == PAGE_SIZE


# --- the dry run ---------------------------------------------------------------------------


async def test_a_dry_run_fetches_and_validates_and_writes_nothing() -> None:
    database = FakeReportDatabase()
    client = FakeAwinClient(
        transaction_pages=[[raw_row(1), raw_row(2, commissionStatus="refunded")]]
    )

    report = await run_report_import(client, None, options(dry_run=True), NOW)

    assert (report.received, report.valid) == (2, 1)
    assert dict(report.tally.skipped) == {"unknown_status": 1}
    assert report.tally.inserted == 0
    assert database.units_opened == 0 and database.committed.audits == []


async def test_a_dry_run_ignores_a_unit_opener_even_when_one_is_passed() -> None:
    database = FakeReportDatabase()

    await run([[raw_row(1)]], database, dry_run=True)

    assert database.units_opened == 0


# --- failure -------------------------------------------------------------------------------


async def test_a_fetch_failure_reports_the_code_and_what_was_committed() -> None:
    database = FakeReportDatabase()
    client = FakeAwinClient(
        transaction_pages=[raw_rows(1, PAGE_SIZE), AwinError(AwinReason.SERVER_ERROR)]
    )

    stopped = await failure(client, database)

    assert (
        stopped.cause == "server_error"
        and stopped.stage == "while fetching transactions"
    )
    assert stopped.report.tally.inserted == PAGE_SIZE and stopped.report.pages == 1
    assert len(database.committed.rows) == PAGE_SIZE
    (audit,) = database.committed.audits  # the partial run is on the trail
    assert audit["details"]["status"] == "failed"
    assert audit["details"]["cause"] == "server_error"


async def test_a_failure_before_anything_was_written_leaves_no_audit_row() -> None:
    database = FakeReportDatabase()

    stopped = await failure(
        FakeAwinClient(transaction_pages=[AwinError(AwinReason.AUTH_FAILED)]), database
    )

    assert stopped.cause == "auth_failed" and database.committed.audits == []


async def test_a_database_failure_names_the_error_type_and_commits_nothing_from_the_page() -> (
    None
):
    database = FakeReportDatabase(fail_insert_at=2)

    stopped = await failure(
        FakeAwinClient(transaction_pages=[raw_rows(1, 3)]), database
    )

    assert stopped.cause == "RuntimeError" and stopped.stage == "while writing reports"
    assert database.committed.rows == {}


async def test_a_busy_run_lock_fails_the_run_as_lock_timeout_and_writes_nothing() -> (
    None
):
    database = FakeReportDatabase(lock_error=FakeDriverError("55P03"))

    stopped = await failure(
        FakeAwinClient(transaction_pages=[raw_rows(1, 3)]), database
    )

    assert stopped.cause == "lock_timeout"
    assert database.committed.rows == {} and database.committed.audits == []


async def test_the_run_deadline_stops_a_slow_fetch() -> None:
    class SlowClient(FakeAwinClient):
        async def fetch_transactions(
            self, start: datetime, end: datetime, *, page: int, page_size: int
        ) -> TransactionsPage:
            await asyncio.sleep(5)
            return TransactionsPage(())

    stopped = await failure(SlowClient(), FakeReportDatabase(), max_seconds=0.05)

    assert stopped.cause == "run_deadline"


async def test_nothing_that_could_be_a_row_value_is_in_a_failure() -> None:
    database = FakeReportDatabase(fail_insert_at=1)

    stopped = await failure(
        FakeAwinClient(transaction_pages=[raw_rows(1, 1)]), database
    )

    assert "ref-1" not in repr((stopped.stage, stopped.cause, stopped.report))
