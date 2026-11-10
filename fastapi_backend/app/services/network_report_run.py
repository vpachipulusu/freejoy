"""One report run: fetch pages of transactions, validate them, write them in committed chunks.

``run_report_import`` is the whole loop and the only thing the command calls. The requested dates (inclusive,
UTC) are cut into windows of at most 31 days (Awin's documented maximum range), and for each window it asks
the client for pages of ``PAGE_SIZE`` rows, treats every row as untrusted (``parse_transaction``), drops
duplicates, and hands the valid ones to ``ReportImporter`` in chunks of at most ``PAGE_SIZE``, each in its own
database unit that commits once. A failure stops the run with ``ReportRunFailed`` carrying the report so far:
chunks already committed stay committed and the import is idempotent. With ``dry_run`` nothing is opened or
written: rows are fetched, validated and counted.

How a window ends, since Awin's documentation does not describe paging at all (an assumption):

* a page that is **not exactly full** (empty, short, or **longer** than asked for) finishes the window. A
  response longer than ``PAGE_SIZE`` means the paging parameters were ignored and everything came back at
  once, so asking again would only repeat it;
* a **full** page always leads to one more request (whatever ``pagination.totalPages`` says: it is not
  trusted to end a window early). If Awin ignores paging and the range holds exactly ``PAGE_SIZE`` rows the
  next page repeats the first and the run fails;
* a page whose ids were all seen on earlier pages of the window fails the run as ``pagination_repeat``;
* ``--max-pages`` pages (counted across windows) were fetched and more may exist: ``stopped_early`` and
  ``resume_from``, the first day of the window the run stopped in. A dry run returns that as a note; a real
  run **fails** (``max_pages_reached``, audit status ``incomplete``) because part of the range was not read.

A run where rows were received but none was valid fails (``no_valid_rows``): it imported nothing it could
trust, which is not a success.

A completed run writes one audit row (``admin.reports.imported``, counts and dates only). A run that fails
after a write unit was started writes one too (``failed`` or ``incomplete`` with the cause), best effort, and
``ReportRunFailed.audit_error`` names the exception type when even that could not be written. Both audit
writes are bounded by ``AUDIT_TIMEOUT_SECONDS``. Nothing here logs or returns a field of a row: a skipped row
is logged as a reason code only.
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from app.core.awin_client import (
    MAX_PAGE_SIZE,
    TRANSACTIONS_MAX_RANGE,
    AwinClient,
    AwinError,
    TransactionsPage,
)
from app.services.awin_import_run import RUN_DEADLINE, failure_cause
from app.services.awin_offer_import import Committer, ImportAborted, SystemAuditor
from app.services.awin_transactions import (
    Rejected,
    ReportRow,
    SkipReason,
    parse_transaction,
)
from app.services.network_report_import import (
    ReportImporter,
    ReportStore,
    ReportTally,
)

SOURCE = "commands.import_awin_reports"
AUDIT_ACTION = "admin.reports.imported"
AUDIT_RESOURCE = "network_report_rows"
AUDIT_TIMEOUT_SECONDS = 30
PAGE_SIZE = MAX_PAGE_SIZE
DEFAULT_MAX_PAGES, MAX_MAX_PAGES = 10, 50
DEFAULT_MAX_SECONDS = 1200  # the whole run, 20 minutes
MAX_WINDOW_DAYS = TRANSACTIONS_MAX_RANGE.days
MAX_LOGGED_SKIPS = 100
STAGE_FETCH = "while fetching transactions"
STAGE_WRITE = "while writing reports"
STAGE_AUDIT = "while recording the run"
STAGE_LIMIT = "at the page limit"
STAGE_VALIDATE = "after reading the report"
CAUSE_MAX_PAGES = "max_pages_reached"
CAUSE_NO_VALID_ROWS = "no_valid_rows"
STATUS_COMPLETED, STATUS_FAILED, STATUS_INCOMPLETE = "completed", "failed", "incomplete"
LAST_SECOND = time(23, 59, 59)
logger = logging.getLogger("freejoy.network_reports")


@dataclass(frozen=True)
class ReportUnit:
    """One database scope: the importer's store, the session that commits, the audit writer."""

    store: ReportStore
    session: Committer
    auditor: SystemAuditor


ReportUnitOpener = Callable[[], AbstractAsyncContextManager[ReportUnit]]


@dataclass(frozen=True)
class RunOptions:
    start: date  # inclusive, UTC
    end: date  # inclusive, UTC
    dry_run: bool = False
    max_pages: int = DEFAULT_MAX_PAGES
    max_seconds: float = DEFAULT_MAX_SECONDS  # the whole run, fetching and writing


@dataclass(frozen=True)
class RunReport:
    tally: ReportTally = field(default_factory=ReportTally)
    valid: int = 0  # rows that passed validation: written, or (dry run) that would be
    pages: int = 0
    received: int = 0
    write_units: int = 0  # database units started (the last may have failed)
    stopped_early: str | None = None  # "max_pages": more may exist
    resume_from: date | None = (
        None  # with ``stopped_early``: the first day of the window it stopped in
    )


