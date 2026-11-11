"""End to end: the outbound click on a real Postgres.

Runs only when ``TEST_DATABASE_URL`` points at a throwaway local database whose name ends in
"_test" (same contract as the other ``*_e2e.py`` suites). The route, services, repositories and
SQL are production code. It proves the one-row rule (exactly one ``click_events`` row for a
successful click, none for any refusal), which offers and stored destinations may be followed,
that the table has no column that could identify a visitor, and that the answer sets no cookie.
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

from app.api.deps import get_rate_limiter, get_session_provider
from app.core.database import DefaultSessionProvider, build_engine
from app.core.rate_limit import OUTBOUND_OFFER_BOT_POLICY, OUTBOUND_OFFER_POLICY
from app.main import create_app
from app.repositories.admin_merchants import AdminMerchantRepository
from commands.prune_click_events import prune_click_events
from tests.api.test_catalogue_e2e import add_category, add_merchant, add_offer, insert
from tests.fakes import FakeRateLimiter, as_limiter

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
BACKEND_ROOT = Path(__file__).resolve().parents[2]
KEY = "test-only-internal-service-key-not-for-deployment"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
DAY = timedelta(days=1)
AWIN = "https://www.awin1.com/cread.php?awinmid=1&awinaffid=2"

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="TEST_DATABASE_URL is not set"
)

# Everything a click row may contain. Anything else (an IP, a cookie id, a raw user agent,
# a user or session id) would let a click be tied to a person.
CLICK_COLUMNS = {
    "id",
    "offer_id",
    "affiliate_link_id",
    "occurred_at",
    "referrer_host",
    "user_agent_class",
}


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


@pytest.fixture
async def shop(engine: AsyncEngine) -> dict[str, str]:
    return {
        "category": await add_category(engine, "beauty"),
        "merchant": await add_merchant(
            engine, "acme", website="https://www.acme.example"
        ),
    }


async def add_link(
    engine: AsyncEngine, offer_id: str, url: str = AWIN, **overrides: Any
) -> str:
    return await insert(
        engine,
        "affiliate_links",
        {"offer_id": offer_id, "provider": "awin", "destination_url": url, **overrides},
    )


async def click_rows(engine: AsyncEngine) -> list[Any]:
    async with engine.connect() as connection:
        result = await connection.execute(
            text(
                "SELECT offer_id, affiliate_link_id, occurred_at, referrer_host, "
                "user_agent_class FROM click_events ORDER BY occurred_at"
            )
        )
        return list(result.all())


def click_path(offer_id: str) -> str:
    return f"/v1/catalogue/offers/{offer_id}/click"


# --- the one-row rule -------------------------------------------------------------


async def test_a_successful_click_writes_exactly_one_row_and_returns_the_destination(
    client: AsyncClient, engine: AsyncEngine, shop: dict[str, str]
) -> None:
    offer = await add_offer(engine, shop["category"], shop["merchant"], "lipstick")
    link = await add_link(engine, offer)

    response = await client.post(
        click_path(offer),
        json={"referrer_host": "Deals.Example", "user_agent_class": "MOBILE"},
    )

    assert response.status_code == 200
    assert response.json() == {"success": True, "data": {"destination_url": AWIN}}
    assert response.headers["Cache-Control"] == "no-store"
    assert "set-cookie" not in response.headers
    [row] = await click_rows(engine)
    assert (row.offer_id, row.affiliate_link_id) == (offer, link)
    assert (row.referrer_host, row.user_agent_class) == ("deals.example", "MOBILE")
    assert row.occurred_at.second == 0 and row.occurred_at.microsecond == 0


async def test_each_successful_request_writes_its_own_row(
    client: AsyncClient, engine: AsyncEngine, shop: dict[str, str]
) -> None:
    offer = await add_offer(engine, shop["category"], shop["merchant"], "lipstick")
    await add_link(engine, offer)

    responses = [await client.post(click_path(offer)) for _ in range(3)]

    assert [response.status_code for response in responses] == [200, 200, 200]
    assert len(await click_rows(engine)) == 3


async def test_a_click_without_a_body_stores_no_referrer_and_no_device_class(
    client: AsyncClient, engine: AsyncEngine, shop: dict[str, str]
) -> None:
    offer = await add_offer(engine, shop["category"], shop["merchant"], "lipstick")
    await add_link(engine, offer)

    await client.post(click_path(offer))

    [row] = await click_rows(engine)
    assert (row.referrer_host, row.user_agent_class) == (None, None)


async def test_the_click_table_has_no_column_that_could_identify_a_visitor(
    client: AsyncClient, engine: AsyncEngine
) -> None:
    async with engine.connect() as connection:
        result = await connection.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'click_events' AND table_schema = current_schema()"
            )
        )
    assert {name for (name,) in result.all()} == CLICK_COLUMNS


async def test_visitor_details_sent_with_the_request_are_never_stored(
    client: AsyncClient, engine: AsyncEngine, shop: dict[str, str]
) -> None:
    offer = await add_offer(engine, shop["category"], shop["merchant"], "lipstick")
    await add_link(engine, offer)
    headers = {
        "X-Client-IP": "203.0.113.77",
        "User-Agent": "Mozilla/5.0 SecretBrowser/9.9",
        "Cookie": "visitor=cookie-id-4242",
        "Referer": "https://deals.example/secret/path?token=abc",
    }

    response = await client.post(click_path(offer), headers=headers)

    assert response.status_code == 200
    async with engine.connect() as connection:
        dump = (
            await connection.execute(text("SELECT c::text FROM click_events c"))
        ).all()
    stored = str(dump)
    for fragment in ("203.0.113.77", "SecretBrowser", "cookie-id-4242", "secret/path"):
        assert fragment not in stored


# --- refusals write nothing -------------------------------------------------------


async def seed_refusal(
    engine: AsyncEngine, shop: dict[str, str], case: str
) -> tuple[str, int]:
    """Create the offer for one refusal case; return its id and the expected status."""
    category, merchant = shop["category"], shop["merchant"]
    now = datetime.now(UTC)
    status = 404
    if case == "expired_by_date":
        offer = await add_offer(engine, category, merchant, "o", end_at=now - DAY)
        status = 410
    elif case == "expired_status":
        offer = await add_offer(engine, category, merchant, "o", status="EXPIRED")
        status = 410
    elif case == "never_live_expired":
        offer = await add_offer(
            engine, category, merchant, "o", status="EXPIRED", last_verified_at=None
        )
    elif case == "draft":
        offer = await add_offer(
            engine, category, merchant, "o", status="DRAFT", last_verified_at=None
        )
    elif case == "archived":
        offer = await add_offer(engine, category, merchant, "o", status="ARCHIVED")
    elif case == "disabled_category":
        hidden = await add_category(engine, "hidden", enabled=False)
        offer = await add_offer(engine, hidden, merchant, "o")
    elif case == "inactive_merchant":
        gone = await add_merchant(engine, "gone", active=False)
        offer = await add_offer(engine, category, gone, "o")
    else:
        offer = await add_offer(engine, category, merchant, "o")
    if case == "inactive_link":
        await add_link(engine, offer, active=False)
    elif case != "no_link":
        await add_link(engine, offer)
    return offer, status


REFUSALS = [
    "expired_by_date",
    "expired_status",
    "never_live_expired",
    "draft",
    "archived",
    "disabled_category",
    "inactive_merchant",
    "inactive_link",
    "no_link",
]


@pytest.mark.parametrize("case", REFUSALS)
async def test_a_refused_click_redirects_nowhere_and_writes_no_row(
    case: str, client: AsyncClient, engine: AsyncEngine, shop: dict[str, str]
) -> None:
    offer, status = await seed_refusal(engine, shop, case)

    response = await client.post(click_path(offer))

    assert response.status_code == status, response.text
    assert "destination_url" not in response.text
    assert "awin1.com" not in response.text
    assert "set-cookie" not in response.headers
    assert await click_rows(engine) == []


async def test_an_unknown_offer_is_the_same_404_as_a_draft(
    client: AsyncClient, engine: AsyncEngine, shop: dict[str, str]
) -> None:
    draft, _ = await seed_refusal(engine, shop, "draft")

    unknown = await client.post(click_path("no-such-offer"))
    hidden = await client.post(click_path(draft))

    assert unknown.status_code == hidden.status_code == 404
    assert unknown.json() == hidden.json()
    assert await click_rows(engine) == []


async def test_the_410_body_matches_the_public_detail_page(
    client: AsyncClient, engine: AsyncEngine, shop: dict[str, str]
) -> None:
    offer, _ = await seed_refusal(engine, shop, "expired_by_date")

    click = await client.post(click_path(offer))
    detail = await client.get("/v1/catalogue/offers/o")

    assert click.status_code == detail.status_code == 410
    assert click.json() == detail.json()


# --- hostile stored destinations --------------------------------------------------

HOSTILE_DESTINATIONS = [
    "javascript:alert(1)",
    "data:text/html;base64,PHNjcmlwdD4=",
    "http://www.awin1.com/cread.php",
    "//evil.example/x",
    "https://www.awin1.com@evil.example/",
    "https://evil.example\\@www.awin1.com/",
    "https://www.awin1.com/x\r\nSet-Cookie: a=b",
    "https://www.awin1.com/x%0d%0aLocation:https://evil.example",
    "https://awin1.com.evil.example/",
    "https://evil.example/?u=https://www.awin1.com/",
    "https://127.0.0.1/",
    "",
]


@pytest.mark.parametrize("destination", HOSTILE_DESTINATIONS)
async def test_a_hostile_destination_written_straight_to_the_table_is_never_returned(
    destination: str,
    client: AsyncClient,
    engine: AsyncEngine,
    shop: dict[str, str],
) -> None:
    offer = await add_offer(engine, shop["category"], shop["merchant"], "lipstick")
    await add_link(engine, offer, destination)

    response = await client.post(click_path(offer))

    assert response.status_code == 404
    assert response.json() == {"success": False, "error": "Offer not found"}
    assert await click_rows(engine) == []


async def test_a_provider_may_only_use_its_own_hosts(
    client: AsyncClient, engine: AsyncEngine, shop: dict[str, str]
) -> None:
    offer = await add_offer(engine, shop["category"], shop["merchant"], "lipstick")
    await add_link(engine, offer, "https://www.tradedoubler.com/x", provider="awin")
    await add_link(engine, offer, "https://x.example/", provider="unlisted")

    response = await client.post(click_path(offer))

    assert response.status_code == 404
    assert await click_rows(engine) == []


async def test_a_direct_link_is_followed_only_on_the_merchant_website(
    client: AsyncClient, engine: AsyncEngine, shop: dict[str, str]
) -> None:
    on_site = await add_offer(engine, shop["category"], shop["merchant"], "on-site")
    off_site = await add_offer(engine, shop["category"], shop["merchant"], "off-site")
    await add_link(engine, on_site, "https://www.acme.example/free", provider="direct")
    await add_link(engine, off_site, "https://evil.example/free", provider="direct")

    followed = await client.post(click_path(on_site))
    refused = await client.post(click_path(off_site))

    assert followed.status_code == 200
    assert followed.json()["data"]["destination_url"] == "https://www.acme.example/free"
    assert refused.status_code == 404
    assert [row.offer_id for row in await click_rows(engine)] == [on_site]


async def test_the_oldest_usable_active_link_is_chosen(
    client: AsyncClient, engine: AsyncEngine, shop: dict[str, str]
) -> None:
    offer = await add_offer(engine, shop["category"], shop["merchant"], "lipstick")
    await add_link(engine, offer, "javascript:alert(1)")
    good = await add_link(engine, offer, AWIN)
    await add_link(engine, offer, "https://www.awin1.com/other")

    response = await client.post(click_path(offer))

    assert response.status_code == 200
    assert response.json()["data"]["destination_url"] == AWIN
    [row] = await click_rows(engine)
    assert row.affiliate_link_id == good


async def test_the_sample_provider_is_followed_in_the_test_environment(
    client: AsyncClient, engine: AsyncEngine, shop: dict[str, str]
) -> None:
    offer = await add_offer(engine, shop["category"], shop["merchant"], "lipstick")
    await add_link(engine, offer, "https://example.com/go/lipstick", provider="sample")

    response = await client.post(click_path(offer))

    assert response.status_code == 200
    assert len(await click_rows(engine)) == 1


# --- the per-offer recording budget (FREEJ-62) ----------------------------------


async def test_a_flood_of_bot_requests_cannot_use_up_the_budget_genuine_clicks_need(
    engine: AsyncEngine, shop: dict[str, str]
) -> None:
    offer = await add_offer(engine, shop["category"], shop["merchant"], "lipstick")
    await add_link(engine, offer)
    app = create_app()
    provider = DefaultSessionProvider(
        async_sessionmaker(engine, expire_on_commit=False)
    )
    app.dependency_overrides[get_session_provider] = lambda: provider
    limiter = as_limiter(FakeRateLimiter())  # one instance, so the counts add up
    app.dependency_overrides[get_rate_limiter] = lambda: limiter
    flood = OUTBOUND_OFFER_POLICY.limit

    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
        headers={"X-Internal-Key": KEY},
    ) as http:
        for n in range(flood):
            await http.post(
                click_path(offer),
                json={"user_agent_class": "BOT"},
                headers={"X-Client-IP": f"10.{n // 250}.{n % 250}.1"},
            )
        genuine = await http.post(
            click_path(offer),
            json={"user_agent_class": "DESKTOP"},
            headers={"X-Client-IP": "10.9.9.9"},
        )

    assert genuine.status_code == 200
    classes = [row.user_agent_class for row in await click_rows(engine)]
    assert classes.count("BOT") == OUTBOUND_OFFER_BOT_POLICY.limit
    assert classes.count("DESKTOP") == 1


# --- retention --------------------------------------------------------------------


async def test_pruning_deletes_only_clicks_older_than_the_period(
    engine: AsyncEngine, shop: dict[str, str]
) -> None:
    offer = await add_offer(engine, shop["category"], shop["merchant"], "lipstick")
    now = datetime.now(UTC)
    ages = {"old": 400, "just_over": 396, "kept": 394, "new": 0}
    for name, days in ages.items():
        await insert(
            engine,
            "click_events",
            {
                "offer_id": offer,
                "occurred_at": now - timedelta(days=days),
                "referrer_host": name,
            },
        )

    async with async_sessionmaker(engine)() as session:
        deleted = await prune_click_events(session, days=395, batch_size=1)

    assert deleted == 2
    remaining = {row.referrer_host for row in await click_rows(engine)}
    assert remaining == {"kept", "new"}


async def test_the_merchant_repository_lists_only_active_direct_link_ids(
    engine: AsyncEngine, shop: dict[str, str]
) -> None:
    one = await add_offer(engine, shop["category"], shop["merchant"], "one")
    two = await add_offer(engine, shop["category"], shop["merchant"], "two")
    direct = await add_link(
        engine, one, "https://www.acme.example/x", provider="direct"
    )
    await add_link(engine, one, provider="awin")
    await add_link(
        engine, two, "https://www.acme.example/y", provider="direct", active=False
    )

    async with async_sessionmaker(engine)() as session:
        ids = await AdminMerchantRepository(session).active_direct_link_ids(
            shop["merchant"]
        )

    assert ids == [direct]
