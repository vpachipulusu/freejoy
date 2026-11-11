"""End to end: the public catalogue on a real Postgres.

Runs only when ``TEST_DATABASE_URL`` points at a throwaway local database whose name ends
in "_test" (same contract as the other ``*_e2e.py`` suites). The routes, service, repository
and SQL are the production code. It proves which offers are visible (published, not past
their end date, enabled category, active merchant), every filter and sort, search escaping,
the 404 and 410 paths, category counts, and that editorial-only data never leaves the API.
"""

import os
import subprocess
import sys
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.api.deps import get_session_provider
from app.api.v1.catalogue import get_catalogue_service
from app.core.database import DefaultSessionProvider, build_engine
from app.core.ids import new_id
from app.main import create_app
from app.repositories.catalogue import CatalogueRepository
from app.services.catalogue import CatalogueService

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
BACKEND_ROOT = Path(__file__).resolve().parents[2]
KEY = "test-only-internal-service-key-not-for-deployment"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
SECRET_NOTE = "internal-only publisher note 7f3a"

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="TEST_DATABASE_URL is not set"
)

DAY = timedelta(days=1)
JSON_COLUMNS = {"description", "steps"}


def _migrate(url: str, *args: str) -> None:
    subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_ROOT,
        env={**os.environ, "DATABASE_URL_DIRECT": url},
        check=True,
        capture_output=True,
    )


@pytest.fixture(scope="module")
def database_url() -> Iterator[str]:
    assert TEST_DATABASE_URL is not None
    target = make_url(TEST_DATABASE_URL)
    assert (target.database or "").endswith("_test"), (
        "refusing to rebuild a database whose name does not end in _test"
    )
    assert target.host in LOCAL_HOSTS, "refusing to rebuild a non-local database"
    _migrate(TEST_DATABASE_URL, "downgrade", "base")
    _migrate(TEST_DATABASE_URL, "upgrade", "head")
    yield TEST_DATABASE_URL


@pytest.fixture
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    built = build_engine(database_url)
    async with built.begin() as connection:
        for table in ("click_events", "affiliate_links", "offers"):
            await connection.execute(text(f"DELETE FROM {table}"))
        await connection.execute(text("DELETE FROM merchants"))
        await connection.execute(text("DELETE FROM categories"))
    yield built
    await built.dispose()


@pytest.fixture
async def client(engine: AsyncEngine) -> AsyncIterator[AsyncClient]:
    app = create_app()
    provider = DefaultSessionProvider(
        async_sessionmaker(engine, expire_on_commit=False)
    )
    app.dependency_overrides[get_session_provider] = lambda: provider
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(
        transport=transport, base_url="http://test", headers={"X-Internal-Key": KEY}
    ) as http:
        yield http


async def insert(engine: AsyncEngine, table: str, values: dict[str, Any]) -> str:
    row = {"id": new_id(), **values}
    columns = ", ".join(row)
    params = ", ".join(
        f"CAST(:{name} AS jsonb)"
        if table == "offers" and name in JSON_COLUMNS
        else f":{name}"
        for name in row
    )
    async with engine.begin() as connection:
        await connection.execute(
            text(f"INSERT INTO {table} ({columns}) VALUES ({params})"), row
        )
    return str(row["id"])


async def add_category(engine: AsyncEngine, slug: str, **overrides: Any) -> str:
    return await insert(
        engine,
        "categories",
        {
            "name": slug.title(),
            "slug": slug,
            "short_name": slug.title(),
            "description": f"{slug} freebies",
            "intro": "intro",
            "tint": "blush",
            **overrides,
        },
    )


async def add_merchant(engine: AsyncEngine, slug: str, **overrides: Any) -> str:
    return await insert(
        engine,
        "merchants",
        {"name": slug.title(), "slug": slug, **overrides},
    )


async def add_offer(
    engine: AsyncEngine, category_id: str, merchant_id: str, slug: str, **overrides: Any
) -> str:
    values: dict[str, Any] = {
        "title": slug.replace("-", " ").title(),
        "slug": slug,
        "summary": "A short summary",
        "category_id": category_id,
        "merchant_id": merchant_id,
        "offer_type": "FREE_SAMPLE",
        "commercial_basis": "EDITORIAL",
        "status": "PUBLISHED",
        "image_tint": "mint",
        "image_label": "label",
        "image_alt": "alt",
        "postage_kind": "FREE",
        "eligibility": "UK",
        "availability": "now",
        "last_verified_at": datetime.now(UTC),
        "publisher_notes": SECRET_NOTE,
        **overrides,
    }
    return await insert(engine, "offers", values)


