"""Import Awin promotions and vouchers (joined programmes, UK) as DRAFT offers for an editor to review.

    uv run python -m commands.import_awin_offers --dry-run                 # fetch and validate, write nothing
    uv run python -m commands.import_awin_offers                           # import
    uv run python -m commands.import_awin_offers --limit 100 --max-pages 2 --updated-since 2026-11-01

Needs ``AWIN_PUBLISHER_ID`` and ``AWIN_OAUTH_API_TOKEN`` (the token goes only in an Authorization header)
and ``DATABASE_URL`` (the app settings require it; a ``--dry-run`` opens no connection to it). It never publishes, verifies or features an offer. An
imported offer is a DRAFT in a disabled "Imported (uncategorised)" category on an inactive merchant, so
an editor must choose a category, activate the merchant and verify the offer before it can go live. Safe
to repeat: an offer is found again by promotion id, and one an editor has edited, published, expired or
archived is never overwritten (the source change is only recorded). See
``docs/developer/backend-awin-import.md``.

Output is counts by outcome and by reason code. No URL, token, voucher code or advertiser text is ever
printed or logged; skipped items are logged as a promotion id and a reason code. A failed run sends ONE
Sentry event (stage, cause, counts, reason codes) after the last request has finished.

Exit codes: 0 the run completed (also when items were skipped or the run stopped at a limit); 1 the run
failed (stderr says how much was already committed, which stays committed); 2 bad arguments or missing
settings.
"""

import argparse
import asyncio
import re
import sys
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager, suppress
from datetime import UTC, date, datetime
from typing import Any, Protocol

from pydantic import SecretStr, ValidationError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.awin_client import AwinClient, HttpxAwinClient
from app.repositories.audit import AuditRepository
from app.repositories.awin_import import AwinImportRepository
from app.services.audit import AuditService
from app.services.awin_import_run import (
    DEFAULT_LIMIT,
    DEFAULT_MAX_PAGES,
    MAX_LIMIT,
    MAX_MAX_PAGES,
    ImportFailed,
    RunOptions,
    RunReport,
    Unit,
    UnitOpener,
    run_import,
)

PROG = "import-awin-offers"
FAILURE_MESSAGE = "Awin offers import run failed"
EVENT_CONTEXT = "awin_import"
MIN_VALUE = 1
STOPPED_NOTES = {
    "limit": "stopped at --limit: more promotions may exist, raise --limit or run again",
    "max_pages": "stopped at --max-pages: more promotions may exist, raise --max-pages or run again",
}


class Reporter(Protocol):
    """Anything callable like ``report_to_sentry`` (a test passes a recording one)."""

    def __call__(
        self, message: str, context: Mapping[str, Any], *, level: str = "error"
    ) -> object: ...


def database_units(maker: async_sessionmaker[AsyncSession]) -> UnitOpener:
    @asynccontextmanager
    async def open_unit() -> AsyncIterator[Unit]:
        async with maker() as session:
            yield Unit(
                AwinImportRepository(session),
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

    Sentry is started here, after every request has been made, so the HTTP client's breadcrumbs can
    never be attached to the event (the Awin client must not run in a process where it is on).
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
        "--dry-run",
        action="store_true",
        help="fetch and validate, write nothing, print the counts (no database is used)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help=f"most promotions to read, 1 to {MAX_LIMIT} (default {DEFAULT_LIMIT})",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=DEFAULT_MAX_PAGES,
        help=f"most pages to fetch, 1 to {MAX_MAX_PAGES} (default {DEFAULT_MAX_PAGES}); Awin allows 20 calls a minute",
    )
    parser.add_argument(
        "--updated-since",
        type=_iso_date,
        default=None,
        metavar="YYYY-MM-DD",
        help="only promotions Awin updated since this date",
    )
    return parser.parse_args(argv)


def _fail(message: str, code: int) -> int:
    print(f"{PROG}: {message}", file=sys.stderr)
    return code


def _invalid(args: argparse.Namespace) -> str | None:
    for name, value, cap in (
        ("--limit", args.limit, MAX_LIMIT),
        ("--max-pages", args.max_pages, MAX_MAX_PAGES),
    ):
        if not MIN_VALUE <= value <= cap:
            return f"{name} must be between {MIN_VALUE} and {cap}."
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
        f"created {tally.created}, updated {tally.updated}, unchanged {tally.unchanged}, "
        f"source changed and not applied {tally.source_changed}, skipped {tally.skipped_total}"
    )


def _reason_text(report: RunReport) -> str:
    return ", ".join(f"{k} {v}" for k, v in sorted(report.tally.skipped.items()))


