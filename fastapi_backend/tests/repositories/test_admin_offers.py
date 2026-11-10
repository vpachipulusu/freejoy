"""AdminOfferRepository: the review-queue filter's SQL, via a recording session (no database).

The rows it selects are proved against Postgres in ``test_admin_offers_review_queue_e2e.py``.
"""

from typing import Any

import pytest
from sqlalchemy.dialects import postgresql

from app.models.platform.offer import OfferStatus
from app.repositories.admin_offers import AdminOfferRepository, OfferFilters


class _Result:
    def all(self) -> list[Any]:
        return []


class RecordingSession:
    """Answers every query with no rows and a zero count, keeping the statements."""

    def __init__(self) -> None:
        self.statements: list[Any] = []

    async def execute(self, statement: Any) -> _Result:
        self.statements.append(statement)
        return _Result()

    async def scalar(self, statement: Any) -> int:
        self.statements.append(statement)
        return 0


async def sql_for(filters: OfferFilters) -> tuple[str, str]:
    session = RecordingSession()
    await AdminOfferRepository(session).list_offers(filters, 20, 0)  # type: ignore[arg-type]
    rows, count = (
        str(s.compile(dialect=postgresql.dialect())) for s in session.statements
    )
    return rows, count


async def test_the_list_is_one_query_with_a_left_join_and_one_count():
    rows, count = await sql_for(OfferFilters())

    assert "LEFT OUTER JOIN offer_imports ON offer_imports.offer_id = offers.id" in rows
    assert (
        "offer_imports.provider" in rows and "offer_imports.source_changed_at" in rows
    )
    assert "LEFT OUTER JOIN offer_imports" in count
    assert count.count("SELECT") == 1 and "offer_imports.provider" not in count


async def test_no_review_condition_without_the_filter():
    rows, count = await sql_for(OfferFilters(status=OfferStatus.DRAFT))

    for text in (rows, count):
        assert "source_changed_at IS NOT NULL" not in text.split("WHERE")[1]
        assert "offer_imports.id" not in text.split("WHERE")[1]


@pytest.mark.parametrize("wanted", [True, False])
async def test_the_review_rule_is_defined_once_and_used_by_rows_and_count(wanted):
    rows, count = await sql_for(OfferFilters(needs_review=wanted))

    for text in (rows, count):
        where = text.split("WHERE")[1].split("ORDER BY")[0]
        assert "offer_imports.id IS NOT NULL" in where
        assert "offers.status != " in where
        assert "offer_imports.source_changed_at IS NOT NULL" in where
        assert ("NOT (" in where) is (wanted is False)


async def test_the_review_filter_combines_with_the_other_filters():
    filters = OfferFilters(
        search="sale",
        status=OfferStatus.DRAFT,
        category_id="cat1",
        needs_review=True,
    )

    rows, _ = await sql_for(filters)

    where = rows.split("WHERE")[1]
    for fragment in (
        "ilike",
        "offers.status =",
        "offers.category_id =",
        "offer_imports",
    ):
        assert fragment.lower() in where.lower()