@pytest.fixture
async def shop(engine: AsyncEngine) -> dict[str, str]:
    """One enabled category and one active merchant, for tests that need only those."""
    return {
        "category": await add_category(engine, "beauty"),
        "merchant": await add_merchant(engine, "acme"),
    }


async def slugs(client: AsyncClient, query: str = "") -> list[str]:
    response = await client.get(f"/v1/catalogue/offers?{query}")
    assert response.status_code == 200, response.text
    return [item["slug"] for item in response.json()["data"]["items"]]


# --- visibility --------------------------------------------------------------


async def test_only_published_unexpired_offers_are_listed(
    client: AsyncClient, engine: AsyncEngine, shop: dict[str, str]
) -> None:
    now = datetime.now(UTC)
    cat, mer = shop["category"], shop["merchant"]
    await add_offer(engine, cat, mer, "open-ended")
    await add_offer(engine, cat, mer, "ends-later", end_at=now + DAY)
    await add_offer(engine, cat, mer, "ended-yesterday", end_at=now - DAY)
    await add_offer(engine, cat, mer, "marked-expired", status="EXPIRED")
    await add_offer(engine, cat, mer, "a-draft", status="DRAFT", last_verified_at=None)
    await add_offer(engine, cat, mer, "archived", status="ARCHIVED")

    response = await client.get("/v1/catalogue/offers")

    data = response.json()["data"]
    assert sorted(item["slug"] for item in data["items"]) == [
        "ends-later",
        "open-ended",
    ]
    assert data["total"] == 2


async def test_empty_catalogue_is_an_empty_list(client: AsyncClient) -> None:
    response = await client.get("/v1/catalogue/offers")

    assert response.status_code == 200
    assert response.json()["data"]["items"] == []
    assert response.json()["data"]["total"] == 0


async def test_offers_of_a_disabled_category_or_inactive_merchant_are_hidden(
    client: AsyncClient, engine: AsyncEngine, shop: dict[str, str]
) -> None:
    hidden_cat = await add_category(engine, "hidden", enabled=False)
    gone_merchant = await add_merchant(engine, "gone", active=False)
    await add_offer(engine, shop["category"], shop["merchant"], "visible")
    await add_offer(engine, hidden_cat, shop["merchant"], "in-hidden-category")
    await add_offer(engine, shop["category"], gone_merchant, "from-gone-merchant")

    assert await slugs(client) == ["visible"]
    assert (
        await client.get("/v1/catalogue/offers/in-hidden-category")
    ).status_code == 404
    assert (
        await client.get("/v1/catalogue/offers/from-gone-merchant")
    ).status_code == 404


# --- filters -----------------------------------------------------------------


async def test_category_filter_and_unknown_or_disabled_category(
    client: AsyncClient, engine: AsyncEngine, shop: dict[str, str]
) -> None:
    food = await add_category(engine, "food")
    off = await add_category(engine, "off", enabled=False)
    await add_offer(engine, shop["category"], shop["merchant"], "lipstick")
    await add_offer(engine, food, shop["merchant"], "crisps")
    await add_offer(engine, off, shop["merchant"], "disabled-thing")

    assert await slugs(client, "category=food") == ["crisps"]
    assert await slugs(client, "category=no-such-category") == []
    assert await slugs(client, "category=off") == []


async def test_type_filter_accepts_several_values(
    client: AsyncClient, engine: AsyncEngine, shop: dict[str, str]
) -> None:
    cat, mer = shop["category"], shop["merchant"]
    await add_offer(engine, cat, mer, "sample", offer_type="FREE_SAMPLE")
    await add_offer(engine, cat, mer, "trial", offer_type="FREE_TRIAL")
    await add_offer(engine, cat, mer, "money-back", offer_type="CASHBACK")

    assert await slugs(client, "type=free_trial") == ["trial"]
    assert sorted(await slugs(client, "type=free_trial&type=cashback")) == [
        "money-back",
        "trial",
    ]


