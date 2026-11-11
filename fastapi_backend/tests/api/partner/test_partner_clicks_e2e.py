"""``GET /partner/v1/clicks`` on real Postgres, as the restricted role (ADR-013, FREEJ-59).

The partner side runs through the real surface, authenticator and ``PartnerSessionProvider`` on the role the committed
script creates (``freejoy_partner_<database>``). The data is seeded as the owner. The main proof is the first test:
the partner query and the FREEJ-54 admin report, which are two separate queries, give the same per offer per day
counts for the same merchant and range, for the offers a partner may see.
"""

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

import tests.catalogue_e2e_support as support
from app.core.click_retention import CLICK_RETENTION_DAYS
from app.core.config import settings
from app.core.database import PartnerSessionProvider
from app.core.ids import new_id
from app.core.partner_keys import generate_key
from app.main import app
from app.partner import deps
from app.partner.clicks_report import MAX_OFFERS
from app.partner.clicks_store import PartnerClickStore
from app.repositories.merchant_clicks import MerchantClickRepository
from app.services.merchant_click_report import MerchantClickReportService
from tests.api.partner.support import call_raw
from tests.api.test_catalogue_e2e import add_category, add_merchant, add_offer
from tests.partner_role_support import (
    apply_partner_role,
    role_name_for,
    role_password_for,
    role_url,
)

pytestmark = support.requires_database
database_url, engine = support.database_url, support.engine

CLICKS = "/partner/v1/clicks"
LISTED = {"PUBLISHED", "EXPIRED"}


@pytest.fixture
async def partner_world(
    database_url: str, engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[None]:
    role = role_name_for(make_url(database_url).database or "")
    await apply_partner_role(database_url, role, role_password_for(role))
    provider = PartnerSessionProvider(
        role_url(database_url, role, role_password_for(role))
    )
    monkeypatch.setattr(deps, "get_partner_session_provider", lambda: provider)
    monkeypatch.setattr(settings, "PARTNER_API_ENABLED", True)
    yield


async def add_key(engine: AsyncEngine, merchant_id: str) -> str:
    """A key row for ``merchant_id``; returns the key."""
    key = generate_key("test", new_id())
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO partner_api_keys (id, merchant_id, key_hash, label, created_by) "
                "VALUES (:id, :merchant, :hash, 'Feed', 'admin1')"
            ),
            {"id": key.key_id, "merchant": merchant_id, "hash": key.digest},
        )
    return key.value


async def click(
    engine: AsyncEngine, offer_id: str, at: datetime, user_agent_class: str | None
) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO click_events (id, offer_id, occurred_at, user_agent_class) "
                "VALUES (:id, :offer, :at, :ua)"
            ),
            {"id": new_id(), "offer": offer_id, "at": at, "ua": user_agent_class},
        )


async def read(key: str, query: str) -> tuple[int, dict[str, Any], dict[str, str]]:
    response = await call_raw(
        app,
        "GET",
        CLICKS,
        headers=[("authorization", f"Bearer {key}")],
        query_string=query,
    )
    return response.status, response.json(), dict(response.headers)


def at(day: int, hour: int = 9, month: int = 3) -> datetime:
    return datetime(2026, month, day, hour, 0, tzinfo=UTC)


@pytest.fixture
async def shop(engine: AsyncEngine, partner_world: None) -> dict[str, Any]:
    """Two merchants. Acme has a published, an ended, an idle published, a draft and an archived offer."""
    category = await add_category(engine, "beauty")
    acme = await add_merchant(engine, "acme")
    other = await add_merchant(engine, "other")
    offers = {
        "soap": await add_offer(engine, category, acme, "soap", title="Soap"),
        "balm": await add_offer(
            engine, category, acme, "balm", title="Balm", status="EXPIRED"
        ),
        "idle": await add_offer(engine, category, acme, "idle", title="Idle"),
        # Listed, with clicks that are all from bots: 0 in both reports, no days.
        "bots": await add_offer(engine, category, acme, "bots", title="Bots"),
        "draft": await add_offer(
            engine,
            category,
            acme,
            "draft",
            title="Secret draft",
            status="DRAFT",
            last_verified_at=None,
        ),
        "archived": await add_offer(
            engine,
            category,
            acme,
            "archived",
            title="Secret archived",
            status="ARCHIVED",
            last_verified_at=None,
        ),
        "foreign": await add_offer(engine, category, other, "foreign", title="Foreign"),
    }
    return {
        "category": category,
        "acme": acme,
        "other": other,
        "offers": offers,
        "acme_key": await add_key(engine, acme),
        "other_key": await add_key(engine, other),
    }


