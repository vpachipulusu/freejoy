"""Import Awin's own transaction report (joined programmes) into ``network_report_rows``.

    uv run python -m commands.import_awin_reports --from 2026-11-01 --to 2026-11-10 --dry-run   # fetch, validate
    uv run python -m commands.import_awin_reports --from 2026-11-01 --to 2026-11-10             # import
    uv run python -m commands.import_awin_reports --from 2026-08-01 --to 2026-11-10 --allow-long-range

Revenue is attributed from the network's reports and never inferred from clicks. For every transaction dated
from ``--from`` to ``--to`` (inclusive, UTC; at most 31 days unless ``--allow-long-range``, which reads the
range in windows of 31) the command stores the network's own transaction id, status (mapped onto pending,
approved, declined or paid), commission and sale amounts in minor units, currency, the reported click
reference, and (when they can be found) the offer, affiliate link and merchant it belongs to. A transaction
whose click reference matches no link is kept unmatched. Safe to repeat: a row is found again by the
network's transaction id, an unchanged row only has ``last_seen_at`` moved, and a row whose status or amount
changed at the network is updated. See ``docs/developer/backend-network-reports.md`` (its list of unverified
assumptions about Awin's response is what the first ``--dry-run`` confirms or breaks).

Needs ``AWIN_PUBLISHER_ID`` and ``AWIN_OAUTH_API_TOKEN`` (the token goes only in an Authorization header) and
``DATABASE_URL`` (the app settings require it; a ``--dry-run`` opens no connection to it). Not scheduled:
the housekeeping workflow deliberately excludes the Awin commands until the owner has run this once.

Output is counts by outcome and by reason code. No URL, token, amount, click reference or transaction id is
ever printed or logged. A failed run sends ONE Sentry event (stage, cause, counts, reason codes) after the
HTTP client has been closed.

Exit codes: 0 the run completed (also when some rows were skipped, and for a ``--dry-run`` that stopped at
``--max-pages``); 1 the run failed, including a run that writes and stops at ``--max-pages`` (an incomplete
range) or one where no row was valid (stderr says how much was already committed, which stays committed, and
from which date to rerun); 2 bad arguments (a range that needs more windows than ``--max-pages`` included) or
missing settings.
"""

import argparse
import asyncio
import re
import sys
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager, suppress
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol

from pydantic import SecretStr, ValidationError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.awin_client import AwinClient, HttpxAwinClient
from app.repositories.audit import AuditRepository
from app.repositories.network_reports import (
    DEFAULT_LOCK_TIMEOUT_MS,
    NetworkReportRepository,
)
from app.services.audit import AuditService
from app.services.awin_import_run import RUN_DEADLINE
from app.services.network_report_run import (
    CAUSE_MAX_PAGES,
    DEFAULT_MAX_PAGES,
    MAX_MAX_PAGES,
    MAX_WINDOW_DAYS,
    ReportRunFailed,
    ReportUnit,
    ReportUnitOpener,
    RunOptions,
    RunReport,
    report_windows,
    run_report_import,
)

PROG = "import-awin-reports"
FAILURE_MESSAGE = "Awin network report import run failed"
EVENT_CONTEXT = "awin_reports"
MIN_VALUE = 1
MAX_PAGES_NOTE = (
    "stopped at --max-pages: more transactions may exist; raise --max-pages "
    "(a run that writes would stop here and exit 1)"
)
EARLIEST_DATE = date(
    2000, 1, 1
)  # the same floor the report parser applies to a transaction date


class Reporter(Protocol):
    """Anything callable like ``report_to_sentry`` (a test passes a recording one)."""

    def __call__(
        self, message: str, context: Mapping[str, Any], *, level: str = "error"
    ) -> object: ...


def database_units(
    maker: async_sessionmaker[AsyncSession],
    lock_timeout_ms: int = DEFAULT_LOCK_TIMEOUT_MS,
) -> ReportUnitOpener:
    @asynccontextmanager
    async def open_unit() -> AsyncIterator[ReportUnit]:
        async with maker() as session:
            yield ReportUnit(
                NetworkReportRepository(session, lock_timeout_ms),
                session,
                AuditService(AuditRepository(session)),
            )

    return open_unit


def build_client(publisher_id: int, token: SecretStr) -> AwinClient:
    """The real client. The only place that creates one, so tests can replace it."""
    return HttpxAwinClient(publisher_id, token)


def report_to_sentry(
    message: str,
    context: Mapping[str, Any],
    settings: Any = None,
    transport: Any = None,
    *,
    level: str = "error",
) -> bool:
    """Send ``context`` as one Sentry event; nothing when ``SENTRY_DSN`` is not set.

    Sentry is started here, after the HTTP client has been closed, so its httpx integration can never
    record a request (the Awin client must not run in a process where it is on).
    """
    from app.core.sentry import flush_events, init_sentry, report_message

    if settings is None:
        from app.core.config import settings as app_settings

        settings = app_settings
    if not init_sentry(settings, transport=transport):
        return False
    sent = report_message(
        message, context_name=EVENT_CONTEXT, context=context, level=level
    )
    flush_events()
    return sent


