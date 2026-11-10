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
    database.add_link(
        FakeLink(
            "l1",
            "o1",
            "https://www.awin1.com/cread.php?awinmid=77&awinaffid=424242",
            "ref-1",
        )
    )
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
        "rejected_known",
        "skipped",
        "match_problems",
        "stopped_early",
        "resume_from",
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


async def test_a_full_page_still_asks_for_the_next_whatever_the_total_says() -> None:
    client = FakeAwinClient(
        transaction_pages=[raw_rows(1, PAGE_SIZE), raw_rows(1000, 3)], total_pages=1
    )

    report = await run_report_import(client, FakeReportDatabase().unit, options(), NOW)

    assert report.pages == 2 and len(client.transaction_requests) == 2


async def test_a_short_page_ends_the_window_whatever_the_total_says() -> None:
    client = FakeAwinClient(
        transaction_pages=[raw_rows(1, 3), raw_rows(10, 3)], total_pages=2
    )

    report = await run_report_import(client, FakeReportDatabase().unit, options(), NOW)

    assert report.pages == 1 and len(client.transaction_requests) == 1


async def test_a_response_longer_than_the_page_size_finishes_the_window_in_chunks() -> (
    None
):
    database = FakeReportDatabase()
    wide = {"start": date(2026, 9, 1), "end": date(2026, 10, 15)}  # two windows
    client = FakeAwinClient(
        transaction_pages=[raw_rows(1, PAGE_SIZE + 50), raw_rows(1000, 2)]
    )

    report = await run_report_import(client, database.unit, options(**wide), NOW)

    starts = [request[0] for request in client.transaction_requests]
    assert starts == [
        datetime(2026, 9, 1, tzinfo=UTC),
        datetime(2026, 10, 2, tzinfo=UTC),
    ]
    assert [request[2] for request in client.transaction_requests] == [1, 1]
    assert report.tally.inserted == PAGE_SIZE + 52
    assert report.pages == 2
    # two write units for the over-long page, one for the next window, one for the audit row
    assert database.units_opened == 4 and database.commits == 4
    assert len(database.committed.rows) == PAGE_SIZE + 52


async def test_a_dry_run_that_reaches_max_pages_returns_a_note_with_the_resume_date() -> (
    None
):
    client = FakeAwinClient(
        transaction_pages=[raw_rows(n * 1000, PAGE_SIZE) for n in range(1, 4)]
    )

    report = await run_report_import(
        client, None, options(max_pages=2, dry_run=True), NOW
    )

    assert report.pages == 2 and report.stopped_early == "max_pages"
    assert report.resume_from == DAY and len(client.transaction_requests) == 2


async def test_a_real_run_that_reaches_max_pages_mid_window_fails_as_incomplete() -> (
    None
):
    database = FakeReportDatabase()
    client = FakeAwinClient(
        transaction_pages=[raw_rows(n * 1000, PAGE_SIZE) for n in range(1, 4)]
    )

    stopped = await failure(client, database, max_pages=2)

    assert stopped.cause == "max_pages_reached" and stopped.stage == "at the page limit"
    assert stopped.report.pages == 2 and stopped.report.resume_from == DAY
    assert stopped.report.tally.inserted == 2 * PAGE_SIZE  # what was read is kept
    (audit,) = database.committed.audits
    assert audit["details"]["status"] == "incomplete"
    assert audit["details"]["cause"] == "max_pages_reached"
    assert audit["details"]["stopped_early"] == "max_pages"
    assert audit["details"]["resume_from"] == "2026-11-03"


async def test_a_real_run_that_reaches_max_pages_between_windows_resumes_at_the_next() -> (
    None
):
    wide = {"start": date(2026, 9, 1), "end": date(2026, 10, 15), "max_pages": 1}
    client = FakeAwinClient(transaction_pages=[raw_rows(1, 2), raw_rows(10, 2)])

    stopped = await failure(client, FakeReportDatabase(), **wide)

    assert stopped.cause == "max_pages_reached" and stopped.report.pages == 1
    assert stopped.report.resume_from == date(2026, 10, 2)  # the unread window


async def test_a_run_where_every_row_is_rejected_fails_in_both_modes() -> None:
    page = [raw_row(1, commissionStatus="refunded"), raw_row(2, advertiserId="x")]
    database = FakeReportDatabase()

    real = await failure(FakeAwinClient(transaction_pages=[page]), database)
    dry = await failure(FakeAwinClient(transaction_pages=[page]), None, dry_run=True)

    assert real.cause == dry.cause == "no_valid_rows"
    assert real.report.received == 2 and real.report.valid == 0
    assert database.committed.rows == {}


