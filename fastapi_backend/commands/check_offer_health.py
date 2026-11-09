"""Expire ended offers and report which live offers point at a dead destination.

    uv run python -m commands.check_offer_health                          # both steps
    uv run python -m commands.check_offer_health --skip-reachability      # expire only, no network
    uv run python -m commands.check_offer_health --limit 100 --concurrency 3

1. **Expire.** Every ``PUBLISHED`` offer whose ``end_at`` has passed moves to ``EXPIRED`` through
   the offer status machine, in committed batches, with one audit row each (``admin.offers.expire``,
   no ``user_id``, ``metadata.actor`` = ``commands.check_offer_health``). A rerun does nothing more.
2. **Check (report only).** For live offers it requests the offer ``source_url`` and the active
   affiliate links whose provider is ``direct``. It never requests a network provider's tracking
   link (that would record a click at the network). An unreachable destination is reported, never
   expired or edited. URLs are re-validated, resolved here and pinned to a public address; see
   ``app/core/url_probe.py``. ``--limit`` caps the URLs per run (least recently verified first).

Output is counts. The offer ids, link ids and reason codes of the problems go to the log (JSON on
stdout when run as a script) and, when ``SENTRY_DSN`` is set, to ONE Sentry summary event per run.
No URL, query string or header is ever printed or logged.

Exit codes: 0 the run completed (also when URLs are unreachable); 1 the run itself failed (stderr
says how many offers were already expired); 2 bad arguments. Safe to repeat. Nothing schedules it
yet: see ``docs/developer/backend-offer-health.md``.
"""

import argparse
import asyncio
import re
import sys
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.url_probe import HttpxUrlProbe, ProbeStatus, UrlProbe
from app.repositories.audit import AuditRepository
from app.repositories.offer_health import OfferHealthRepository
from app.services.audit import AuditService
from app.services.offer_expiry import (
    DEFAULT_BATCH_SIZE,
    Committer,
    ExpiryFailed,
    ExpiryStore,
    SystemAuditor,
    expire_ended_offers,
)
from app.services.offer_reachability import (
    DEFAULT_CONCURRENCY,
    DEFAULT_LIMIT,
    CheckTarget,
    Finding,
    ReachabilityReport,
    ReachabilityStore,
    collect_targets,
    event_context,
    probe_targets,
)

PROG = "check-offer-health"
EVENT_MESSAGE = "Offer health check found unreachable or refused URLs"
EVENT_CONTEXT = "offer_health"
MIN_VALUE = 1
MAX_CONCURRENCY = 20
USER_AGENT_VERSION = "1.0"


class HealthStore(ExpiryStore, ReachabilityStore, Protocol):
    """Everything the command reads and writes through the repository."""


@dataclass(frozen=True)
class Unit:
    """One database scope: the repository, the session that commits, and the audit writer."""

    store: HealthStore
    session: Committer
    auditor: SystemAuditor


UnitOpener = Callable[[], AbstractAsyncContextManager[Unit]]
Reporter = Callable[[Mapping[str, Any]], object]


@dataclass(frozen=True)
class RunResult:
    expired: int
    report: ReachabilityReport | None  # None when reachability was skipped


class RunFailed(Exception):
    """The run stopped; ``expired`` offers had already been committed. ``cause`` is a type name."""

    def __init__(self, expired: int, stage: str, cause: str) -> None:
        super().__init__(expired, stage, cause)
        self.expired, self.stage, self.cause = expired, stage, cause


def database_units(maker: async_sessionmaker[AsyncSession]) -> UnitOpener:
    @asynccontextmanager
    async def open_unit() -> AsyncIterator[Unit]:
        async with maker() as session:
            yield Unit(
                OfferHealthRepository(session),
                session,
                AuditService(AuditRepository(session)),
            )

    return open_unit


def build_user_agent(app_name: str, site_url: str) -> str:
    """A neutral, ASCII user agent that names the site, for example ``Freejoy-LinkCheck/1.0 (+https://...)``."""
    words = re.findall(r"[A-Za-z0-9]+", app_name)
    product = words[0] if words else "Site"
    return f"{product}-LinkCheck/{USER_AGENT_VERSION} (+{site_url})".encode(
        "ascii", "ignore"
    ).decode("ascii")


def report_to_sentry(
    context: Mapping[str, Any],
    settings: Any = None,
    transport: Any = None,
) -> bool:
    """Send the summary as one Sentry event; nothing when ``SENTRY_DSN`` is not set.

    Sentry is started here, after every request has been made, so the HTTP client's breadcrumbs
    (which hold URLs) can never be attached to the event.
    """
    from app.core.sentry import flush_events, init_sentry, report_message

    if settings is None:
        from app.core.config import settings as app_settings

        settings = app_settings
    if not init_sentry(settings, transport=transport):
        return False
    sent = report_message(EVENT_MESSAGE, context_name=EVENT_CONTEXT, context=context)
    flush_events()
    return sent


async def _expire(units: UnitOpener, args: argparse.Namespace, now: datetime) -> int:
    try:
        async with units() as unit:
            return await expire_ended_offers(
                unit.store,
                unit.session,
                unit.auditor,
                now,
                batch_size=args.batch_size,
            )
    except ExpiryFailed as failure:
        cause = type(failure.__cause__).__name__
        raise RunFailed(failure.expired, "while expiring offers", cause) from None