def _iso_date(value: str) -> date:
    """Exactly ``YYYY-MM-DD`` (Python's own parser also takes ``20261101`` and week dates)."""
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None:
        raise argparse.ArgumentTypeError("must be a date like 2026-11-01")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a date like 2026-11-01") from None


def _parse(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--from",
        dest="start",
        type=_iso_date,
        required=True,
        metavar="YYYY-MM-DD",
        help="first day to read (UTC, inclusive)",
    )
    parser.add_argument(
        "--to",
        dest="end",
        type=_iso_date,
        required=True,
        metavar="YYYY-MM-DD",
        help=f"last day to read (UTC, inclusive, not before --from, at most {MAX_WINDOW_DAYS} days after it)",
    )
    parser.add_argument(
        "--allow-long-range",
        action="store_true",
        help=f"allow a range over {MAX_WINDOW_DAYS} days (read in windows of {MAX_WINDOW_DAYS}; Awin allows 20 calls a minute)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="fetch and validate, write nothing, print the counts (no database is used)",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=DEFAULT_MAX_PAGES,
        help=f"most pages to fetch across all windows, {MIN_VALUE} to {MAX_MAX_PAGES} (default {DEFAULT_MAX_PAGES})",
    )
    return parser.parse_args(argv)


def _fail(message: str, code: int) -> int:
    print(f"{PROG}: {message}", file=sys.stderr)
    return code


def _invalid(args: argparse.Namespace, today: date) -> str | None:
    if args.end < args.start:
        return "--to must not be before --from."
    if args.start < EARLIEST_DATE or args.end > today + timedelta(days=1):
        return f"--from and --to must be between {EARLIEST_DATE.isoformat()} and tomorrow (UTC)."
    days = (args.end - args.start) + timedelta(days=1)
    if days > timedelta(days=MAX_WINDOW_DAYS) and not args.allow_long_range:
        return (
            f"the range is {days.days} days; Awin allows {MAX_WINDOW_DAYS} per request. "
            "Shorten it or add --allow-long-range."
        )
    if not MIN_VALUE <= args.max_pages <= MAX_MAX_PAGES:
        return f"--max-pages must be between {MIN_VALUE} and {MAX_MAX_PAGES}."
    windows = len(report_windows(args.start, args.end))
    if windows > args.max_pages:
        return (
            f"the range needs {windows} windows of at most {MAX_WINDOW_DAYS} days but --max-pages is "
            f"{args.max_pages}: raise --max-pages or shorten the range."
        )
    return None


def _missing_settings(settings: Any) -> list[str]:
    missing = []
    if settings.AWIN_PUBLISHER_ID is None:
        missing.append("AWIN_PUBLISHER_ID")
    if settings.AWIN_OAUTH_API_TOKEN is None:
        missing.append("AWIN_OAUTH_API_TOKEN")
    return missing


def _counts_text(report: RunReport) -> str:
    tally = report.tally
    return (
        f"inserted {tally.inserted}, updated {tally.updated}, unchanged {tally.unchanged}, "
        f"unmatched {tally.unmatched}, skipped {tally.skipped_total}"
    )


def _counts_of(counts: Mapping[str, int]) -> str:
    return ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))


def _reason_text(report: RunReport) -> str:
    return _counts_of(report.tally.skipped)


def _summary_lines(report: RunReport, options: RunOptions) -> list[str]:
    fetched = (
        f"fetched {report.received} transaction(s) in {report.pages} page(s) for "
        f"{options.start.isoformat()} to {options.end.isoformat()}; "
        f"{report.valid} valid, {report.tally.skipped_total} skipped."
    )
    tally = report.tally
    if options.dry_run:
        lines = [f"{PROG}: dry run, nothing was written. {fetched}"]
    else:
        lines = [
            f"{PROG}: {fetched}",
            f"{PROG}: inserted {tally.inserted}, updated {tally.updated}, unchanged {tally.unchanged}, unmatched {tally.unmatched}.",
        ]
    if tally.skipped:
        lines.append(f"{PROG}: skipped by reason: {_reason_text(report)}.")
    if tally.match_problems:
        lines.append(
            f"{PROG}: not matched because: {_counts_of(tally.match_problems)}."
        )
    if tally.rejected_known:
        lines.append(
            f"{PROG}: {tally.rejected_known} rejected row(s) are already stored and were left as they were."
        )
    if report.stopped_early:
        lines.append(f"{PROG}: {MAX_PAGES_NOTE}.")
    return lines


def _failure_context(failure: ReportRunFailed, options: RunOptions) -> dict[str, Any]:
    """What Sentry gets: dates, counts and reason codes. No row, URL or token exists to send."""
    report = failure.report
    return {
        "stage": failure.stage,
        "cause": failure.cause,
        "dry_run": options.dry_run,
        "from": options.start.isoformat(),
        "to": options.end.isoformat(),
        "pages": report.pages,
        "received": report.received,
        "valid": report.valid,
        "inserted": report.tally.inserted,
        "updated": report.tally.updated,
        "unchanged": report.tally.unchanged,
        "unmatched": report.tally.unmatched,
        "rejected_known": report.tally.rejected_known,
        "skipped": dict(report.tally.skipped),
        "match_problems": dict(report.tally.match_problems),
        "resume_from": report.resume_from.isoformat() if report.resume_from else None,
    }


