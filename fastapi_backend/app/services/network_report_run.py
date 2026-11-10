"""One report run: fetch pages of transactions, validate them, write them one committed page at a time.

``run_report_import`` is the whole loop and the only thing the command calls. The requested dates (inclusive,
UTC) are cut into windows of at most 31 days (Awin's documented maximum range), and for each window it asks
the client for pages of ``PAGE_SIZE`` rows, treats every row as untrusted (``parse_transaction``), drops
duplicates, and hands the valid ones to ``ReportImporter`` in one database unit per page that commits once.
A failure stops the run with ``ReportRunFailed`` carrying the report so far: pages already committed stay
committed and the next run carries on (the import is idempotent). With ``dry_run`` nothing is opened or
written: rows are fetched, validated and counted.

How a window ends, since Awin's documentation does not describe paging at all (an assumption):

* the page holds fewer rows than were asked for (an empty page included), or the response said how many pages
  exist and this was the last one: the window is finished;
* a page whose ids were all seen on earlier pages of the window is not trusted (the pagination is not doing
  what it was assumed to, for example the parameters are ignored and the whole range comes back every time):
  the run fails with ``pagination_repeat``;
* ``--max-pages`` pages (counted across windows) were fetched and more may exist: stop,
  ``stopped_early = "max_pages"``.

A completed run writes one audit row (``admin.reports.imported``, counts only). A run that fails after it
committed something writes one too, with ``status = failed`` and the cause, best effort. Nothing here logs or
returns a field of a row: a skipped row is logged as a reason code only.
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import AbstractAsyncContextManager, suppress
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
PAGE_SIZE = MAX_PAGE_SIZE
DEFAULT_MAX_PAGES, MAX_MAX_PAGES = 10, 50
DEFAULT_MAX_SECONDS = 1200  # the whole run, 20 minutes
MAX_WINDOW_DAYS = TRANSACTIONS_MAX_RANGE.days
MAX_LOGGED_SKIPS = 100
STAGE_FETCH = "while fetching transactions"
STAGE_WRITE = "while writing reports"
STAGE_AUDIT = "while recording the run"
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
    stopped_early: str | None = None  # "max_pages": more may exist


class ReportRunFailed(Exception):
    """The run stopped. ``report`` is what was done (committed) before; ``cause`` is a code or type name."""

    def __init__(self, report: RunReport, stage: str, cause: str) -> None:
        super().__init__(stage, cause)
        self.report, self.stage, self.cause = report, stage, cause


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


def _parse_page(
    items: Sequence[Any], seen: set[int], logged: int
) -> tuple[list[ReportRow], ReportTally]:
    rows: list[ReportRow] = []
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
        if isinstance(outcome, Rejected):
            tally = tally.skip(outcome.reason)
            if logged + tally.skipped_total <= MAX_LOGGED_SKIPS:
                logger.warning(
                    "awin transaction skipped: reason=%s", outcome.reason.value
                )
        else:
            rows.append(outcome)
    return rows, tally


def _check_not_a_repeat(items: Sequence[Any], seen: set[int]) -> None:
    ids = [found for found in map(_raw_id, items) if found is not None]
    if ids and all(found in seen for found in ids):
        raise ImportAborted("pagination_repeat")


def _window_finished(page: TransactionsPage, page_no: int) -> bool:
    if not page.items:
        return True
    if (
        page.total_pages is not None
    ):  # the response said how many pages exist: believe it
        return page_no >= page.total_pages
    return len(page.items) < PAGE_SIZE


async def _write_page(
    units: ReportUnitOpener, rows: Sequence[ReportRow], report: RunReport, now: datetime
) -> AsyncIterator[RunReport]:
    async with units() as unit:
        importer = ReportImporter(unit.store, unit.session, lambda: now)
        written = await importer.import_batch(rows)
        # Counted at once and handed over while the unit is still open, so a deadline that cancels the
        # session's close cannot lose a page that was committed.
        yield replace(report, tally=report.tally.merged(written))


async def _progress(
    client: AwinClient,
    units: ReportUnitOpener | None,
    options: RunOptions,
    now: datetime,
) -> AsyncIterator[_Progress]:
    report = RunReport()
    for window_start, window_end in report_windows(options.start, options.end):
        if report.pages >= options.max_pages:
            yield _Progress(replace(report, stopped_early="max_pages"), STAGE_FETCH)
            return
        seen = set[int]()
        page_no = 1
        while True:
            page = await client.fetch_transactions(
                window_start, window_end, page=page_no, page_size=PAGE_SIZE
            )
            _check_not_a_repeat(page.items, seen)
            rows, skipped = _parse_page(page.items, seen, report.tally.skipped_total)
            report = replace(
                report,
                pages=report.pages + 1,
                received=report.received + len(page.items),
                valid=report.valid + len(rows),
                tally=report.tally.merged(skipped),
            )
            yield _Progress(report, STAGE_WRITE)
            if units is not None and not options.dry_run and rows:
                async for written in _write_page(units, rows, report, now):
                    report = written
                    yield _Progress(report, STAGE_WRITE)
            yield _Progress(report, STAGE_FETCH)
            if _window_finished(page, page_no):
                break
            if report.pages >= options.max_pages:
                yield _Progress(replace(report, stopped_early="max_pages"), STAGE_FETCH)
                return
            page_no += 1


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
        "skipped": dict(report.tally.skipped),
        "stopped_early": report.stopped_early,
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
    async with units() as unit:
        await unit.auditor.record_system_action(
            SOURCE,
            action=AUDIT_ACTION,
            resource=AUDIT_RESOURCE,
            details=_audit_details(report, options, status, cause),
        )
        await unit.session.commit()


async def _failed(
    units: ReportUnitOpener | None, options: RunOptions, progress: _Progress, cause: str
) -> ReportRunFailed:
    """The failure to raise; a run that committed something also leaves its (partial) audit row."""
    report = progress.report
    if units is not None and not options.dry_run and report.tally.written:
        with suppress(
            Exception
        ):  # the trail is best effort here: the run already failed
            await _record_run(units, report, options, "failed", cause)
    return ReportRunFailed(report, progress.stage, cause)


async def run_report_import(
    client: AwinClient,
    units: ReportUnitOpener | None,
    options: RunOptions,
    now: datetime,
) -> RunReport:
    """Run the import; ``units`` may be ``None`` only for a dry run. Raises ``ReportRunFailed``."""
    progress = _Progress(RunReport(), STAGE_FETCH)
    deadline = asyncio.timeout(options.max_seconds)
    failed: tuple[str, str] | None = None
    try:
        async with deadline:
            async for progress in _progress(client, units, options, now):
                pass
    except AwinError as error:
        failed = (STAGE_FETCH, error.reason.value)
    except ImportAborted as error:
        failed = (progress.stage, error.reason)
    except Exception as error:  # noqa: BLE001  (the type is reported, never the message)
        failed = (
            progress.stage,
            RUN_DEADLINE if deadline.expired() else failure_cause(error),
        )
    if failed is not None:
        raise await _failed(
            units, options, replace(progress, stage=failed[0]), failed[1]
        ) from None
    if units is not None and not options.dry_run:
        try:
            await _record_run(units, progress.report, options, "completed")
        except Exception as error:  # noqa: BLE001
            raise ReportRunFailed(
                progress.report, STAGE_AUDIT, failure_cause(error)
            ) from None
    return progress.report
