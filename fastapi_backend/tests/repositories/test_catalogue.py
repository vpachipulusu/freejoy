"""CatalogueRepository: the SQL it builds, via a recording session from the provider seam."""

from dataclasses import asdict
from typing import Any

import pytest
from sqlalchemy.dialects import postgresql

from app.models.platform.offer import OfferStatus, OfferType
from app.repositories.catalogue import (
    MAX_PAGE_SIZE,
    CatalogueRepository,
    OfferFilters,
    OfferSort,
    escape_like,
)
from tests.fakes import FakeSessionProvider
from tests.fakes_catalogue import NOW, RowsSession, make_category, make_offer

# created_at is deliberately absent: the start date falls back to it inside coalesce().
FORBIDDEN_COLUMNS = (
    "publisher_notes",
    "destination_url",
    "tracking_id",
    "source_url",
    "website",
    "updated_at",
)


def compiled(statement: Any) -> Any:
    return statement.compile(dialect=postgresql.dialect())


async def list_with(
    filters: OfferFilters, limit: int = 12, offset: int = 0
) -> tuple[RowsSession, int, list[Any]]:
    session = RowsSession(([asdict(make_offer())], 1), ([asdict(make_offer())], 1))
    provider = FakeSessionProvider(session)
    async with provider.session() as active:
        total, items = await CatalogueRepository(active).list_live_offers(
            filters, NOW, limit, offset
        )
    return session, total, items


def _where(result: tuple[RowsSession, int, list[Any]]) -> str:
    return str(result[0].executed[1].compile()).split("WHERE")[1].split("ORDER BY")[0]


@pytest.mark.parametrize(
    ("raw", "escaped"),
    [
        ("plain", "plain"),
        ("100%", "100\\%"),
        ("a_b", "a\\_b"),
        ("back\\slash", "back\\\\slash"),
        ("%_\\", "\\%\\_\\\\"),
    ],
)
def test_escape_like_neutralises_wildcards(raw: str, escaped: str) -> None:
    assert escape_like(raw) == escaped


async def test_list_returns_total_and_records() -> None:
    _, total, items = await list_with(OfferFilters())

    assert total == 1
    assert [item.slug for item in items] == ["free-shampoo"]


async def test_search_term_is_a_bound_escaped_pattern_on_title_summary_and_merchant() -> (
    None
):
    session, _, _ = await list_with(OfferFilters(query="50%_off"))

    statement = compiled(session.executed[1])
    assert r"%50\%\_off%" in statement.params.values()
    sql = str(statement)
    assert sql.count("ILIKE") == 3
    assert "ESCAPE" in sql
    assert "50%_off" not in sql


async def test_values_are_bound_parameters_never_inlined() -> None:
    session, _, _ = await list_with(
        OfferFilters(query="x'; DROP TABLE offers;--", category_slug="a' OR '1'='1")
    )

    sql = str(compiled(session.executed[1]))
    assert "DROP TABLE" not in sql
    assert "OR '1'" not in sql


@pytest.mark.parametrize(
    ("requested", "expected"), [(500, MAX_PAGE_SIZE), (50, 50), (0, 1), (-3, 1)]
)
async def test_limit_is_clamped_to_the_page_bounds(
    requested: int, expected: int
) -> None:
    session, _, _ = await list_with(OfferFilters(), limit=requested)

    params = list(compiled(session.executed[1]).params.values())
    assert params[-2:] == [expected, 0]


async def test_negative_offset_is_clamped_to_zero() -> None:
    session, _, _ = await list_with(OfferFilters(), offset=-5)

    assert 0 in compiled(session.executed[1]).params.values()


async def test_filters_become_conditions_only_when_set() -> None:
    open_sql = _where(await list_with(OfferFilters()))
    filtered = OfferFilters(
        category_slug="beauty",
        offer_types=(OfferType.CASHBACK,),
        free_postage_only=True,
        featured=True,
    )
    filtered_sql = _where(await list_with(filtered))

    for fragment in (
        "categories.slug =",
        "offer_type IN",
        "postage_kind =",
        "featured",
    ):
        assert fragment not in open_sql
        assert fragment in filtered_sql


@pytest.mark.parametrize(
    ("sort", "fragment"),
    [
        (OfferSort.NEWEST, "coalesce(offers.start_at, offers.created_at) DESC"),
        (OfferSort.ENDING_SOON, "offers.end_at ASC NULLS LAST"),
        (OfferSort.A_Z, "lower(offers.title),"),
    ],
)
async def test_sort_orders_end_on_the_id_for_stable_pages(
    sort: OfferSort, fragment: str
) -> None:
    session, _, _ = await list_with(OfferFilters(sort=sort))

    order_by = str(session.executed[1].compile()).split("ORDER BY")[1]
    assert fragment in order_by
    assert "offers.id" in order_by


