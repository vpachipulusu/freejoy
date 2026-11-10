"""One import run: fetch pages of promotions, validate them, write them in committed batches.

``run_import`` is the whole loop and the only thing the command calls. For each page it asks the client
for up to ``page_size`` items, treats every item as untrusted (``parse_promotion``), drops duplicates, and
hands the valid ones to ``OfferImporter`` in batches of ``BATCH_SIZE``, each in its own database unit that
commits once. A failure stops the run with ``ImportFailed`` carrying the report so far: batches already
committed stay committed and the next run carries on (the import is idempotent). With ``dry_run`` nothing
is opened or written: the items are fetched and validated and counted.

How the run ends, since Awin does not document the last page:

* the page holds fewer items than were asked for (an empty page included), or the response said how many
  pages exist and this was the last one: the feed is finished;
* ``--limit`` items were received: stop, ``stopped_early = "limit"``;
* ``--max-pages`` pages were fetched and the last was full: stop, ``stopped_early = "max_pages"``;
* a page whose promotion ids were all seen on earlier pages is not trusted (the pagination is not doing
  what it was assumed to): the run fails with ``pagination_repeat``.

Nothing here logs or returns item text: a skipped item is logged as its promotion id (an integer) and a
reason code.
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from typing import Any

from app.core.awin_client import (
    MAX_PAGE_SIZE,
    MIN_PAGE_SIZE,
    AwinClient,
    AwinError,
    Membership,
    PromotionFilters,
    PromotionsPage,
)
from app.services.awin_offer_import import (
    Committer,
    ImportAborted,
    ImportStore,
    ImportTally,
    OfferImporter,
    SystemAuditor,
)
from app.services.awin_promotions import (
    MAX_ID,
    Promotion,
    Rejected,
    SkipReason,
    parse_promotion,
)

BATCH_SIZE = 50
DEFAULT_LIMIT, MAX_LIMIT = 500, 5000
DEFAULT_MAX_PAGES, MAX_MAX_PAGES = 10, 50
DEFAULT_MAX_SECONDS, MAX_MAX_SECONDS = (
    1200,
    86_400,
)  # the whole run, 20 minutes by default
MAX_LOGGED_SKIPS = 100
RUN_DEADLINE = "run_deadline"
MEMBERSHIP_NOT_JOINED = "membership_not_joined"
LOCK_NOT_AVAILABLE = "55P03"  # SQLSTATE: lock_timeout expired
STAGE_FETCH = "while fetching promotions"
STAGE_BEFORE_FETCH = "before the first fetch"
STAGE_WRITE = "while writing offers"
logger = logging.getLogger("freejoy.awin_import")


@dataclass(frozen=True)
class Unit:
    """One database scope: the importer's store, the session that commits, the audit writer."""

    store: ImportStore
    session: Committer
    auditor: SystemAuditor


UnitOpener = Callable[[], AbstractAsyncContextManager[Unit]]


@dataclass(frozen=True)
class RunOptions:
    publisher_id: int  # ours: a tracking link must carry it
    dry_run: bool = False
    limit: int = DEFAULT_LIMIT
    max_pages: int = DEFAULT_MAX_PAGES
    updated_since: date | None = None
    max_seconds: float = DEFAULT_MAX_SECONDS  # the whole run, fetching and writing
    membership: Membership = "joined"  # anything else is for a dry run only


def filters_for(options: RunOptions) -> PromotionFilters:
    """The one place the request filters are built; the dry-run summary prints these same filters."""
    return PromotionFilters(options.updated_since, options.membership)


@dataclass(frozen=True)
class RunReport:
    tally: ImportTally = field(default_factory=ImportTally)
    valid: int = 0  # items that passed validation: written, or (dry run) that would be
    pages: int = 0
    received: int = 0
    stopped_early: str | None = None  # "limit" or "max_pages": more may exist


class ImportFailed(Exception):
    """The run stopped. ``report`` is what was done (committed) before; ``cause`` is a code or type name."""

    def __init__(self, report: RunReport, stage: str, cause: str) -> None:
        super().__init__(stage, cause)
        self.report, self.stage, self.cause = report, stage, cause


@dataclass(frozen=True)
class _Progress:
    report: RunReport
    stage: str


def _raw_id(item: object) -> int | None:
    """The promotion id of a raw item when it is an integer in the accepted range, else ``None`` (safe to log).

    An out-of-range id is still rejected by ``parse_promotion`` (``invalid_id``); it is only never logged.
    """
    if not isinstance(item, dict):
        return None
    value = item.get("promotionId")
    ok = isinstance(value, int) and not isinstance(value, bool) and 0 < value <= MAX_ID
    return value if ok else None


def _parse_page(
    items: Sequence[Any],
    seen: set[int],
    now: datetime,
    logged: int,
    publisher_id: int,
) -> tuple[list[Promotion], ImportTally]:
    promotions: list[Promotion] = []
    tally = ImportTally()
    for item in items:
        pid = _raw_id(item)
        outcome = (
            Rejected(SkipReason.DUPLICATE_IN_FEED)
            if pid is not None and pid in seen
            else parse_promotion(item, now, publisher_id)
        )
        if pid is not None:
            seen.add(pid)
        if isinstance(outcome, Rejected):
            tally = tally.skip(outcome.reason)
            if logged + tally.skipped_total <= MAX_LOGGED_SKIPS:
                logger.warning(
                    "awin item skipped: promotion_id=%s reason=%s",
                    pid if pid is not None else "-",
                    outcome.reason.value,
                )
        else:
            promotions.append(outcome)
    return promotions, tally