async def seed_clicks(engine: AsyncEngine, offers: dict[str, str]) -> None:
    for offer, day, hour, agent in (
        ("soap", 10, 0, "DESKTOP"),
        ("soap", 10, 23, "MOBILE"),
        ("soap", 11, 9, None),  # an unclassed click counts
        ("soap", 11, 9, "BOT"),  # a bot click does not
        ("soap", 12, 0, "DESKTOP"),  # outside the range (the end is exclusive)
        ("soap", 9, 23, "DESKTOP"),  # outside the range
        ("balm", 11, 9, "TABLET"),
        ("balm", 11, 10, "OTHER"),
        ("bots", 10, 9, "BOT"),
        ("bots", 11, 9, "BOT"),
        ("draft", 10, 9, "DESKTOP"),
        ("archived", 10, 9, "DESKTOP"),
        ("foreign", 10, 9, "DESKTOP"),
    ):
        await click(engine, offers[offer], at(day, hour), agent)


async def admin_days(
    engine: AsyncEngine, merchant_id: str, first: date, last: date
) -> dict[str, dict[str, int]]:
    """The FREEJ-54 admin report as ``{slug: {day: clicks}}`` for the listed statuses only."""
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        status = await _statuses(session)
        report = await MerchantClickReportService(
            MerchantClickRepository(session)
        ).report(merchant_id, first, last)
    return {
        o.slug: {d.day.isoformat(): d.clicks for d in o.days}
        for o in report.offers
        if status[o.slug] in LISTED
    }


async def admin_order(
    engine: AsyncEngine, merchant_id: str, first: date, last: date
) -> list[str]:
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        status = await _statuses(session)
        report = await MerchantClickReportService(
            MerchantClickRepository(session)
        ).report(merchant_id, first, last)
    return [o.slug for o in report.offers if status[o.slug] in LISTED]


async def admin_listed_order(
    engine: AsyncEngine, merchant_id: str, first: date, last: date
) -> tuple[str, list[str]]:
    """The admin repository's first offer, and every listed offer in its order (no cap), for the cap comparison."""
    start = datetime.combine(first, datetime.min.time(), tzinfo=UTC)
    end = datetime.combine(last + timedelta(days=1), datetime.min.time(), tzinfo=UTC)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        status = await _statuses(session)
        page = await MerchantClickRepository(session).offers(
            merchant_id, start, end, 1000
        )
    return page.offers[0].slug, [
        o.slug for o in page.offers if status[o.slug] in LISTED
    ]


async def _statuses(session: AsyncSession) -> dict[str, str]:
    rows = await session.execute(text("SELECT slug, status FROM offers"))
    return {slug: status for slug, status in rows.all()}


def partner_days(body: dict[str, Any]) -> dict[str, dict[str, int]]:
    return {
        o["slug"]: {d["date"]: d["clicks"] for d in o["days"]}
        for o in body["data"]["offers"]
    }


async def test_the_partner_query_and_the_admin_report_give_the_same_counts(
    engine: AsyncEngine, shop: dict[str, Any]
) -> None:
    await seed_clicks(engine, shop["offers"])

    status, body, _ = await read(shop["acme_key"], "from=2026-03-10&to=2026-03-11")
    admin = await admin_days(engine, shop["acme"], date(2026, 3, 10), date(2026, 3, 11))
    order = await admin_order(
        engine, shop["acme"], date(2026, 3, 10), date(2026, 3, 11)
    )

    assert status == 200
    assert partner_days(body) == admin
    assert [o["slug"] for o in body["data"]["offers"]] == order
    assert admin == {
        "soap": {"2026-03-10": 2, "2026-03-11": 1},
        "balm": {"2026-03-11": 2},
        "bots": {},
        "idle": {},
    }
    assert [o["total_clicks"] for o in body["data"]["offers"]] == [3, 2, 0, 0]


async def test_a_draft_or_an_archived_offer_is_never_named_even_with_clicks(
    engine: AsyncEngine, shop: dict[str, Any]
) -> None:
    await seed_clicks(engine, shop["offers"])

    _, body, _ = await read(shop["acme_key"], "from=2026-03-10&to=2026-03-11")

    text_body = str(body)
    for hidden in ("Secret draft", "Secret archived", "'draft'", "'archived'"):
        assert hidden not in text_body
    assert {o["slug"] for o in body["data"]["offers"]} == {
        "soap",
        "balm",
        "idle",
        "bots",
    }
    assert body["data"]["offers_truncated"] is False


