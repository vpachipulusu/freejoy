"""The review queue filter against a real Postgres: every kind of offer, every combination of filters.

Runs only with ``TEST_DATABASE_URL`` (see ``tests/catalogue_e2e_support.py``). The rule under test is
``needs_review`` in ``app/repositories/admin_offers.py``: an imported offer (an ``offer_imports`` row) that is
a draft or whose ``source_changed_at`` is set, and is not archived.
"""

from collections.abc import AsyncIterator

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

import tests.catalogue_e2e_support as support
from app.core.ids import new_id
from app.models.platform.offer import OfferStatus
from app.repositories.admin_offers import AdminOfferRepository, OfferFilters
from tests.catalogue_e2e_support import EDITOR, as_user, offer_body, seed_admin

pytestmark = support.requires_database
database_url, engine, client = support.database_url, support.engine, support.client

HASH = "a" * 64
# name: (status, imported, source moved on)
KINDS: dict[str, tuple[str, bool, bool]] = {
    "imported draft": ("DRAFT", True, False),
    "imported draft marked": ("DRAFT", True, True),
    "imported published": ("PUBLISHED", True, False),
    "imported published marked": ("PUBLISHED", True, True),
    "imported expired": ("EXPIRED", True, False),
    "imported expired marked": ("EXPIRED", True, True),
    "imported archived": ("ARCHIVED", True, False),
    "imported archived marked": ("ARCHIVED", True, True),
    "hand draft": ("DRAFT", False, False),
    "hand published": ("PUBLISHED", False, False),
    "hand archived": ("ARCHIVED", False, False),
}
IN_QUEUE = {
    "imported draft",
    "imported draft marked",
    "imported published marked",
    "imported expired marked",
}
SET_STATUS = (
    "UPDATE offers SET status = :s, "
    "last_verified_at = CASE WHEN :verified THEN now() END WHERE id = :id"
)
INSERT_IMPORT = (
    "INSERT INTO offer_imports (id, provider, external_id, offer_id, source_hash,"
    " applied_source_hash, applied_content_hash, applied_at, source_changed_at)"
    " VALUES (:id, 'awin', :ext, :offer, :h, :h, :h, now(),"
    " CASE WHEN :marked THEN now() END)"
)


@pytest.fixture
async def world(engine: AsyncEngine, client) -> dict[str, str]:
    """One offer of every kind (in category one); returns name -> offer id."""
    await seed_admin(engine, "editor", EDITOR)
    headers = as_user("editor")
    category_id, merchant_id = await support.create_references(client, headers)
    ids: dict[str, str] = {}
    for position, (name, (status, imported, marked)) in enumerate(KINDS.items()):
        created = await client.post(
            "/v1/admin/offers",
            json=offer_body(category_id, merchant_id, title=f"Kind {name}"),
            headers=headers,
        )
        offer_id = created.json()["data"]["id"]
        ids[name] = offer_id
        async with engine.begin() as connection:
            await connection.execute(
                text(SET_STATUS),
                {
                    "s": status,
                    "verified": status in {"PUBLISHED", "EXPIRED"},
                    "id": offer_id,
                },
            )
            if imported:
                await connection.execute(
                    text(INSERT_IMPORT),
                    {
                        "id": new_id(),
                        "ext": str(position),
                        "offer": offer_id,
                        "h": HASH,
                        "marked": marked,
                    },
                )
    return ids


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with async_sessionmaker(engine, expire_on_commit=False)() as opened:
        yield opened


async def listed(session: AsyncSession, filters: OfferFilters):
    page = await AdminOfferRepository(session).list_offers(filters, 100, 0)
    return page, {item.id for item in page.offers}


def named(ids: dict[str, str], *names: str) -> set[str]:
    return {ids[name] for name in names}


async def test_the_queue_is_imported_drafts_and_imported_offers_whose_source_moved(
    session, world
):
    page, found = await listed(session, OfferFilters(needs_review=True))

    assert found == named(world, *IN_QUEUE)
    assert page.total_count == len(IN_QUEUE)


async def test_not_in_the_queue_is_everything_else_including_hand_made_and_archived(
    session, world
):
    page, found = await listed(session, OfferFilters(needs_review=False))

    assert found == named(world, *(set(KINDS) - IN_QUEUE))
    assert page.total_count == len(KINDS) - len(IN_QUEUE)


