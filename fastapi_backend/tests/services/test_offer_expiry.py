"""``expire_ended_offers``: which offers move to EXPIRED, how they are audited and batched."""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import pytest

from app.models.platform.offer import OfferStatus
from app.repositories.offer_health import DueOffer
from app.services.offer_expiry import (
    AUDIT_ACTION,
    SOURCE,
    ExpiryFailed,
    expire_ended_offers,
)
from tests.fakes_offer_health import FakeAuditor, FakeCommitter, FakeHealthStore

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
P, D, E, A = (
    OfferStatus.PUBLISHED,
    OfferStatus.DRAFT,
    OfferStatus.EXPIRED,
    OfferStatus.ARCHIVED,
)


def offer(offer_id: str, status: OfferStatus, end_at: datetime | None) -> DueOffer:
    return DueOffer(offer_id, status, end_at)


async def run(
    store: FakeHealthStore,
    *,
    auditor: FakeAuditor | None = None,
    batch_size: int = 200,
) -> tuple[int, FakeAuditor, FakeCommitter]:
    auditor = auditor or FakeAuditor()
    committer = FakeCommitter()
    count = await expire_ended_offers(
        store, committer, auditor, NOW, batch_size=batch_size
    )
    return count, auditor, committer


async def test_only_published_offers_whose_end_has_passed_are_expired() -> None:
    store = FakeHealthStore(
        [
            offer("past", P, NOW - timedelta(seconds=1)),
            offer("exactly_now", P, NOW),
            offer("future", P, NOW + timedelta(seconds=1)),
            offer("no_end", P, None),
            offer("draft", D, NOW - timedelta(days=1)),
            offer("already", E, NOW - timedelta(days=1)),
            offer("archived", A, NOW - timedelta(days=1)),
        ]
    )

    count, _, _ = await run(store)

    assert count == 2
    assert store.status == {
        "past": E,
        "exactly_now": E,
        "future": P,
        "no_end": P,
        "draft": D,
        "already": E,
        "archived": A,
    }


async def test_each_expiry_writes_one_system_audit_entry() -> None:
    ended = NOW - timedelta(days=2)
    store = FakeHealthStore([offer("o1", P, ended)])

    _, auditor, committer = await run(store)

    assert auditor.calls == [
        {
            "source": SOURCE,
            "action": "admin.offers.expire",
            "resource": "offers",
            "resource_id": "o1",
            "before": {"status": "PUBLISHED"},
            "after": {"status": "EXPIRED"},
            "details": {"reason": "end_at_passed", "end_at": ended},
        }
    ]
    assert AUDIT_ACTION == "admin.offers.expire"
    assert committer.commits == 1


async def test_a_second_run_changes_nothing_and_commits_nothing() -> None:
    store = FakeHealthStore([offer("o1", P, NOW - timedelta(hours=1))])
    await run(store)

    count, auditor, committer = await run(store)

    assert count == 0
    assert auditor.calls == [] and committer.commits == 0


async def test_offers_are_expired_and_committed_in_batches() -> None:
    ended = NOW - timedelta(days=1)
    store = FakeHealthStore([offer(f"o{n}", P, ended) for n in range(5)])

    count, auditor, committer = await run(store, batch_size=2)

    assert count == 5
    assert [len(batch) for batch in store.expired_batches] == [2, 2, 1]
    assert committer.commits == 3
    assert len(auditor.calls) == 5


async def test_a_run_that_ends_on_a_full_batch_asks_once_more_and_stops() -> None:
    ended = NOW - timedelta(days=1)
    store = FakeHealthStore([offer(f"o{n}", P, ended) for n in range(4)])

    count, _, committer = await run(store, batch_size=2)

    assert count == 4 and committer.commits == 2
    assert len(store.lock_requests) == 3
    assert all(request == (NOW, 2) for request in store.lock_requests)


async def test_a_failure_part_way_reports_what_earlier_batches_already_committed() -> (
    None
):
    ended = NOW - timedelta(days=1)
    store = FakeHealthStore([offer(f"o{n}", P, ended) for n in range(5)])
    auditor = FakeAuditor(fail_on=3)  # the first audit write of the second batch

    with pytest.raises(ExpiryFailed) as raised:
        await run(store, auditor=auditor, batch_size=2)

    assert raised.value.expired == 2
    assert isinstance(raised.value.__cause__, RuntimeError)


async def test_the_failing_batch_is_not_committed() -> None:
    ended = NOW - timedelta(days=1)
    store = FakeHealthStore([offer(f"o{n}", P, ended) for n in range(4)])
    committer = FakeCommitter()

    with pytest.raises(ExpiryFailed):
        await expire_ended_offers(
            store, committer, FakeAuditor(fail_on=3), NOW, batch_size=2
        )

    assert committer.commits == 1


async def test_an_offer_that_is_not_published_is_refused_by_the_lifecycle_rules() -> (
    None
):
    class WrongStore(FakeHealthStore):
        async def lock_due_offers(self, now: datetime, limit: int) -> list[DueOffer]:
            return [offer("o1", D, NOW - timedelta(days=1))]

    committer = FakeCommitter()

    with pytest.raises(ExpiryFailed) as raised:
        await expire_ended_offers(WrongStore(), committer, FakeAuditor(), NOW)

    assert raised.value.expired == 0 and committer.commits == 0


async def test_a_row_count_mismatch_rolls_the_batch_back() -> None:
    class ShortStore(FakeHealthStore):
        async def mark_expired(self, offer_ids: Sequence[str]) -> int:
            await super().mark_expired(offer_ids)
            return len(offer_ids) - 1

    ended = NOW - timedelta(days=1)
    store = ShortStore([offer(f"o{n}", P, ended) for n in range(3)])
    auditor, committer = FakeAuditor(), FakeCommitter()

    with pytest.raises(ExpiryFailed) as raised:
        await expire_ended_offers(store, committer, auditor, NOW)

    assert raised.value.expired == 0
    assert committer.commits == 0 and auditor.calls == []
    assert "mismatch" in str(raised.value.__cause__)