async def test_one_merchants_key_never_reads_anothers_counts(
    engine: AsyncEngine, shop: dict[str, Any]
) -> None:
    await seed_clicks(engine, shop["offers"])

    _, mine, _ = await read(shop["acme_key"], "from=2026-03-10&to=2026-03-11")
    _, theirs, _ = await read(
        shop["other_key"],
        f"from=2026-03-10&to=2026-03-11&merchant_id={shop['acme']}",
    )

    assert partner_days(theirs) == {"foreign": {"2026-03-10": 1}}
    assert "foreign" not in str(mine)
    for word in ("soap", "balm", "idle"):
        assert word not in str(theirs)
    assert shop["acme"] not in str(theirs) and shop["other"] not in str(mine)


async def test_no_internal_id_reaches_the_response(
    engine: AsyncEngine, shop: dict[str, Any]
) -> None:
    await seed_clicks(engine, shop["offers"])

    _, body, _ = await read(shop["acme_key"], "from=2026-03-10&to=2026-03-11")

    text_body = str(body)
    for internal in (shop["acme"], *shop["offers"].values()):
        assert internal not in text_body


async def test_the_day_boundaries_are_midnight_utc_whatever_the_session_time_zone(
    engine: AsyncEngine, shop: dict[str, Any], database_url: str
) -> None:
    soap = shop["offers"]["soap"]
    for moment in (
        datetime(2026, 3, 9, 23, 59, 59, tzinfo=UTC),
        datetime(2026, 3, 10, 0, 0, 0, tzinfo=UTC),
        datetime(2026, 3, 11, 23, 59, 59, tzinfo=UTC),
        datetime(2026, 3, 12, 0, 0, 0, tzinfo=UTC),
    ):
        await click(engine, soap, moment, "DESKTOP")
    role = role_name_for(make_url(database_url).database or "")
    provider = PartnerSessionProvider(
        role_url(database_url, role, role_password_for(role))
    )
    start, end = datetime(2026, 3, 10, tzinfo=UTC), datetime(2026, 3, 12, tzinfo=UTC)

    async with provider.session() as session:
        # A zone far from UTC, where a day cut on local midnight would move every edge click to another day.
        await session.execute(text("SET TIME ZONE 'Pacific/Auckland'"))
        store = PartnerClickStore(session)
        page = await store.offers(shop["acme"], start, end, 100)
        days = await store.daily(shop["acme"], [soap], start, end)

    assert [(d.day.isoformat(), d.clicks) for d in days] == [
        ("2026-03-10", 1),
        ("2026-03-11", 1),
    ]
    assert {o.slug: o.clicks for o in page.offers}["soap"] == 2


async def test_at_most_a_hundred_offers_come_back_busiest_first(
    engine: AsyncEngine, shop: dict[str, Any]
) -> None:
    async with engine.begin() as connection:
        for number in range(MAX_OFFERS + 1):
            offer_id = new_id()
            await connection.execute(
                text(
                    "INSERT INTO offers (id, title, slug, summary, category_id, merchant_id, offer_type, "
                    "commercial_basis, status, image_tint, image_label, image_alt, postage_kind, "
                    "eligibility, availability, last_verified_at) SELECT :id, :title, :slug, 's', "
                    "category_id, :merchant, 'FREE_SAMPLE', 'EDITORIAL', 'PUBLISHED', 'mint', 'l', 'a', "
                    "'FREE', 'UK', 'now', now() FROM offers WHERE id = :template"
                ),
                {
                    "id": offer_id,
                    "title": f"Bulk {number:03d}",
                    "slug": f"bulk-{number:03d}",
                    "merchant": shop["acme"],
                    "template": shop["offers"]["soap"],
                },
            )
            for _ in range(number % 4):
                await connection.execute(
                    text(
                        "INSERT INTO click_events (id, offer_id, occurred_at, user_agent_class) "
                        "VALUES (:id, :offer, :at, 'DESKTOP')"
                    ),
                    {"id": new_id(), "offer": offer_id, "at": at(10)},
                )

    # A draft with more clicks than any listed offer: first in the admin report's list, never in the partner's.
    for _ in range(10):
        await click(engine, shop["offers"]["draft"], at(10), "DESKTOP")

    _, body, _ = await read(shop["acme_key"], "from=2026-03-10&to=2026-03-11")

    offers = body["data"]["offers"]
    assert len(offers) == 100
    assert body["data"]["offers_truncated"] is True
    totals = [o["total_clicks"] for o in offers]
    assert totals == sorted(totals, reverse=True)
    admin_first, listed = await admin_listed_order(
        engine, shop["acme"], date(2026, 3, 10), date(2026, 3, 11)
    )
    assert admin_first == "draft"  # the one place the two lists legitimately differ
    assert [o["slug"] for o in offers] == listed[:100]
    assert "draft" not in str(body)