async def test_free_postage_filter_excludes_paid_and_unknown_postage(
    client: AsyncClient, engine: AsyncEngine, shop: dict[str, str]
) -> None:
    cat, mer = shop["category"], shop["merchant"]
    await add_offer(engine, cat, mer, "free-post")
    await add_offer(
        engine, cat, mer, "paid-post", postage_kind="COST",
        postage_amount_minor=299, postage_currency="GBP",
    )  # fmt: skip
    await add_offer(engine, cat, mer, "check-post", postage_kind="CHECK_TERMS")

    assert await slugs(client, "postage=free") == ["free-post"]
    assert len(await slugs(client)) == 3


async def test_featured_filter(
    client: AsyncClient, engine: AsyncEngine, shop: dict[str, str]
) -> None:
    cat, mer = shop["category"], shop["merchant"]
    await add_offer(engine, cat, mer, "star", featured=True)
    await add_offer(engine, cat, mer, "plain", featured=False)

    assert await slugs(client, "featured=true") == ["star"]
    assert await slugs(client, "featured=false") == ["plain"]
    assert sorted(await slugs(client)) == ["plain", "star"]


# --- search ------------------------------------------------------------------


async def test_search_matches_title_summary_and_merchant_case_insensitively(
    client: AsyncClient, engine: AsyncEngine, shop: dict[str, str]
) -> None:
    cat = shop["category"]
    zed = await add_merchant(engine, "zedco", name="Zedco Foods")
    await add_offer(engine, cat, shop["merchant"], "by-title", title="Lemon Soap")
    await add_offer(
        engine, cat, shop["merchant"], "by-summary", summary="Smells of LEMON"
    )
    await add_offer(engine, cat, zed, "by-merchant", title="Crisps")
    await add_offer(engine, cat, shop["merchant"], "other", title="Tea")

    assert sorted(await slugs(client, "q=lemon")) == ["by-summary", "by-title"]
    assert await slugs(client, "q=zedco%20foods") == ["by-merchant"]
    assert await slugs(client, "q=nothing-matches-this") == []


async def test_search_percent_and_underscore_are_literal(
    client: AsyncClient, engine: AsyncEngine, shop: dict[str, str]
) -> None:
    cat, mer = shop["category"], shop["merchant"]
    await add_offer(engine, cat, mer, "percent", title="100% cotton")
    await add_offer(engine, cat, mer, "plain-number", title="1000 cotton")
    await add_offer(engine, cat, mer, "underscore", title="snake_case")
    await add_offer(engine, cat, mer, "no-underscore", title="snakeXcase")

    assert await slugs(client, "q=100%25") == ["percent"]
    assert await slugs(client, "q=%25") == ["percent"]
    assert await slugs(client, "q=snake_case") == ["underscore"]
    assert await slugs(client, "q=_") == ["underscore"]


async def test_search_backslash_and_sql_text_are_just_text(
    client: AsyncClient, engine: AsyncEngine, shop: dict[str, str]
) -> None:
    await add_offer(engine, shop["category"], shop["merchant"], "normal")

    for term in ("%5C", "'; DROP TABLE offers; --", "%5C%25"):
        assert await slugs(client, f"q={term}") == []

    assert await slugs(client) == ["normal"]


# --- sort and paging ---------------------------------------------------------


async def test_sorts(
    client: AsyncClient, engine: AsyncEngine, shop: dict[str, str]
) -> None:
    cat, mer = shop["category"], shop["merchant"]
    now = datetime.now(UTC)
    await add_offer(engine, cat, mer, "banana", title="banana", start_at=now - 3 * DAY,
                    end_at=now + 9 * DAY)  # fmt: skip
    await add_offer(engine, cat, mer, "apple", title="Apple", start_at=now - 1 * DAY,
                    end_at=now + 2 * DAY)  # fmt: skip
    await add_offer(engine, cat, mer, "cherry", title="Cherry", start_at=now - 2 * DAY)

    assert await slugs(client, "sort=newest") == ["apple", "cherry", "banana"]
    assert await slugs(client) == ["apple", "cherry", "banana"]
    assert await slugs(client, "sort=a_z") == ["apple", "banana", "cherry"]
    assert await slugs(client, "sort=ending_soon") == ["apple", "banana", "cherry"]


async def test_newest_falls_back_to_creation_when_start_is_missing(
    client: AsyncClient, engine: AsyncEngine, shop: dict[str, str]
) -> None:
    cat, mer = shop["category"], shop["merchant"]
    await add_offer(engine, cat, mer, "old", start_at=datetime.now(UTC) - 30 * DAY)
    await add_offer(engine, cat, mer, "no-start-date")

    response = await client.get("/v1/catalogue/offers")

    first = response.json()["data"]["items"][0]
    assert first["slug"] == "no-start-date"
    assert first["start_at"] is not None