async def test_without_the_filter_every_offer_is_listed_and_the_two_halves_partition_it(
    session, world
):
    page, found = await listed(session, OfferFilters())
    _, queue = await listed(session, OfferFilters(needs_review=True))
    _, rest = await listed(session, OfferFilters(needs_review=False))

    assert found == set(world.values()) and page.total_count == len(KINDS)
    assert queue | rest == found and queue & rest == set()


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (OfferStatus.DRAFT, {"imported draft", "imported draft marked"}),
        (OfferStatus.PUBLISHED, {"imported published marked"}),
        (OfferStatus.EXPIRED, {"imported expired marked"}),
        (OfferStatus.ARCHIVED, set()),
    ],
)
async def test_the_queue_combines_with_a_status(session, world, status, expected):
    page, found = await listed(session, OfferFilters(status=status, needs_review=True))

    assert found == named(world, *expected) and page.total_count == len(expected)


async def test_the_queue_combines_with_search_and_category(session, world, engine):
    other = new_id()
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO categories (id, name, slug, short_name, description, intro, tint)"
                " VALUES (:id, 'Other', 'other', 'Other', 'd', 'i', 'sage')"
            ),
            {"id": other},
        )
        await connection.execute(
            text("UPDATE offers SET category_id = :c WHERE id = :id"),
            {"c": other, "id": world["imported draft marked"]},
        )

    _, searched = await listed(
        session, OfferFilters(search="published marked", needs_review=True)
    )
    _, in_other = await listed(
        session, OfferFilters(category_id=other, needs_review=True)
    )
    _, rest_in_other = await listed(
        session, OfferFilters(category_id=other, needs_review=False)
    )
    _, everything = await listed(
        session,
        OfferFilters(
            search="Kind",
            status=OfferStatus.DRAFT,
            category_id=other,
            needs_review=True,
        ),
    )

    assert searched == named(world, "imported published marked")
    assert in_other == named(world, "imported draft marked")
    assert rest_in_other == set()
    assert everything == named(world, "imported draft marked")


async def test_rows_carry_their_provenance_from_the_same_query_with_no_per_row_lookups(
    session, world, engine
):
    statements: list[str] = []

    def record(conn, cursor, statement, *rest):
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", record)
    try:
        page, _ = await listed(session, OfferFilters())
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", record)

    imported = {world[name] for name, (_, yes, _) in KINDS.items() if yes}
    assert set(page.provenance) == imported
    assert len(statements) == 2  # the page and the count, whatever the page size
    marked = {
        offer_id
        for offer_id, provenance in page.provenance.items()
        if provenance.source_changed_at is not None
    }
    assert marked == named(world, *(n for n, (_, _, m) in KINDS.items() if m))
    assert {p.provider for p in page.provenance.values()} == {"awin"}


async def test_the_page_window_and_total_count_ignore_the_join(session, world):
    repository = AdminOfferRepository(session)

    first = await repository.list_offers(OfferFilters(needs_review=True), 2, 0)
    second = await repository.list_offers(OfferFilters(needs_review=True), 2, 2)

    assert first.total_count == second.total_count == len(IN_QUEUE)
    assert len(first.offers) == len(second.offers) == 2
    assert {i.id for i in first.offers}.isdisjoint({i.id for i in second.offers})


async def test_the_http_list_filters_and_names_the_import(client, world):
    headers = as_user("editor")

    queue = await client.get("/v1/admin/offers?needs_review=true", headers=headers)
    rest = await client.get("/v1/admin/offers?needs_review=false", headers=headers)

    rows = queue.json()["data"]["offers"]
    assert {r["id"] for r in rows} == named(world, *IN_QUEUE)
    assert all(r["import_provider"] == "awin" for r in rows)
    by_title = {r["title"]: r for r in rows}
    assert by_title["Kind imported draft"]["source_changed_at"] is None
    assert by_title["Kind imported published marked"]["source_changed_at"] is not None
    assert queue.json()["data"]["total_count"] == len(IN_QUEUE)
    rest_rows = rest.json()["data"]["offers"]
    assert {r["id"] for r in rest_rows} == named(world, *(set(KINDS) - IN_QUEUE))
    hand = next(r for r in rest_rows if r["title"] == "Kind hand draft")
    assert hand["import_provider"] is None and hand["source_changed_at"] is None
