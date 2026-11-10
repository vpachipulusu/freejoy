"""Delete alert subscribers whose retention has run out.

    uv run python -m commands.purge_subscribers --dry-run    # count only, change nothing
    uv run python -m commands.purge_subscribers              # delete

Run daily by ``.github/workflows/scheduled-housekeeping.yml`` (and by hand). It removes

* PENDING sign-ups whose consent is older than 30 days (nobody ever confirmed), and
* UNSUBSCRIBED rows whose unsubscribe is older than 12 months (the suppression record is no longer needed).

CONFIRMED rows are never touched. For each row that has a provider contact the command first erases the
contact at the provider; if that fails the row is kept and tried again on the next run, so a contact is never
orphaned at the provider with no row pointing to it. Output is counts only, never addresses.
"""

import asyncio
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from app.core.config import settings
from app.core.database import async_session_maker
from app.core.email_provider import (
    EmailProvider,
    EmailProviderError,
    build_email_provider,
)
from app.repositories.subscribers import PurgeCandidate, SubscriberRepository
from app.services.subscribers import PENDING_RETENTION, UNSUBSCRIBED_RETENTION

BATCH_SIZE = 200


class PurgeStore(Protocol):
    async def count_expired(
        self, pending_before: datetime, unsubscribed_before: datetime
    ) -> tuple[int, int]: ...
    async def list_expired(
        self,
        pending_before: datetime,
        unsubscribed_before: datetime,
        after_id: str,
        limit: int,
    ) -> list[PurgeCandidate]: ...
    async def delete_many(
        self,
        subscriber_ids: Sequence[str],
        pending_before: datetime,
        unsubscribed_before: datetime,
    ) -> int: ...


class Committer(Protocol):
    async def commit(self) -> None: ...


@dataclass(frozen=True)
class PurgeSummary:
    pending: int
    unsubscribed: int
    deleted: int
    provider_failures: int
    dry_run: bool


async def purge_subscribers(
    store: PurgeStore,
    session: Committer,
    provider: EmailProvider,
    now: datetime,
    *,
    dry_run: bool,
) -> PurgeSummary:
    pending_before = now - PENDING_RETENTION
    unsubscribed_before = now - UNSUBSCRIBED_RETENTION
    pending, unsubscribed = await store.count_expired(
        pending_before, unsubscribed_before
    )
    if dry_run:
        return PurgeSummary(pending, unsubscribed, 0, 0, dry_run=True)
    deleted = failures = 0
    after_id = ""
    while batch := await store.list_expired(
        pending_before, unsubscribed_before, after_id, BATCH_SIZE
    ):
        after_id = batch[-1].id
        removable: list[str] = []
        for candidate in batch:
            try:
                if candidate.provider_subscriber_id:
                    await provider.delete_contact(
                        candidate.provider_subscriber_id, None
                    )
            except EmailProviderError:
                failures += 1
                continue
            removable.append(candidate.id)
        if removable:
            deleted += await store.delete_many(
                removable, pending_before, unsubscribed_before
            )
            await session.commit()
    return PurgeSummary(pending, unsubscribed, deleted, failures, dry_run=False)


def report(summary: PurgeSummary) -> str:
    verb = "would delete" if summary.dry_run else "deleted"
    count = (
        summary.pending + summary.unsubscribed if summary.dry_run else summary.deleted
    )
    line = (
        f"purge-subscribers: {verb} {count} row(s) "
        f"(stale pending: {summary.pending}, long unsubscribed: {summary.unsubscribed})"
    )
    if summary.provider_failures:
        line += f"; kept {summary.provider_failures} because the provider failed"
    return line


async def _run(dry_run: bool) -> PurgeSummary:
    async with async_session_maker() as session:
        return await purge_subscribers(
            SubscriberRepository(session),
            session,
            build_email_provider(settings),
            datetime.now(UTC),
            dry_run=dry_run,
        )


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args not in ([], ["--dry-run"]):
        print("purge-subscribers: the only option is --dry-run.")
        return 2
    summary = asyncio.run(_run("--dry-run" in args))
    print(report(summary))
    return 1 if summary.provider_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