async def test_equal_counts_are_ordered_by_title_then_internal_id(
    engine: AsyncEngine, shop: dict[str, Any]
) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE offers SET title = 'Same' WHERE slug IN ('soap', 'balm', 'idle')"
            )
        )

    _, body, _ = await read(shop["acme_key"], "from=2026-03-10&to=2026-03-11")
    admin = await admin_order(
        engine, shop["acme"], date(2026, 3, 10), date(2026, 3, 11)
    )

    assert [o["slug"] for o in body["data"]["offers"]] == admin


async def test_the_retention_boundary_says_when_a_range_starts_before_it(
    engine: AsyncEngine, shop: dict[str, Any]
) -> None:
    today = datetime.now(UTC).date()
    old_from = today - timedelta(days=CLICK_RETENTION_DAYS + 10)
    old_to = old_from + timedelta(days=5)
    recent_from = today - timedelta(days=10)

    _, old, _ = await read(shop["acme_key"], f"from={old_from}&to={old_to}")
    _, recent, _ = await read(shop["acme_key"], f"from={recent_from}&to={today}")

    assert old["data"]["retention"]["days"] == CLICK_RETENTION_DAYS
    assert old["data"]["retention"]["starts_before_retention"] is True
    assert recent["data"]["retention"]["starts_before_retention"] is False
    assert (
        old["data"]["retention"]["earliest_complete_date"]
        == recent["data"]["retention"]["earliest_complete_date"]
    )


async def test_the_response_is_no_store_and_the_range_is_refused_with_the_same_headers(
    engine: AsyncEngine, shop: dict[str, Any]
) -> None:
    status, _, ok_headers = await read(
        shop["acme_key"], "from=2026-03-10&to=2026-03-11"
    )
    refused, body, bad_headers = await read(
        shop["acme_key"], "from=2026-03-10&to=2026-06-30"
    )

    assert status == 200 and refused == 422
    assert body["code"] == "invalid_range"
    for headers in (ok_headers, bad_headers):
        assert headers["cache-control"] == "no-store, private"
        assert headers["x-freejoy-surface"] == "partner"


async def test_a_statement_given_another_merchants_offer_ids_returns_nothing_of_theirs(
    engine: AsyncEngine, shop: dict[str, Any], database_url: str
) -> None:
    await seed_clicks(engine, shop["offers"])
    role = role_name_for(make_url(database_url).database or "")
    provider = PartnerSessionProvider(
        role_url(database_url, role, role_password_for(role))
    )
    start, end = at(10, 0), at(12, 0)
    everyone = [*shop["offers"].values()]

    async with provider.session() as session:
        store = PartnerClickStore(session)
        days = await store.daily(shop["other"], everyone, start, end)

    assert {d.offer_id for d in days} == {shop["offers"]["foreign"]}


async def test_daily_given_a_draft_and_an_archived_offer_of_the_same_merchant_returns_nothing(
    engine: AsyncEngine, shop: dict[str, Any], database_url: str
) -> None:
    await seed_clicks(engine, shop["offers"])
    role = role_name_for(make_url(database_url).database or "")
    provider = PartnerSessionProvider(
        role_url(database_url, role, role_password_for(role))
    )
    ids = [shop["offers"]["draft"], shop["offers"]["archived"]]

    async with provider.session() as session:
        days = await PartnerClickStore(session).daily(
            shop["acme"], ids, at(10, 0), at(12, 0)
        )

    assert days == []


async def test_an_offer_moved_to_another_merchant_takes_its_whole_click_history_with_it(
    engine: AsyncEngine, shop: dict[str, Any]
) -> None:
    await seed_clicks(engine, shop["offers"])
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE offers SET merchant_id = :m WHERE id = :o"),
            {"m": shop["other"], "o": shop["offers"]["soap"]},
        )

    _, first, _ = await read(shop["acme_key"], "from=2026-03-10&to=2026-03-11")
    _, second, _ = await read(shop["other_key"], "from=2026-03-10&to=2026-03-11")
    admin_other = await admin_days(
        engine, shop["other"], date(2026, 3, 10), date(2026, 3, 11)
    )

    assert "soap" not in partner_days(first)
    assert partner_days(second) == admin_other
    assert partner_days(second)["soap"] == {"2026-03-10": 2, "2026-03-11": 1}