class ReportRunFailed(Exception):
    """The run stopped. ``report`` is what was done (committed) before; ``cause`` is a code or type name.

    ``audit_error`` is the exception type name when the failed run's audit row could not be written.
    """

    def __init__(
        self,
        report: RunReport,
        stage: str,
        cause: str,
        audit_error: str | None = None,
    ) -> None:
        super().__init__(stage, cause)
        self.report, self.stage, self.cause = report, stage, cause
        self.audit_error = audit_error


@dataclass(frozen=True)
class _Progress:
    report: RunReport
    stage: str


def report_windows(start: date, end: date) -> list[tuple[datetime, datetime]]:
    """Consecutive UTC windows covering ``start`` to ``end`` inclusive, each within Awin's 31 days.

    Each runs from midnight to ``23:59:59`` of its last day, so the next begins one second after.
    """
    windows: list[tuple[datetime, datetime]] = []
    first = start
    while first <= end:
        last = min(first + timedelta(days=MAX_WINDOW_DAYS - 1), end)
        windows.append(
            (
                datetime.combine(first, time(), tzinfo=UTC),
                datetime.combine(last, LAST_SECOND, tzinfo=UTC),
            )
        )
        first = last + timedelta(days=1)
    return windows


def _raw_id(item: object) -> int | None:
    """The transaction id of a raw row when it is a plain integer, else ``None``."""
    if not isinstance(item, dict):
        return None
    value = item.get("id")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


@dataclass(frozen=True)
class _Parsed:
    rows: list[ReportRow]
    skipped: ReportTally
    rejected_ids: list[
        str
    ]  # ids of rows that failed validation (not duplicates), to count stored ones


def _parse_page(items: Sequence[Any], seen: set[int], logged: int) -> _Parsed:
    rows: list[ReportRow] = []
    rejected_ids: list[str] = []
    tally = ReportTally()
    for item in items:
        transaction_id = _raw_id(item)
        outcome = (
            Rejected(SkipReason.DUPLICATE_IN_FEED)
            if transaction_id is not None and transaction_id in seen
            else parse_transaction(item)
        )
        if transaction_id is not None:
            seen.add(transaction_id)
        if not isinstance(outcome, Rejected):
            rows.append(outcome)
            continue
        tally = tally.skip(outcome.reason)
        if (
            transaction_id is not None
            and outcome.reason is not SkipReason.DUPLICATE_IN_FEED
        ):
            rejected_ids.append(str(transaction_id))
        if logged + tally.skipped_total <= MAX_LOGGED_SKIPS:
            logger.warning("awin transaction skipped: reason=%s", outcome.reason.value)
    return _Parsed(rows, tally, rejected_ids)


def _check_not_a_repeat(items: Sequence[Any], seen: set[int]) -> None:
    ids = [found for found in map(_raw_id, items) if found is not None]
    if ids and all(found in seen for found in ids):
        raise ImportAborted("pagination_repeat")


def _window_finished(page: TransactionsPage) -> bool:
    """Only an exactly full page asks for another: a short or an over-long one ends the window."""
    return len(page.items) != PAGE_SIZE


async def _write_page(
    units: ReportUnitOpener,
    parsed: _Parsed,
    report: RunReport,
    now: datetime,
) -> AsyncIterator[RunReport]:
    """Write one page's valid rows in chunks of ``PAGE_SIZE``, one unit and one commit per chunk."""
    rows = parsed.rows
    chunks = [rows[i : i + PAGE_SIZE] for i in range(0, len(rows), PAGE_SIZE)]
    for index, chunk in enumerate(chunks or [[]]):
        report = replace(report, write_units=report.write_units + 1)
        yield report  # a unit is about to open: from here a failure may owe the audit trail a row
        async with units() as unit:
            importer = ReportImporter(unit.store, unit.session, lambda: now)
            written = await importer.import_batch(
                chunk, parsed.rejected_ids if index == 0 else ()
            )
            # Counted at once and handed over while the unit is still open, so a deadline that cancels the
            # session's close cannot lose a chunk that was committed.
            report = replace(report, tally=report.tally.merged(written))
            yield report


async def _read_window(
    client: AwinClient,
    units: ReportUnitOpener | None,
    options: RunOptions,
    now: datetime,
    window: tuple[datetime, datetime],
    report: RunReport,
) -> AsyncIterator[_Progress]:
    seen = set[int]()
    page_no = 1
    while True:
        page = await client.fetch_transactions(
            *window, page=page_no, page_size=PAGE_SIZE
        )
        _check_not_a_repeat(page.items, seen)
        parsed = _parse_page(page.items, seen, report.tally.skipped_total)
        report = replace(
            report,
            pages=report.pages + 1,
            received=report.received + len(page.items),
            valid=report.valid + len(parsed.rows),
            tally=report.tally.merged(parsed.skipped),
        )
        yield _Progress(report, STAGE_WRITE)
        if (
            units is not None
            and not options.dry_run
            and (parsed.rows or parsed.rejected_ids)
        ):
            async for written in _write_page(units, parsed, report, now):
                report = written
                yield _Progress(report, STAGE_WRITE)
        yield _Progress(report, STAGE_FETCH)
        if _window_finished(page):
            return
        if report.pages >= options.max_pages:
            stopped = replace(
                report, stopped_early="max_pages", resume_from=window[0].date()
            )
            yield _Progress(stopped, STAGE_FETCH)
            return
        page_no += 1