async def _probe(
    probe: UrlProbe | None, targets: Sequence[CheckTarget], concurrency: int
) -> list[Finding]:
    if probe is not None:
        return await probe_targets(probe, targets, concurrency=concurrency)
    from app.core.config import settings

    real = HttpxUrlProbe(
        user_agent=build_user_agent(settings.APP_NAME, settings.PUBLIC_SITE_URL)
    )
    try:
        return await probe_targets(real, targets, concurrency=concurrency)
    finally:
        await real.aclose()


async def _check(
    units: UnitOpener,
    args: argparse.Namespace,
    probe: UrlProbe | None,
    now: datetime,
    expired: int,
) -> ReachabilityReport:
    # The database scope closes before any request, so no connection idles during the network work.
    try:
        async with units() as unit:
            targets = await collect_targets(unit.store, now, limit=args.limit)
    except Exception as error:  # noqa: BLE001  (the type is reported, never the message)
        stage = "while reading the URLs to check"
        raise RunFailed(expired, stage, type(error).__name__) from None
    findings = await _probe(probe, targets.items, args.concurrency)
    return ReachabilityReport(tuple(findings), targets.not_followable)


async def _run(
    units: UnitOpener,
    args: argparse.Namespace,
    probe: UrlProbe | None,
    now: datetime,
    engine: AsyncEngine | None,
) -> RunResult:
    try:
        expired = await _expire(units, args, now)
        if args.skip_reachability:
            return RunResult(expired, None)
        return RunResult(expired, await _check(units, args, probe, now, expired))
    finally:
        if engine is not None:
            await engine.dispose()


def _parse(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--skip-reachability",
        action="store_true",
        help="only expire ended offers; make no network request",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help=f"most URLs to check in one run (default {DEFAULT_LIMIT})",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"offers per committed expiry batch (default {DEFAULT_BATCH_SIZE})",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=DEFAULT_CONCURRENCY,
        help=f"requests in flight at once, 1 to {MAX_CONCURRENCY} (default {DEFAULT_CONCURRENCY})",
    )
    return parser.parse_args(argv)


def _fail(message: str, code: int) -> int:
    print(f"{PROG}: {message}", file=sys.stderr)
    return code


def _invalid(args: argparse.Namespace) -> str | None:
    if min(args.limit, args.batch_size, args.concurrency) < MIN_VALUE:
        return f"--limit, --batch-size and --concurrency must be at least {MIN_VALUE}."
    if args.concurrency > MAX_CONCURRENCY:
        return f"--concurrency must be at most {MAX_CONCURRENCY}."
    return None


def _summary_lines(result: RunResult) -> list[str]:
    lines = [f"{PROG}: expired {result.expired} offer(s) whose end date had passed."]
    report = result.report
    if report is None:
        return [*lines, f"{PROG}: reachability checks skipped (--skip-reachability)."]
    counts = ", ".join(f"{s.value} {report.count(s)}" for s in ProbeStatus)
    line = f"{PROG}: checked {report.checked} URL(s): {counts}."
    if report.not_followable:
        line += f" {report.not_followable} direct link(s) skipped (host not one /go/ follows)."
    lines.append(line)
    if report.problems:
        reasons = ", ".join(
            f"{k} {v}" for k, v in sorted(report.reason_counts().items())
        )
        lines.append(
            f"{PROG}: problems by reason: {reasons}. Offer and link ids are in the log."
        )
    return lines


def _report(result: RunResult, reporter: Reporter) -> None:
    if result.report is None or not result.report.problems:
        return
    try:
        reporter(event_context(result.expired, result.report))
    except Exception as error:  # noqa: BLE001  (the run completed; say only what failed)
        _fail(f"could not send the Sentry summary ({type(error).__name__}).", 0)


def main(
    argv: Sequence[str] | None = None,
    *,
    units: UnitOpener | None = None,
    probe: UrlProbe | None = None,
    reporter: Reporter | None = None,
    now: datetime | None = None,
) -> int:
    """Entry point. The keyword arguments exist for tests; the command line passes none."""
    args = _parse(sys.argv[1:] if argv is None else argv)
    if (problem := _invalid(args)) is not None:
        return _fail(problem, 2)
    engine: AsyncEngine | None = None
    try:
        if units is None:
            from app.core.database import async_session_maker, engine

            units = database_units(async_session_maker)
        result = asyncio.run(_run(units, args, probe, now or datetime.now(UTC), engine))
    except RunFailed as failure:
        return _fail(
            f"failed ({failure.cause}) {failure.stage}. {failure.expired} offer(s) were "
            "already expired before the failure; run it again to continue.",
            1,
        )
    except Exception as error:  # noqa: BLE001  (report the type only, never the message)
        return _fail(f"failed ({type(error).__name__}). Check DATABASE_URL.", 1)
    print("\n".join(_summary_lines(result)))
    _report(result, reporter or report_to_sentry)
    return 0


if __name__ == "__main__":
    from app.core.logging import configure_logging

    configure_logging()
    sys.exit(main())