async def test_an_empty_report_is_not_a_failure() -> None:
    report = await run([[]], FakeReportDatabase())

    assert report.received == 0 and report.valid == 0


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
    assert database.committed.rows == {}
    (audit,) = (
        database.committed.audits
    )  # the audit write takes no run lock, so the failure is on the trail
    assert audit["details"]["status"] == "failed"
    assert audit["details"]["cause"] == "lock_timeout"


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


async def test_max_pages_is_not_reported_when_the_last_page_was_short() -> None:
    client = FakeAwinClient(transaction_pages=[raw_rows(1, 5)])

    report = await run_report_import(
        client, FakeReportDatabase().unit, options(max_pages=1), NOW
    )

    assert report.stopped_early is None and report.resume_from is None


# --- rejected rows that are already stored, and match problems --------------------------------


async def test_a_stored_row_that_later_fails_validation_is_left_alone_and_counted() -> (
    None
):
    database = FakeReportDatabase()
    await run([[raw_row(1), raw_row(2)]], database)
    before = {k: dict(v) for k, v in database.committed.rows.items()}

    report = await run(
        [[raw_row(1, commissionAmount={"amount": -1, "currency": "GBP"}), raw_row(2)]],
        database,
    )

    assert report.tally.rejected_known == 1 and report.tally.unchanged == 1
    assert report.tally.skipped_total == 1
    rows_now = database.committed.rows
    assert (
        rows_now[("awin", "1")]["report_hash"] == before[("awin", "1")]["report_hash"]
    )
    assert database.committed.audits[-1]["details"]["rejected_known"] == 1


async def test_a_rejected_row_that_was_never_stored_is_not_counted_as_known() -> None:
    database = FakeReportDatabase()

    report = await run(
        [[raw_row(1), raw_row(2, commissionStatus="refunded")]], database
    )

    assert report.tally.rejected_known == 0


async def test_a_duplicate_in_the_feed_is_not_a_known_rejection() -> None:
    database = FakeReportDatabase()
    await run([[raw_row(1)]], database)

    report = await run([[raw_row(1), raw_row(1)]], database)

    assert report.tally.rejected_known == 0
    assert dict(report.tally.skipped) == {"duplicate_in_feed": 1}


async def test_match_problems_reach_the_report_and_the_audit_row() -> None:
    database = FakeReportDatabase()
    database.add_link(
        FakeLink(
            "l1",
            "o1",
            "https://www.awin1.com/cread.php?awinmid=11&awinaffid=424242",
            "ref-1",
        )
    )

    report = await run([[raw_row(1, clickRefs={"clickRef": "ref-1"})]], database)

    assert dict(report.tally.match_problems) == {"merchant_mismatch": 1}
    assert database.committed.audits[0]["details"]["match_problems"] == {
        "merchant_mismatch": 1
    }


# --- the audit row is bounded and its failure is visible --------------------------------------


async def test_a_run_that_writes_needs_a_unit_opener() -> None:
    with pytest.raises(ValueError):
        await run_report_import(FakeAwinClient(), None, options(), NOW)


async def test_a_failed_runs_audit_row_that_cannot_be_written_is_named_by_type() -> (
    None
):
    database = FakeReportDatabase()
    client = FakeAwinClient(
        transaction_pages=[raw_rows(1, PAGE_SIZE), AwinError(AwinReason.SERVER_ERROR)]
    )
    # the pages are written, then the audit store starts failing
    database.audit_error = KeyError("never shown")

    stopped = await failure(client, database)

    assert stopped.cause == "server_error" and stopped.audit_error == "KeyError"
    assert database.committed.audits == []
    assert "never shown" not in repr((stopped.cause, stopped.audit_error))


async def test_a_failure_in_the_first_write_unit_still_owes_the_audit_row() -> None:
    database = FakeReportDatabase(fail_insert_at=1)

    stopped = await failure(
        FakeAwinClient(transaction_pages=[raw_rows(1, 3)]), database
    )

    assert stopped.cause == "RuntimeError" and stopped.audit_error is None
    (audit,) = database.committed.audits
    assert audit["details"]["status"] == "failed" and audit["details"]["inserted"] == 0


async def test_a_completed_runs_audit_failure_is_reported_at_the_audit_stage() -> None:
    database = FakeReportDatabase(audit_error=KeyError("never shown"))

    stopped = await failure(
        FakeAwinClient(transaction_pages=[raw_rows(1, 2)]), database
    )

    assert stopped.stage == "while recording the run" and stopped.cause == "KeyError"
    assert len(database.committed.rows) == 2  # the pages were committed and stay


async def test_a_slow_audit_write_is_cut_off(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.services.network_report_run as module

    monkeypatch.setattr(module, "AUDIT_TIMEOUT_SECONDS", 0.05)
    database = FakeReportDatabase(audit_delay=5)

    stopped = await failure(
        FakeAwinClient(transaction_pages=[raw_rows(1, 2)]), database
    )

    assert (
        stopped.stage == "while recording the run" and stopped.cause == "TimeoutError"
    )
