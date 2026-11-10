"""Delete old outbound click events.

    uv run python -m commands.prune_click_events            # keep 395 days (13 months)
    uv run python -m commands.prune_click_events --days 90 --batch-size 5000

Deletes every ``click_events`` row whose ``occurred_at`` is older than ``--days`` days and prints how many
went. It deletes in batches of ``--batch-size`` rows (default 10000), committing after each batch, so a
large backlog never holds one huge transaction; if it fails part-way the earlier batches stay deleted and
the error message says how many. ``occurred_at`` is already truncated to the minute, so the cutoff is exact
to the minute. The retention period and the reasoning are in ``docs/developer/freejoy-affiliate.md``; the
privacy page states the same period, so change both together.

Run daily by ``.github/workflows/scheduled-housekeeping.yml`` (and by hand). It uses ``DATABASE_URL`` and is safe to repeat.
"""

import argparse
import asyncio
import sys
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import CursorResult, delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.click_retention import CLICK_RETENTION_DAYS
from app.models.platform.click_event import ClickEvent

DEFAULT_RETENTION_DAYS = CLICK_RETENTION_DAYS
DEFAULT_BATCH_SIZE = 10_000
MIN_VALUE = 1


class PruneFailed(Exception):
    """A batch failed; ``deleted`` rows were already committed by earlier batches."""

    def __init__(self, deleted: int) -> None:
        super().__init__(deleted)
        self.deleted = deleted


async def prune_click_events(
    session: AsyncSession,
    *,
    days: int,
    batch_size: int = DEFAULT_BATCH_SIZE,
    now: datetime | None = None,
) -> int:
    """Delete rows older than ``days`` days in committed batches; return the total.

    Each batch deletes the ids picked by a limited subquery on ``occurred_at`` (indexed), and the loop ends
    on the first batch smaller than ``batch_size``. Raises ``PruneFailed`` with the running total if a batch
    fails.
    """
    cutoff = (now or datetime.now(UTC)) - timedelta(days=days)
    oldest = (
        select(ClickEvent.id)
        .where(ClickEvent.occurred_at < cutoff)
        .limit(batch_size)
        .scalar_subquery()
    )
    total = 0
    while True:
        try:
            result = await session.execute(
                delete(ClickEvent).where(ClickEvent.id.in_(oldest))
            )
            await session.commit()
        except Exception as error:
            raise PruneFailed(total) from error
        batch = int(cast("CursorResult[Any]", result).rowcount)
        total += batch
        if batch < batch_size:
            return total


async def _run(
    maker: async_sessionmaker[AsyncSession],
    days: int,
    batch_size: int,
    engine: AsyncEngine | None = None,
) -> int:
    try:
        async with maker() as session:
            return await prune_click_events(session, days=days, batch_size=batch_size)
    finally:
        if engine is not None:
            await engine.dispose()


def _parse(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="prune-click-events", description=__doc__)
    parser.add_argument(
        "--days",
        type=int,
        default=DEFAULT_RETENTION_DAYS,
        help=f"keep this many days (default {DEFAULT_RETENTION_DAYS})",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"rows per committed batch (default {DEFAULT_BATCH_SIZE})",
    )
    return parser.parse_args(argv)


def _fail(message: str, code: int) -> int:
    print(f"prune-click-events: {message}", file=sys.stderr)
    return code


def main(
    argv: Sequence[str] | None = None,
    *,
    maker: async_sessionmaker[AsyncSession] | None = None,
) -> int:
    """Entry point. ``maker`` exists for tests; the command line passes none."""
    args = _parse(sys.argv[1:] if argv is None else argv)
    if args.days < MIN_VALUE or args.batch_size < MIN_VALUE:
        return _fail(f"--days and --batch-size must be at least {MIN_VALUE}.", 2)
    engine: AsyncEngine | None = None
    try:
        if maker is None:
            from app.core.database import async_session_maker, engine

            maker = async_session_maker
        deleted = asyncio.run(_run(maker, args.days, args.batch_size, engine))
    except PruneFailed as failure:
        # Only the type: a driver message can carry host and user details.
        cause = type(failure.__cause__).__name__
        return _fail(
            f"failed ({cause}). {failure.deleted} click events were already deleted "
            "before the failure; run it again to continue.",
            1,
        )
    except Exception as error:  # noqa: BLE001  (report the type only, never the message)
        return _fail(
            f"failed ({type(error).__name__}) before deleting anything. Check DATABASE_URL.",
            1,
        )
    print(
        f"prune-click-events: deleted {deleted} click events older than {args.days} days."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
