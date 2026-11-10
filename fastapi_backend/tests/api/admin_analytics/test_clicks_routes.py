"""The merchant click report end to end on a real Postgres: permissions, real SQL and the response."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from app.core.ids import new_id
from tests.api.admin_analytics.support import BASE
from tests.api.admin_subscriptions.support import TEST_DATABASE_URL, as_user, seed_user
from tests.api.test_catalogue_e2e import add_category, add_merchant, add_offer

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="TEST_DATABASE_URL is not set"
)

PATH = f"{BASE}/clicks"


async def click(
    engine, offer_id: str, at: datetime, user_agent_class="DESKTOP"
) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO click_events (id, offer_id, occurred_at, user_agent_class) "
                "VALUES (:id, :offer, :at, :ua)"
            ),
            {"id": new_id(), "offer": offer_id, "at": at, "ua": user_agent_class},
        )


@pytest.fixture
async def shop(engine):
    async with engine.begin() as connection:
        await connection.execute(
            text("TRUNCATE click_events, offers, categories, merchants CASCADE")
        )
    category = await add_category(engine, "beauty")
    acme = await add_merchant(engine, "acme", name="Acme, Ltd")
    other = await add_merchant(engine, "other")
    soap = await add_offer(engine, category, acme, "soap", title='=HYPERLINK("x")')
    balm = await add_offer(engine, category, acme, "balm", title="Balm")
    foreign = await add_offer(engine, category, other, "foreign")
    await seed_user(engine, "owner", "admin", ["offers.read"])
    return {"acme": acme, "soap": soap, "balm": balm, "foreign": foreign}


def params(merchant_id: str, start: str = "2026-03-10", end: str = "2026-03-11"):
    return {"merchant_id": merchant_id, "from": start, "to": end}


async def test_the_report_counts_the_merchants_own_clicks_per_offer_per_day(
    client, engine, shop
):
    at = datetime(2026, 3, 10, 23, 59, 59, tzinfo=UTC)
    await click(engine, shop["soap"], at)
    await click(engine, shop["soap"], at + timedelta(seconds=1))
    await click(engine, shop["soap"], at, "BOT")
    await click(engine, shop["foreign"], at)

    result = await client.get(
        PATH, params=params(shop["acme"]), headers=as_user("owner")
    )

    assert result.status_code == 200
    assert result.headers["cache-control"] == "no-store"
    data = result.json()["data"]
    assert data["merchant"]["name"] == "Acme, Ltd"
    assert (data["total_clicks"], data["excluded_bot_clicks"]) == (2, 1)
    soap, balm = data["offers"]
    assert soap["slug"] == "soap" and soap["total_clicks"] == 2
    assert soap["days"] == [
        {"date": "2026-03-10", "clicks": 1},
        {"date": "2026-03-11", "clicks": 1},
    ]
    assert (balm["slug"], balm["total_clicks"], balm["days"]) == ("balm", 0, [])


async def test_an_unknown_merchant_is_404_with_the_catalogue_code(client, engine, shop):
    result = await client.get(PATH, params=params("nope"), headers=as_user("owner"))

    assert result.status_code == 404
    assert result.json()["code"] == "not_found"


async def test_an_admin_without_offers_read_is_403_even_with_analytics_access(
    client, engine, shop
):
    await seed_user(engine, "analyst", "admin", ["users.read", "subscriptions.read"])

    result = await client.get(
        PATH, params=params(shop["acme"]), headers=as_user("analyst")
    )

    assert result.status_code == 403


async def test_a_signed_in_user_who_is_not_an_admin_is_403(client, engine, shop):
    await seed_user(engine, "plain")

    result = await client.get(
        PATH, params=params(shop["acme"]), headers=as_user("plain")
    )

    assert result.status_code == 403


async def test_a_range_over_366_days_is_422(client, engine, shop):
    result = await client.get(
        PATH,
        params=params(shop["acme"], "2025-01-01", "2026-01-02"),
        headers=as_user("owner"),
    )

    assert result.status_code == 422
    assert result.json()["code"] == "invalid_range"