def _check_not_a_repeat(items: Sequence[Any], seen: set[int]) -> None:
    ids = [pid for pid in map(_raw_id, items) if pid is not None]
    if ids and all(pid in seen for pid in ids):
        raise ImportAborted("pagination_repeat")


def _stop_reason(
    page: PromotionsPage,
    page_no: int,
    page_size: int,
    received: int,
    options: RunOptions,
    *,
    truncated: bool,
) -> tuple[bool, str | None]:
    """Whether to stop after this page, and the ``stopped_early`` note when more may exist."""
    if (
        truncated
    ):  # items past --limit were dropped, so the feed was not read to its end
        return True, "limit"
    if not page.items:
        return True, None
    if (
        page.total_pages is not None
    ):  # the response said how many pages exist: believe it
        finished = page_no >= page.total_pages
    else:
        finished = len(page.items) < page_size
    if finished:
        return True, None
    if received >= options.limit:
        return True, "limit"
    if page_no >= options.max_pages:
        return True, "max_pages"
    return False, None


async def _write_page(
    units: UnitOpener,
    promotions: Sequence[Promotion],
    report: RunReport,
    now: datetime,
) -> AsyncIterator[RunReport]:
    for start in range(0, len(promotions), BATCH_SIZE):
        batch = promotions[start : start + BATCH_SIZE]
        async with units() as unit:
            importer = OfferImporter(
                unit.store, unit.session, unit.auditor, lambda: now
            )
            written = await importer.import_batch(batch)
            # Counted at once, with nothing awaited in between, and handed over while the unit is still
            # open: a deadline that cancels the session's close cannot lose a batch that was committed.
            # Only a cancellation inside commit() itself can: the failure line says "at least".
            report = replace(report, tally=report.tally.merged(written))
            yield report


async def _progress(
    client: AwinClient,
    units: UnitOpener | None,
    options: RunOptions,
    now: datetime,
) -> AsyncIterator[_Progress]:
    page_size = max(MIN_PAGE_SIZE, min(MAX_PAGE_SIZE, options.limit))
    filters = filters_for(options)
    report, seen = RunReport(), set[int]()
    for page_no in range(1, options.max_pages + 1):
        page = await client.fetch_promotions(filters, page=page_no, page_size=page_size)
        _check_not_a_repeat(page.items, seen)
        items = page.items[: options.limit - report.received]
        promotions, skipped = _parse_page(
            items, seen, now, report.tally.skipped_total, options.publisher_id
        )
        report = replace(
            report,
            pages=page_no,
            received=report.received + len(items),
            valid=report.valid + len(promotions),
            tally=report.tally.merged(skipped),
        )
        yield _Progress(report, STAGE_WRITE)
        if units is not None and not options.dry_run:
            async for written in _write_page(units, promotions, report, now):
                report = written
                yield _Progress(report, STAGE_WRITE)
        stop, early = _stop_reason(
            page,
            page_no,
            page_size,
            report.received,
            options,
            truncated=len(items) < len(page.items),
        )
        report = replace(report, stopped_early=early)
        yield _Progress(report, STAGE_FETCH)
        if stop:
            return


def failure_cause(error: Exception) -> str:
    """``lock_timeout`` for a database lock wait that ran out, else the exception type name."""
    wrapped = getattr(error, "orig", None)
    for candidate in (wrapped, getattr(wrapped, "__cause__", None)):
        if getattr(candidate, "sqlstate", None) == LOCK_NOT_AVAILABLE:
            return "lock_timeout"
    return type(error).__name__


async def run_import(
    client: AwinClient,
    units: UnitOpener | None,
    options: RunOptions,
    now: datetime,
) -> RunReport:
    """Run the import; ``units`` may be ``None`` only for a dry run. Raises ``ImportFailed``."""
    if options.membership != "joined" and not options.dry_run:
        # Tracking links only work for joined programmes: importing others must be impossible.
        raise ImportFailed(RunReport(), STAGE_BEFORE_FETCH, MEMBERSHIP_NOT_JOINED)
    progress = _Progress(RunReport(), STAGE_FETCH)
    deadline = asyncio.timeout(options.max_seconds)
    try:
        async with deadline:
            async for progress in _progress(client, units, options, now):
                pass
    except AwinError as error:
        raise ImportFailed(progress.report, STAGE_FETCH, error.reason.value) from None
    except ImportAborted as error:
        raise ImportFailed(progress.report, progress.stage, error.reason) from None
    except Exception as error:  # noqa: BLE001  (the type is reported, never the message)
        cause = RUN_DEADLINE if deadline.expired() else failure_cause(error)
        raise ImportFailed(progress.report, progress.stage, cause) from None
    return progress.report
