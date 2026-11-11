"""The plans of the partner click statements on real Postgres, as the restricted role (ADR-013, FREEJ-59).

The role has a 10 s statement timeout and 20 connections shared by every merchant, so the cost of one merchant's read
must not grow with the clicks of every other merchant (anyone can add click rows through the public redirect). The
offers statement is driven from the merchant's offers and must reach ``click_events`` only through
``ix_click_events_offer_id_occurred_at``: never by a range scan of ``ix_click_events_occurred_at`` (which reads every
merchant's clicks in the range) and never by a sequential scan of the table. A merchant that owns a small share of a
large click table is the case that matters, so the test seeds exactly that.
"""

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import event, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine

import tests.catalogue_e2e_support as support
from app.core.database import PartnerSessionProvider
from app.partner.clicks_store import PartnerClickStore
from tests.api.partner import test_partner_clicks_e2e as clicks_e2e
from tests.api.test_catalogue_e2e import add_category, add_merchant
from tests.partner_role_support import role_name_for, role_password_for, role_url

pytestmark = support.requires_database
database_url, engine = support.database_url, support.engine
partner_world = clicks_e2e.partner_world

MINE = 60
OTHERS = 300
CLICKS = 400_000
RANGE_DAYS = 92
FORBIDDEN_INDEX = "ix_click_events_occurred_at"
WANTED_INDEX = "ix_click_events_offer_id_occurred_at"


async def seed(engine: AsyncEngine) -> str:
    """One merchant with 60 offers among 360, and 400,000 clicks spread over every offer and 395 days."""
    category = await add_category(engine, "beauty")
    mine = await add_merchant(engine, "mine")
    other = await add_merchant(engine, "other")
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO offers (id, title, slug, summary, category_id, merchant_id, offer_type, "
                "commercial_basis, status, image_tint, image_label, image_alt, postage_kind, eligibility, "
                "availability, last_verified_at) SELECT 'o' || g, 'Offer ' || g, 'offer-' || g, 's', :cat, "
                "CASE WHEN g <= :mine THEN :m ELSE :o END, 'FREE_SAMPLE', 'EDITORIAL', 'PUBLISHED', 'mint', "
                "'l', 'a', 'FREE', 'UK', 'now', now() FROM generate_series(1, :total) g"
            ),
            {
                "cat": category,
                "mine": MINE,
                "m": mine,
                "o": other,
                "total": MINE + OTHERS,
            },
        )
        await connection.execute(
            text(
                "INSERT INTO click_events (id, offer_id, occurred_at, user_agent_class) "
                "SELECT 'c' || g, 'o' || (1 + g % :total), "
                "date_trunc('minute', now() - random() * interval '395 days'), "
                "(ARRAY['DESKTOP', 'MOBILE', 'TABLET', 'BOT', 'OTHER'])[1 + g % 5] "
                "FROM generate_series(1, :clicks) g"
            ),
            {"total": MINE + OTHERS, "clicks": CLICKS},
        )
        await connection.execute(text("ANALYZE offers"))
        await connection.execute(text("ANALYZE click_events"))
    return mine


def plan_nodes(node: dict[str, Any]) -> list[dict[str, Any]]:
    """Every node of a JSON plan, sub plans included."""
    return [node, *(n for child in node.get("Plans", []) for n in plan_nodes(child))]


async def plans(
    provider: PartnerSessionProvider, merchant: str
) -> dict[str, list[dict[str, Any]]]:
    """The plan nodes of the offers statement and of the per day statement, as the role plans them."""
    end = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0) + (
        timedelta(days=1)
    )
    start = end - timedelta(days=RANGE_DAYS)
    captured: list[tuple[str, Any]] = []
    found: dict[str, list[dict[str, Any]]] = {}
    async with provider.session() as session:
        bind = session.sync_session.get_bind()

        def record(
            conn: Any, cursor: Any, statement: str, params: Any, *_: Any
        ) -> None:
            if not statement.startswith("EXPLAIN"):
                captured.append((statement, params))

        event.listen(bind, "before_cursor_execute", record)
        store = PartnerClickStore(session)
        page = await store.offers(merchant, start, end, 100)
        await store.daily(merchant, [o.offer_id for o in page.offers], start, end)
        event.remove(bind, "before_cursor_execute", record)
        assert len(captured) == 2
        connection = await session.connection()
        for name, (statement, params) in zip(
            ("offers", "daily"), captured, strict=True
        ):
            result = await connection.exec_driver_sql(
                f"EXPLAIN (FORMAT JSON) {statement}", params
            )
            document = result.scalar_one()
            parsed = json.loads(document) if isinstance(document, str) else document
            root = parsed[0]["Plan"]
            found[name] = plan_nodes(root)
    return found


@pytest.fixture
async def small_share(
    engine: AsyncEngine, database_url: str, partner_world: None
) -> AsyncIterator[tuple[PartnerSessionProvider, str]]:
    merchant = await seed(engine)
    role = role_name_for(make_url(database_url).database or "")
    yield (
        PartnerSessionProvider(role_url(database_url, role, role_password_for(role))),
        merchant,
    )


async def test_a_merchant_with_a_small_share_of_the_clicks_never_range_scans_the_whole_table(
    small_share: tuple[PartnerSessionProvider, str],
) -> None:
    provider, merchant = small_share

    found = await plans(provider, merchant)

    for name, nodes in found.items():
        indexes = {n.get("Index Name") for n in nodes}
        scanned = {(n.get("Node Type"), n.get("Relation Name")) for n in nodes}
        assert FORBIDDEN_INDEX not in indexes, (name, indexes)
        assert ("Seq Scan", "click_events") not in scanned, (name, scanned)
    assert WANTED_INDEX in {n.get("Index Name") for n in found["offers"]}