async def test_paging_covers_every_offer_once_and_total_ignores_the_page(
    client: AsyncClient, engine: AsyncEngine, shop: dict[str, str]
) -> None:
    for number in range(7):
        await add_offer(engine, shop["category"], shop["merchant"], f"offer-{number}")

    pages = [await client.get(f"/v1/catalogue/offers?limit=3&offset={n}&sort=a_z")
             for n in (0, 3, 6, 9)]  # fmt: skip

    seen = [item["slug"] for page in pages for item in page.json()["data"]["items"]]
    assert seen == [f"offer-{number}" for number in range(7)]
    assert {page.json()["data"]["total"] for page in pages} == {7}
    assert pages[-1].json()["data"]["items"] == []


# --- detail ------------------------------------------------------------------


async def test_detail_returns_the_full_public_offer(
    client: AsyncClient, engine: AsyncEngine, shop: dict[str, str]
) -> None:
    await add_offer(
        engine, shop["category"], shop["merchant"], "free-shampoo",
        description='["One.", "Two."]', steps='[{"title": "Go", "detail": "Do it."}]',
        freshness="NEW_TODAY", requires_card=True, subscription_terms="Cancel anytime",
    )  # fmt: skip

    response = await client.get("/v1/catalogue/offers/free-shampoo")

    data = response.json()["data"]
    assert response.status_code == 200
    assert data["description"] == ["One.", "Two."]
    assert data["steps"] == [{"title": "Go", "detail": "Do it."}]
    assert data["freshness"] == "new_today"
    assert data["requires_card"] is True
    assert data["category"] == {"slug": "beauty", "name": "Beauty"}
    assert data["merchant"] == {"slug": "acme", "name": "Acme", "logo_url": None}
    assert data["postage"] == {"kind": "free", "amount_minor": None, "currency": None}
    assert response.headers["Cache-Control"] == "private, max-age=60"


AWIN_URL = "https://www.awin1.com/cread.php?awinmid=1&awinaffid=2"
SHOP_URL = "https://www.acme.example/free-sample"
OFF_HOST_URL = "https://evil.example/x"
LINK_SCENARIOS = {
    "network-only": ([("awin", AWIN_URL)], True),
    "direct-only": ([("direct", SHOP_URL)], False),
    "network-then-direct": ([("awin", AWIN_URL), ("direct", SHOP_URL)], True),
    "direct-then-network": ([("direct", SHOP_URL), ("awin", AWIN_URL)], False),
    "unusable-network-then-direct": (
        [("awin", OFF_HOST_URL), ("direct", SHOP_URL)],
        False,
    ),
    "unusable-direct-then-network": (
        [("direct", OFF_HOST_URL), ("awin", AWIN_URL)],
        True,
    ),
    "unusable-only": ([("awin", OFF_HOST_URL)], False),
    "no-links": ([], False),
}


@pytest.mark.parametrize("scenario", list(LINK_SCENARIOS))
async def test_detail_flag_describes_the_link_the_click_follows(
    client: AsyncClient, engine: AsyncEngine, shop: dict[str, str], scenario: str
) -> None:
    links, expected = LINK_SCENARIOS[scenario]
    merchant = await add_merchant(
        engine, "acme-site", website="https://www.acme.example"
    )
    offer_id = await add_offer(engine, shop["category"], merchant, "linked")
    for provider, url in links:
        await insert(
            engine,
            "affiliate_links",
            {"offer_id": offer_id, "provider": provider, "destination_url": url},
        )

    detail = await client.get("/v1/catalogue/offers/linked")
    click = await client.post(f"/v1/catalogue/offers/{offer_id}/click", json={})

    assert detail.status_code == 200
    assert detail.json()["data"]["earns_commission"] is expected
    if links and click.status_code == 200:
        followed = click.json()["data"]["destination_url"]
        provider = next(p for p, url in links if url == followed)
        assert (provider != "direct") is expected
    else:
        assert expected is False