async def test_live_rule_visibility_and_expiry_are_always_applied() -> None:
    session, _, _ = await list_with(OfferFilters())

    for statement in session.executed:
        sql = str(statement.compile())
        assert "offers.status =" in sql
        assert "offers.end_at IS NULL OR offers.end_at >" in sql
        assert "categories.enabled IS true" in sql
        assert "merchants.active IS true" in sql


async def test_no_statement_selects_editorial_only_columns() -> None:
    session, _, _ = await list_with(OfferFilters())
    detail = RowsSession(([], 0))
    async with FakeSessionProvider(detail).session() as active:
        await CatalogueRepository(active).find_visible_offer("x")
    categories = RowsSession(([], 0))
    async with FakeSessionProvider(categories).session() as active:
        await CatalogueRepository(active).list_enabled_categories(NOW)

    statements = [*session.executed, *detail.executed, *categories.executed]
    assert len(statements) == 4
    for statement in statements:
        sql = str(statement.compile())
        for column in FORBIDDEN_COLUMNS:
            assert column not in sql


async def test_expiry_boundary_is_strict_and_status_is_published() -> None:
    session, _, _ = await list_with(OfferFilters())

    for statement in session.executed:
        built = compiled(statement)
        sql = str(built)
        assert "offers.end_at > %(end_at_1)s" in sql
        assert "offers.end_at >=" not in sql
        assert built.params["end_at_1"] == NOW
        assert OfferStatus.PUBLISHED in built.params.values()


async def test_category_counts_apply_the_live_rule_and_merchant_activity() -> None:
    session = RowsSession(([], 0))
    async with FakeSessionProvider(session).session() as active:
        await CatalogueRepository(active).list_enabled_categories(NOW)

    built = compiled(session.executed[0])
    sql = str(built)
    assert "offers.status =" in sql
    assert "offers.end_at IS NULL OR offers.end_at > %(end_at_1)s" in sql
    assert "merchants.active IS true" in sql
    assert "offers.end_at >=" not in sql
    assert built.params["end_at_1"] == NOW
    assert OfferStatus.PUBLISHED in built.params.values()


async def test_find_visible_offer_is_none_when_nothing_matches() -> None:
    session = RowsSession(([], 0))
    async with FakeSessionProvider(session).session() as active:
        assert await CatalogueRepository(active).find_visible_offer("x") is None


async def test_find_visible_offer_does_not_filter_on_status() -> None:
    session = RowsSession(([asdict(make_offer())], 0))
    async with FakeSessionProvider(session).session() as active:
        found = await CatalogueRepository(active).find_visible_offer("free-shampoo")

    assert found is not None
    sql = str(session.executed[0].compile())
    assert "offers.status =" not in sql
    assert "offers.slug =" in sql
    assert "categories.enabled IS true" in sql
    assert "merchants.active IS true" in sql


async def test_categories_are_enabled_only_in_display_order() -> None:
    row = {key: value for key, value in asdict(make_category()).items()}
    session = RowsSession(([row], 0))
    async with FakeSessionProvider(session).session() as active:
        categories = await CatalogueRepository(active).list_enabled_categories(NOW)

    assert [c.offer_count for c in categories] == [3]
    sql = str(session.executed[0].compile())
    assert "categories.enabled IS true" in sql
    assert "ORDER BY categories.sort_order, categories.name, categories.id" in sql


def test_nothing_that_filters_or_orders_the_public_list_uses_the_commercial_basis() -> (
    None
):
    """The public disclosure says sponsorship does not change the order of results: no sort or filter reads it."""
    from sqlalchemy import select

    from app.models.platform.offer import Offer
    from app.repositories.catalogue import _filter_conditions, _order_by

    everything = OfferFilters(
        query="tea",
        category_slug="beauty",
        offer_types=(OfferType.FREE_SAMPLE,),
        free_postage_only=True,
        featured=True,
    )
    clauses = [*_filter_conditions(everything)]
    for sort in OfferSort:
        clauses.extend(_order_by(sort))

    sql = str(
        select(Offer.id)
        .where(*_filter_conditions(everything))
        .order_by(*[c for sort in OfferSort for c in _order_by(sort)])
        .compile(dialect=postgresql.dialect())
    )

    assert clauses and "commercial_basis" not in sql
