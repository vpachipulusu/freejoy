"""The click statements, compiled for Postgres without a database: each carries its own merchant and status predicates.

The admin report scopes by a list of offer ids fetched earlier. The partner statements must not rely on that:
the merchant predicate and the listed-status predicate are in the statement itself, whatever ids are passed.
"""

from datetime import UTC, datetime

from sqlalchemy.dialects import postgresql
from sqlalchemy.sql import Select

from app.partner.clicks_store import (
    LISTED_STATUSES,
    daily_statement,
    offers_statement,
)

MERCHANT = "merchant-under-test"
START = datetime(2026, 3, 1, tzinfo=UTC)
END = datetime(2026, 3, 2, tzinfo=UTC)


def compiled(statement: Select) -> tuple[str, dict[str, object]]:
    result = statement.compile(dialect=postgresql.dialect())
    return str(result), dict(result.params)


def test_the_offers_statement_has_the_merchant_and_status_predicates_in_its_where() -> (
    None
):
    sql, params = compiled(offers_statement(MERCHANT, START, END, 100))

    where = sql.split("WHERE", 1)[1].split("ORDER BY", 1)[0]
    assert "offers.merchant_id = " in where
    assert "offers.status IN " in where
    assert MERCHANT in params.values()
    flat = [
        item for v in params.values() for item in (v if isinstance(v, list) else [v])
    ]
    assert set(LISTED_STATUSES) <= set(flat)


def test_the_daily_statement_has_the_merchant_and_status_predicates_whatever_ids_it_is_given() -> (
    None
):
    sql, params = compiled(
        daily_statement(MERCHANT, ["someone-elses-offer", "a-draft"], START, END)
    )

    where = sql.split("WHERE", 1)[1].split("GROUP BY", 1)[0]
    assert "offers.merchant_id = " in where
    assert "offers.status IN " in where
    assert "click_events.offer_id IN " in where
    assert MERCHANT in params.values()


def test_only_published_and_ended_offers_are_listed() -> None:
    assert LISTED_STATUSES == ("PUBLISHED", "EXPIRED")


def test_the_offers_statement_counts_through_a_correlated_subquery_driven_from_the_offers() -> (
    None
):
    sql, _ = compiled(offers_statement(MERCHANT, START, END, 100))

    assert "FROM offers" in sql
    assert "FROM offers LEFT OUTER JOIN click_events" not in sql
    assert "WHERE click_events.offer_id = offers.id" in sql
    assert "LIMIT " in sql