async def test_an_inactive_network_link_does_not_make_the_offer_earn(
    client: AsyncClient, engine: AsyncEngine, shop: dict[str, str]
) -> None:
    merchant = await add_merchant(
        engine, "acme-site", website="https://www.acme.example"
    )
    offer_id = await add_offer(engine, shop["category"], merchant, "linked")
    await insert(
        engine,
        "affiliate_links",
        {
            "offer_id": offer_id,
            "provider": "awin",
            "destination_url": AWIN_URL,
            "active": False,
        },
    )
    await insert(
        engine,
        "affiliate_links",
        {"offer_id": offer_id, "provider": "direct", "destination_url": SHOP_URL},
    )

    detail = await client.get("/v1/catalogue/offers/linked")

    assert detail.json()["data"]["earns_commission"] is False
    for hidden in ("awin", "direct", AWIN_URL, SHOP_URL, "provider"):
        assert hidden not in detail.text


async def test_detail_404_for_unknown_draft_and_archived(
    client: AsyncClient, engine: AsyncEngine, shop: dict[str, str]
) -> None:
    cat, mer = shop["category"], shop["merchant"]
    await add_offer(engine, cat, mer, "a-draft", status="DRAFT", last_verified_at=None)
    await add_offer(engine, cat, mer, "archived", status="ARCHIVED")

    responses = [
        await client.get(f"/v1/catalogue/offers/{slug}")
        for slug in ("a-draft", "archived", "unknown")
    ]

    assert [r.status_code for r in responses] == [404, 404, 404]
    assert len({r.content for r in responses}) == 1


async def test_detail_410_for_an_ended_offer_without_its_content(
    client: AsyncClient, engine: AsyncEngine, shop: dict[str, str]
) -> None:
    cat, mer = shop["category"], shop["merchant"]
    ended = datetime.now(UTC) - DAY
    await add_offer(engine, cat, mer, "lapsed", title="Lapsed Deal", end_at=ended)
    await add_offer(engine, cat, mer, "retired", title="Retired Deal", status="EXPIRED")

    lapsed = await client.get("/v1/catalogue/offers/lapsed")
    retired = await client.get("/v1/catalogue/offers/retired")

    assert lapsed.status_code == retired.status_code == 410
    body = lapsed.json()
    assert body["expired"] is True
    assert body["success"] is False
    assert body["data"]["title"] == "Lapsed Deal"
    assert body["data"]["category"] == {"slug": "beauty", "name": "Beauty"}
    assert retired.json()["data"]["ended_at"] is None
    assert SECRET_NOTE not in lapsed.text


# --- categories --------------------------------------------------------------


async def test_categories_list_enabled_ones_with_published_counts(
    client: AsyncClient, engine: AsyncEngine, shop: dict[str, str]
) -> None:
    now = datetime.now(UTC)
    food = await add_category(engine, "food", sort_order=-1)
    await add_category(engine, "empty", sort_order=5)
    off = await add_category(engine, "off", enabled=False)
    cat, mer = shop["category"], shop["merchant"]
    gone = await add_merchant(engine, "gone", active=False)
    await add_offer(engine, cat, mer, "live-1")
    await add_offer(engine, cat, mer, "live-2", end_at=now + DAY)
    await add_offer(engine, cat, mer, "ended", end_at=now - DAY)
    await add_offer(engine, cat, mer, "draft", status="DRAFT", last_verified_at=None)
    await add_offer(engine, cat, gone, "inactive-merchant")
    await add_offer(engine, food, mer, "crisps")
    await add_offer(engine, off, mer, "in-disabled")

    response = await client.get("/v1/catalogue/categories")

    data = response.json()["data"]
    assert response.status_code == 200
    assert [(c["slug"], c["offer_count"]) for c in data] == [
        ("food", 1),
        ("beauty", 2),
        ("empty", 0),
    ]
    assert set(data[0]) == {
        "slug", "name", "short_name", "description", "intro", "tint",
        "sort_order", "offer_count",
    }  # fmt: skip


async def test_categories_empty_database_is_an_empty_list(client: AsyncClient) -> None:
    response = await client.get("/v1/catalogue/categories")

    assert response.json() == {"success": True, "data": []}


# --- privacy -----------------------------------------------------------------