def _send(reporter: Reporter, context: Mapping[str, Any]) -> None:
    try:
        reporter(FAILURE_MESSAGE, context, level="error")
    except Exception as error:  # noqa: BLE001  (the run's outcome stands; say only what failed)
        print(
            f"{PROG}: could not send the Sentry event ({type(error).__name__}).",
            file=sys.stderr,
        )


def _outcome_text(failure: ReportRunFailed, options: RunOptions) -> str:
    """What was done before the failure, and what to do next (dates and counts only)."""
    if options.dry_run:
        return "nothing was written (dry run)"
    # A deadline can cancel a chunk inside its own commit: it may be committed and not counted.
    qualifier = " (at least)" if failure.cause == RUN_DEADLINE else ""
    kept = f"already committed and kept{qualifier}: {_counts_text(failure.report)}"
    resume = failure.report.resume_from
    if failure.cause == CAUSE_MAX_PAGES and resume is not None:
        day = resume.isoformat()
        return f"{kept}; stopped in the window starting {day}; rerun with --from {day}"
    return f"{kept}; running it again is safe"


def _fail_run(failure: ReportRunFailed, options: RunOptions, reporter: Reporter) -> int:
    code = _fail(
        f"failed ({failure.cause}) {failure.stage}. After {failure.report.pages} page(s), "
        f"{_outcome_text(failure, options)}.",
        1,
    )
    if failure.audit_error is not None:
        _fail(
            f"could not record the failed run in the audit log ({failure.audit_error}).",
            1,
        )
    _send(reporter, _failure_context(failure, options))
    return code


async def _run(
    client: AwinClient,
    units: ReportUnitOpener | None,
    options: RunOptions,
    now: datetime,
    engine: AsyncEngine | None,
) -> RunReport:
    try:
        return await run_report_import(client, units, options, now)
    finally:
        with suppress(Exception):
            await client.aclose()
        if engine is not None:
            with suppress(
                Exception
            ):  # a failed close must not turn a finished run into exit 1
                await engine.dispose()


def _credentials() -> tuple[int, SecretStr] | str:
    """The publisher id and token from the settings, or the message saying what is wrong."""
    try:
        from app.core.config import settings
    except ValidationError:  # the message is withheld: it could name a value
        return (
            "the settings are invalid: check DATABASE_URL, AWIN_PUBLISHER_ID (a positive "
            "integer) and AWIN_OAUTH_API_TOKEN (printable ASCII, no spaces)."
        )

    missing = _missing_settings(settings)
    if missing:
        return f"set {' and '.join(missing)} (see fastapi_backend/.env.example) before running."
    assert settings.AWIN_PUBLISHER_ID is not None
    assert settings.AWIN_OAUTH_API_TOKEN is not None
    return settings.AWIN_PUBLISHER_ID, settings.AWIN_OAUTH_API_TOKEN


def _real_units() -> tuple[ReportUnitOpener, AsyncEngine]:
    from app.core.database import async_session_maker, engine

    return database_units(async_session_maker), engine


def _execute(
    options: RunOptions,
    client: AwinClient,
    units: ReportUnitOpener | None,
    now: datetime,
    reporter: Reporter,
) -> int:
    engine: AsyncEngine | None = None
    try:
        if units is None and not options.dry_run:
            units, engine = _real_units()
        report = asyncio.run(_run(client, units, options, now, engine))
    except ReportRunFailed as stopped:
        return _fail_run(stopped, options, reporter)
    except Exception as error:  # noqa: BLE001  (report the type only, never the message)
        early = ReportRunFailed(
            RunReport(), "before the run started", type(error).__name__
        )
        return _fail_run(early, options, reporter)
    print("\n".join(_summary_lines(report, options)))
    return 0


def main(
    argv: Sequence[str] | None = None,
    *,
    client: AwinClient | None = None,
    units: ReportUnitOpener | None = None,
    reporter: Reporter | None = None,
    now: datetime | None = None,
) -> int:
    """Entry point. The keyword arguments exist for tests; the command line passes none."""
    try:
        args = _parse(sys.argv[1:] if argv is None else argv)
    except SystemExit as stop:  # argparse: --help is 0, a bad flag or value is 2
        return stop.code if isinstance(stop.code, int) else 2
    now = now or datetime.now(UTC)
    if (problem := _invalid(args, now.date())) is not None:
        return _fail(problem, 2)
    if client is None:
        credentials = _credentials()
        if isinstance(credentials, str):
            return _fail(credentials, 2)
        client = build_client(*credentials)
    options = RunOptions(
        start=args.start,
        end=args.end,
        dry_run=args.dry_run,
        max_pages=args.max_pages,
    )
    return _execute(options, client, units, now, reporter or report_to_sentry)


if __name__ == "__main__":
    from app.core.logging import configure_logging

    configure_logging()
    sys.exit(main())