def _summary_lines(report: RunReport, *, dry_run: bool) -> list[str]:
    fetched = (
        f"fetched {report.received} promotion(s) in {report.pages} page(s); "
        f"{report.valid} valid, {report.tally.skipped_total} skipped."
    )
    if dry_run:
        lines = [f"{PROG}: dry run, nothing was written. {fetched}"]
    else:
        lines = [f"{PROG}: {fetched}", f"{PROG}: {_counts_text(report)}."]
    if report.tally.skipped:
        lines.append(f"{PROG}: skipped by reason: {_reason_text(report)}.")
    if report.stopped_early:
        lines.append(f"{PROG}: {STOPPED_NOTES[report.stopped_early]}.")
    return lines


def _failure_context(failure: ImportFailed, *, dry_run: bool) -> dict[str, Any]:
    """What Sentry gets: ids and counts and reason codes. No URL, copy or token exists to send."""
    report = failure.report
    return {
        "stage": failure.stage,
        "cause": failure.cause,
        "dry_run": dry_run,
        "pages": report.pages,
        "received": report.received,
        "valid": report.valid,
        "created": report.tally.created,
        "updated": report.tally.updated,
        "unchanged": report.tally.unchanged,
        "source_changed": report.tally.source_changed,
        "skipped": dict(report.tally.skipped),
    }


def _send(reporter: Reporter, context: Mapping[str, Any]) -> None:
    try:
        reporter(FAILURE_MESSAGE, context, level="error")
    except Exception as error:  # noqa: BLE001  (the run's outcome stands; say only what failed)
        print(
            f"{PROG}: could not send the Sentry event ({type(error).__name__}).",
            file=sys.stderr,
        )


def _fail_run(failure: ImportFailed, *, dry_run: bool, reporter: Reporter) -> int:
    done = (
        "nothing was written (dry run)"
        if dry_run
        else f"already committed and kept: {_counts_text(failure.report)}; run it again to continue"
    )
    code = _fail(
        f"failed ({failure.cause}) {failure.stage}. After {failure.report.pages} page(s), {done}.",
        1,
    )
    _send(reporter, _failure_context(failure, dry_run=dry_run))
    return code


async def _run(
    client: AwinClient,
    units: UnitOpener | None,
    options: RunOptions,
    now: datetime,
    engine: AsyncEngine | None,
) -> RunReport:
    try:
        return await run_import(client, units, options, now)
    finally:
        with suppress(Exception):
            await client.aclose()
        if engine is not None:
            with suppress(
                Exception
            ):  # a failed close must not turn a finished run into exit 1
                await engine.dispose()


def _options(args: argparse.Namespace) -> RunOptions:
    return RunOptions(
        dry_run=args.dry_run,
        limit=args.limit,
        max_pages=args.max_pages,
        updated_since=args.updated_since,
    )


def _real_client() -> AwinClient | str:
    """The real client, or the message saying which settings are missing."""
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
    return build_client(settings.AWIN_PUBLISHER_ID, settings.AWIN_OAUTH_API_TOKEN)


def _real_units() -> tuple[UnitOpener, AsyncEngine]:
    from app.core.database import async_session_maker, engine

    return database_units(async_session_maker), engine


def _execute(
    args: argparse.Namespace,
    client: AwinClient,
    units: UnitOpener | None,
    now: datetime,
    reporter: Reporter,
) -> int:
    options = _options(args)
    engine: AsyncEngine | None = None
    try:
        if units is None and not args.dry_run:
            units, engine = _real_units()
        report = asyncio.run(_run(client, units, options, now, engine))
    except ImportFailed as stopped:
        return _fail_run(stopped, dry_run=args.dry_run, reporter=reporter)
    except Exception as error:  # noqa: BLE001  (report the type only, never the message)
        early = ImportFailed(
            RunReport(), "before the run started", type(error).__name__
        )
        return _fail_run(early, dry_run=args.dry_run, reporter=reporter)
    print("\n".join(_summary_lines(report, dry_run=args.dry_run)))
    return 0


def main(
    argv: Sequence[str] | None = None,
    *,
    client: AwinClient | None = None,
    units: UnitOpener | None = None,
    reporter: Reporter | None = None,
    now: datetime | None = None,
) -> int:
    """Entry point. The keyword arguments exist for tests; the command line passes none."""
    try:
        args = _parse(sys.argv[1:] if argv is None else argv)
    except SystemExit as stop:  # argparse: --help is 0, a bad flag or value is 2
        return stop.code if isinstance(stop.code, int) else 2
    if (problem := _invalid(args)) is not None:
        return _fail(problem, 2)
    built = client if client is not None else _real_client()
    if isinstance(built, str):
        return _fail(built, 2)
    return _execute(
        args, built, units, now or datetime.now(UTC), reporter or report_to_sentry
    )


if __name__ == "__main__":
    from app.core.logging import configure_logging

    configure_logging()
    sys.exit(main())