async def test_editorial_notes_and_links_never_appear_in_any_response(
    client: AsyncClient, engine: AsyncEngine
) -> None:
    category = await add_category(engine, "beauty")
    merchant = await add_merchant(
        engine, "acme", website="https://merchant.example/secret-site"
    )
    offer_id = await add_offer(
        engine,
        category,
        merchant,
        "private-bits",
        source_url="https://source.example/secret-source",
    )
    await insert(
        engine,
        "affiliate_links",
        {
            "offer_id": offer_id,
            "provider": "awin",
            "destination_url": "https://affiliate.example/secret-destination",
            "tracking_id": "track-123",
        },
    )

    bodies = [
        (await client.get(path)).text
        for path in (
            "/v1/catalogue/offers",
            "/v1/catalogue/offers/private-bits",
            "/v1/catalogue/categories",
        )
    ]

    for body in bodies:
        for secret in (
            SECRET_NOTE,
            "publisher_notes",
            "secret-destination",
            "track-123",
            "secret-source",
            "secret-site",
        ):
            assert secret not in body


async def test_an_expired_row_that_never_went_live_is_the_same_404_as_unknown(
    client: AsyncClient, engine: AsyncEngine, shop: dict[str, str]
) -> None:
    cat, mer = shop["category"], shop["merchant"]
    await add_offer(
        engine, cat, mer, "never-live", status="EXPIRED", last_verified_at=None
    )
    await add_offer(engine, cat, mer, "was-live", status="EXPIRED")

    never = await client.get("/v1/catalogue/offers/never-live")
    unknown = await client.get("/v1/catalogue/offers/unknown")
    was = await client.get("/v1/catalogue/offers/was-live")

    assert never.status_code == 404
    assert never.content == unknown.content
    assert was.status_code == 410


async def test_an_expired_row_with_a_future_end_date_reports_no_end_date(
    client: AsyncClient, engine: AsyncEngine, shop: dict[str, str]
) -> None:
    await add_offer(
        engine,
        shop["category"],
        shop["merchant"],
        "retired-early",
        status="EXPIRED",
        end_at=datetime.now(UTC) + 3 * DAY,
    )

    response = await client.get("/v1/catalogue/offers/retired-early")

    assert response.status_code == 410
    assert response.json()["data"]["ended_at"] is None


async def test_a_matching_etag_never_turns_a_410_into_a_304(
    client: AsyncClient, engine: AsyncEngine, shop: dict[str, str]
) -> None:
    await add_offer(
        engine, shop["category"], shop["merchant"], "lapsed",
        end_at=datetime.now(UTC) - DAY,
    )  # fmt: skip
    first = await client.get("/v1/catalogue/offers/lapsed")

    second = await client.get(
        "/v1/catalogue/offers/lapsed", headers={"If-None-Match": first.headers["ETag"]}
    )

    assert (first.status_code, second.status_code) == (410, 410)


FIXED = datetime(2030, 1, 1, 12, 0, tzinfo=UTC)


@pytest.fixture
async def fixed_clock_client(engine: AsyncEngine) -> AsyncIterator[AsyncClient]:
    app = create_app()
    provider = DefaultSessionProvider(
        async_sessionmaker(engine, expire_on_commit=False)
    )

    async def service() -> AsyncIterator[CatalogueService]:
        async with provider.session() as session:
            yield CatalogueService(CatalogueRepository(session), clock=lambda: FIXED)

    app.dependency_overrides[get_catalogue_service] = service
    app.dependency_overrides[get_session_provider] = lambda: provider
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(
        transport=transport, base_url="http://test", headers={"X-Internal-Key": KEY}
    ) as http:
        yield http


async def test_an_offer_ending_exactly_now_is_expired_and_one_second_later_is_live(
    fixed_clock_client: AsyncClient, engine: AsyncEngine, shop: dict[str, str]
) -> None:
    cat, mer = shop["category"], shop["merchant"]
    await add_offer(engine, cat, mer, "ends-now", end_at=FIXED)
    await add_offer(
        engine, cat, mer, "ends-in-a-second", end_at=FIXED + timedelta(seconds=1)
    )

    listed = await slugs(fixed_clock_client)
    counts = (await fixed_clock_client.get("/v1/catalogue/categories")).json()["data"]
    gone = await fixed_clock_client.get("/v1/catalogue/offers/ends-now")
    live = await fixed_clock_client.get("/v1/catalogue/offers/ends-in-a-second")

    assert listed == ["ends-in-a-second"]
    assert [(c["slug"], c["offer_count"]) for c in counts] == [("beauty", 1)]
    assert gone.status_code == 410
    assert gone.json()["data"]["ended_at"] == "2030-01-01T12:00:00Z"
    assert live.status_code == 200


async def test_internal_key_is_required(engine: AsyncEngine) -> None:
    app = create_app()
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        response = await http.get("/v1/catalogue/offers")

    assert response.status_code == 401
