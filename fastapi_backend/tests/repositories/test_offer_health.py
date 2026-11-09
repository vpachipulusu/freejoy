"""``OfferHealthRepository``: the SQL it sends and how it shapes rows (no database).

The behaviour against a real Postgres is in ``tests/commands/test_check_offer_health_e2e.py``.
"""

from collections import namedtuple
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from sqlalchemy.dialects import postgresql

from app.models.platform.offer import OfferStatus
from app.repositories.offer_health import (
    DirectLinkTarget,
    DueOffer,
    OfferHealthRepository,
    SourceUrlTarget,
)

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
EARLIER = datetime(2026, 11, 1, tzinfo=UTC)


class RecordingSession:
    def __init__(self, rows: list[Any] | None = None, rowcount: int = 0) -> None:
        self.statements: list[Any] = []
        self._rows = rows or []
        self._rowcount = rowcount

    async def execute(self, statement: Any) -> Any:
        self.statements.append(statement)
        return SimpleNamespace(all=lambda: self._rows, rowcount=self._rowcount)

    def sql(self, index: int = 0) -> tuple[str, dict[str, Any]]:
        compiled = self.statements[index].compile(dialect=postgresql.dialect())
        return str(compiled), compiled.params


def repository(session: RecordingSession) -> OfferHealthRepository:
    return OfferHealthRepository(session)  # type: ignore[arg-type]


async def test_due_offers_are_published_ended_oldest_first_limited_and_skip_locked() -> (
    None
):
    row = namedtuple("Row", "id status end_at")("o1", OfferStatus.PUBLISHED, EARLIER)
    session = RecordingSession([row])

    due = await repository(session).lock_due_offers(NOW, 25)

    sql, params = session.sql()
    assert due == [DueOffer("o1", OfferStatus.PUBLISHED, EARLIER)]
    assert "offers.status =" in sql and "offers.end_at IS NOT NULL" in sql
    assert "offers.end_at <=" in sql
    assert "ORDER BY offers.end_at, offers.id" in sql
    assert "LIMIT" in sql and "FOR UPDATE SKIP LOCKED" in sql
    assert NOW in params.values() and OfferStatus.PUBLISHED in params.values()
    assert 25 in params.values()


async def test_mark_expired_updates_only_still_published_rows() -> None:
    session = RecordingSession(rowcount=2)

    changed = await repository(session).mark_expired(["o1", "o2"])

    sql, params = session.sql()
    assert changed == 2
    assert sql.startswith("UPDATE offers SET status=")
    assert "offers.id IN" in sql and "offers.status =" in sql
    assert OfferStatus.EXPIRED in params.values()
    assert OfferStatus.PUBLISHED in params.values()


async def test_mark_expired_with_no_ids_sends_nothing() -> None:
    session = RecordingSession()

    assert await repository(session).mark_expired([]) == 0
    assert session.statements == []


async def test_source_url_targets_are_live_offers_with_a_url_least_verified_first() -> (
    None
):
    row = namedtuple("Row", "id source_url last_verified_at")(
        "o1", "https://x.example/a", None
    )
    session = RecordingSession([row])

    targets = await repository(session).list_source_url_targets(NOW, 10)

    sql, params = session.sql()
    assert targets == [SourceUrlTarget("o1", "https://x.example/a", None)]
    assert "offers.source_url IS NOT NULL" in sql and "offers.source_url !=" in sql
    assert "offers.end_at IS NULL OR offers.end_at >" in sql
    assert "ORDER BY offers.last_verified_at ASC NULLS FIRST, offers.id" in sql
    assert OfferStatus.PUBLISHED in params.values() and NOW in params.values()


async def test_direct_link_targets_select_only_active_direct_links_of_live_offers() -> (
    None
):
    row = namedtuple(
        "Row", "offer_id id provider destination_url website last_verified_at"
    )("o1", "l1", "direct", "https://shop.example/a", "https://shop.example", EARLIER)
    session = RecordingSession([row])

    targets = await repository(session).list_direct_link_targets(NOW, 10)

    sql, params = session.sql()
    assert targets == [
        DirectLinkTarget(
            "o1",
            "l1",
            "direct",
            "https://shop.example/a",
            "https://shop.example",
            EARLIER,
        )
    ]
    assert "affiliate_links.provider =" in sql and "direct" in params.values()
    assert "affiliate_links.active IS true" in sql
    assert "JOIN merchants" in sql and "JOIN offers" in sql
    assert "offers.end_at IS NULL OR offers.end_at >" in sql
    assert OfferStatus.PUBLISHED in params.values()