async def _progress(
    client: AwinClient,
    units: ReportUnitOpener | None,
    options: RunOptions,
    now: datetime,
) -> AsyncIterator[_Progress]:
    report = RunReport()
    for window in report_windows(options.start, options.end):
        if report.pages >= options.max_pages:
            stopped = replace(
                report, stopped_early="max_pages", resume_from=window[0].date()
            )
            yield _Progress(stopped, STAGE_FETCH)
            return
        async for progress in _read_window(client, units, options, now, window, report):
            report = progress.report
            yield progress
        if report.stopped_early:
            return


def _audit_details(
    report: RunReport, options: RunOptions, status: str, cause: str | None
) -> dict[str, Any]:
    """Counts and dates only: nothing of a row can be in it."""
    details: dict[str, Any] = {
        "status": status,
        "from": options.start.isoformat(),
        "to": options.end.isoformat(),
        "pages": report.pages,
        "fetched": report.received,
        "valid": report.valid,
        "inserted": report.tally.inserted,
        "updated": report.tally.updated,
        "unchanged": report.tally.unchanged,
        "unmatched": report.tally.unmatched,
        "rejected_known": report.tally.rejected_known,
        "skipped": dict(report.tally.skipped),
        "match_problems": dict(report.tally.match_problems),
        "stopped_early": report.stopped_early,
        "resume_from": report.resume_from.isoformat() if report.resume_from else None,
    }
    if cause is not None:
        details["cause"] = cause
    return details


async def _record_run(
    units: ReportUnitOpener,
    report: RunReport,
    options: RunOptions,
    status: str,
    cause: str | None = None,
) -> None:
    async with asyncio.timeout(AUDIT_TIMEOUT_SECONDS), units() as unit:
        await unit.auditor.record_system_action(
            SOURCE,
            action=AUDIT_ACTION,
            resource=AUDIT_RESOURCE,
            details=_audit_details(report, options, status, cause),
        )
        await unit.session.commit()


async def _failed(
    units: ReportUnitOpener | None,
    options: RunOptions,
    progress: _Progress,
    cause: str,
    status: str = STATUS_FAILED,
) -> ReportRunFailed:
    """The failure to raise; a run that started a write unit also owes (and attempts) its audit row."""
    report, audit_error = progress.report, None
    if units is not None and not options.dry_run and report.write_units:
        try:
            await _record_run(units, report, options, status, cause)
        except Exception as error:  # noqa: BLE001  (the run already failed: say only what could not be written)
            audit_error = type(error).__name__
    return ReportRunFailed(report, progress.stage, cause, audit_error)


async def _read_report(
    client: AwinClient,
    units: ReportUnitOpener | None,
    options: RunOptions,
    now: datetime,
) -> tuple[_Progress, tuple[str, str] | None]:
    """Run the loop: the last progress, and ``(stage, cause)`` when it stopped on a failure."""
    progress = _Progress(RunReport(), STAGE_FETCH)
    deadline = asyncio.timeout(options.max_seconds)
    try:
        async with deadline:
            async for progress in _progress(client, units, options, now):
                pass
    except AwinError as error:
        return progress, (STAGE_FETCH, error.reason.value)
    except ImportAborted as error:
        return progress, (progress.stage, error.reason)
    except Exception as error:  # noqa: BLE001  (the type is reported, never the message)
        cause = RUN_DEADLINE if deadline.expired() else failure_cause(error)
        return progress, (progress.stage, cause)
    return progress, None


async def run_report_import(
    client: AwinClient,
    units: ReportUnitOpener | None,
    options: RunOptions,
    now: datetime,
) -> RunReport:
    """Run the import; ``units`` may be ``None`` only for a dry run. Raises ``ReportRunFailed``."""
    if units is None and not options.dry_run:
        raise ValueError("a run that writes needs a unit opener")
    progress, failure = await _read_report(client, units, options, now)
    if failure is not None:
        stage, cause = failure
        raise await _failed(units, options, replace(progress, stage=stage), cause)
    report = progress.report
    if report.received and not report.valid:
        stuck = replace(progress, stage=STAGE_VALIDATE)
        raise await _failed(units, options, stuck, CAUSE_NO_VALID_ROWS)
    if report.stopped_early and not options.dry_run:
        stopped = replace(progress, stage=STAGE_LIMIT)
        raise await _failed(units, options, stopped, CAUSE_MAX_PAGES, STATUS_INCOMPLETE)
    if units is not None and not options.dry_run:
        try:
            await _record_run(units, report, options, STATUS_COMPLETED)
        except Exception as error:  # noqa: BLE001
            raise ReportRunFailed(report, STAGE_AUDIT, failure_cause(error)) from None
    return report
