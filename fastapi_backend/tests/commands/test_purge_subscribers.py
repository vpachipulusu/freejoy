"""The retention command: what it selects, the dry run, provider failures and the CLI wrapper."""

from collections.abc import Sequence
from datetime import datetime, timedelta

import pytest

from app.core.email_provider import EmailProviderError, FakeEmailProvider
from app.models.platform.email_subscriber import SubscriberStatus
from app.repositories.subscribers import PurgeCandidate
from app.services.subscribers import PENDING_RETENTION, UNSUBSCRIBED_RETENTION
from commands import purge_subscribers as command
from commands.purge_subscribers import PurgeSummary, purge_subscribers, report
from tests.fakes_subscribers import NOW, FakeCommitter


class FakePurgeStore:
    def __init__(self, rows: list[PurgeCandidate], pending: int, unsubscribed: int):
        self.rows = rows
        self.counts = (pending, unsubscribed)
        self.deleted: list[str] = []
        self.cutoffs: list[tuple[datetime, datetime]] = []

    async def count_expired(
        self, pending_before: datetime, unsubscribed_before: datetime
    ) -> tuple[int, int]:
        self.cutoffs.append((pending_before, unsubscribed_before))
        return self.counts

    async def list_expired(
        self,
        pending_before: datetime,
        unsubscribed_before: datetime,
        after_id: str,
        limit: int,
    ) -> list[PurgeCandidate]:
        return [r for r in self.rows if r.id > after_id and r.id not in self.deleted][
            :limit
        ]

    async def delete_many(
        self,
        subscriber_ids: Sequence[str],
        pending_before: datetime,
        unsubscribed_before: datetime,
    ) -> int:
        self.deleted.extend(subscriber_ids)
        return len(subscriber_ids)


def candidate(subscriber_id: str, provider_id: str | None = None) -> PurgeCandidate:
    return PurgeCandidate(subscriber_id, SubscriberStatus.UNSUBSCRIBED, provider_id)


async def test_dry_run_counts_and_changes_nothing() -> None:
    store = FakePurgeStore([candidate("a", "p1")], pending=2, unsubscribed=1)
    provider = FakeEmailProvider()
    committer = FakeCommitter()

    summary = await purge_subscribers(store, committer, provider, NOW, dry_run=True)

    assert summary == PurgeSummary(2, 1, 0, 0, dry_run=True)
    assert store.deleted == []
    assert provider.deleted == []
    assert committer.commits == 0


async def test_cutoffs_are_30_days_for_pending_and_12_months_for_unsubscribed() -> None:
    store = FakePurgeStore([], pending=0, unsubscribed=0)

    await purge_subscribers(
        store, FakeCommitter(), FakeEmailProvider(), NOW, dry_run=True
    )

    assert store.cutoffs == [(NOW - PENDING_RETENTION, NOW - UNSUBSCRIBED_RETENTION)]
    assert PENDING_RETENTION == timedelta(days=30)
    assert UNSUBSCRIBED_RETENTION == timedelta(days=365)


async def test_rows_are_deleted_after_their_provider_contact() -> None:
    store = FakePurgeStore(
        [candidate("a", "p1"), candidate("b"), candidate("c", "p3")], 1, 2
    )
    provider = FakeEmailProvider()
    committer = FakeCommitter()

    summary = await purge_subscribers(store, committer, provider, NOW, dry_run=False)

    assert store.deleted == ["a", "b", "c"]
    assert provider.deleted == [("p1", None), ("p3", None)]
    assert summary.deleted == 3
    assert committer.commits == 1


async def test_a_row_whose_provider_contact_cannot_be_erased_is_kept() -> None:
    store = FakePurgeStore([candidate("a", "p1"), candidate("b")], 0, 2)
    provider = FakeEmailProvider()
    provider.error = EmailProviderError("delete", 500)

    summary = await purge_subscribers(
        store, FakeCommitter(), provider, NOW, dry_run=False
    )

    assert store.deleted == ["b"]
    assert (summary.deleted, summary.provider_failures) == (1, 1)


async def test_a_batch_where_everything_fails_does_not_loop_forever() -> None:
    store = FakePurgeStore([candidate("a", "p1")], 0, 1)
    provider = FakeEmailProvider()
    provider.error = EmailProviderError("delete", 500)

    summary = await purge_subscribers(
        store, FakeCommitter(), provider, NOW, dry_run=False
    )

    assert summary.provider_failures == 1
    assert store.deleted == []


def test_report_never_mentions_an_address() -> None:
    line = report(PurgeSummary(2, 1, 0, 0, dry_run=True))

    assert line == (
        "purge-subscribers: would delete 3 row(s) "
        "(stale pending: 2, long unsubscribed: 1)"
    )
    assert "kept 2" in report(PurgeSummary(0, 2, 0, 2, dry_run=False))
    assert report(PurgeSummary(1, 1, 2, 0, dry_run=False)).startswith(
        "purge-subscribers: deleted 2 row(s)"
    )


def test_main_rejects_unknown_arguments(capsys: pytest.CaptureFixture[str]) -> None:
    assert command.main(["--force"]) == 2
    assert "--dry-run" in capsys.readouterr().out


def test_main_database_failure_exits_1_and_prints_only_the_error_type(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def failing_run(dry_run: bool) -> PurgeSummary:
        raise ConnectionRefusedError("role app_purge at db.internal:5432 hunter2")

    monkeypatch.setattr(command, "_run", failing_run)

    code = command.main([])

    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == ""
    assert captured.err == (
        "purge-subscribers: failed (ConnectionRefusedError). Check DATABASE_URL.\n"
    )


@pytest.mark.parametrize(
    ("failures", "expected"), [(0, 0), (2, 1)], ids=["clean", "provider-failure"]
)
def test_main_exits_non_zero_when_a_provider_call_failed(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failures: int,
    expected: int,
) -> None:
    async def fake_run(dry_run: bool) -> PurgeSummary:
        return PurgeSummary(0, 0, 0, failures, dry_run=dry_run)

    monkeypatch.setattr(command, "_run", fake_run)

    assert command.main(["--dry-run"]) == expected
    assert "would delete" in capsys.readouterr().out
