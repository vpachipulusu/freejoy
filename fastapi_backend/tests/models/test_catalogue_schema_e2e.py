"""End to end: the catalogue tables created by migration 0002 enforce their rules.

Runs only when ``TEST_DATABASE_URL`` points at a throwaway local database whose name ends
in "_test" (same contract as the other ``*_e2e.py`` suites). It rebuilds the schema with
Alembic, then proves the constraints the models promise: the postage rule, lower-case
subscriber email, unique slugs, delete behaviour of every foreign key, and the defaults.
"""

import enum
import os
import subprocess
import sys
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.core.database import build_engine
from app.core.ids import new_id
from app.models.platform import (
    AlertFrequency,
    CommercialBasis,
    OfferFreshness,
    OfferStatus,
    OfferType,
    PostageKind,
    SubscriberStatus,
    UserAgentClass,
)

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
BACKEND_ROOT = Path(__file__).resolve().parents[2]
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="TEST_DATABASE_URL is not set"
)


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
    yield built
    await built.dispose()


@pytest.fixture
async def conn(engine: AsyncEngine) -> AsyncIterator[AsyncConnection]:
    """A connection whose transaction is rolled back, so tests leave no rows."""
    async with engine.connect() as connection:
        transaction = await connection.begin()
        yield connection
        await transaction.rollback()


async def insert_category(conn: AsyncConnection, slug: str | None = None) -> str:
    category_id = new_id()
    await conn.execute(
        text(
            "INSERT INTO categories (id, name, slug, short_name, description, intro, tint)"
            " VALUES (:id, 'Beauty', :slug, 'Beauty', 'd', 'i', 'blush')"
        ),
        {"id": category_id, "slug": slug or f"beauty-{category_id}"},
    )
    return category_id


async def insert_merchant(conn: AsyncConnection) -> str:
    merchant_id = new_id()
    await conn.execute(
        text("INSERT INTO merchants (id, name, slug) VALUES (:id, 'Acme', :slug)"),
        {"id": merchant_id, "slug": f"acme-{merchant_id}"},
    )
    return merchant_id


async def insert_row(conn: AsyncConnection, table: str, values: dict[str, Any]) -> str:
    """Insert exactly the given columns, so a column left out takes its server default."""
    row = {"id": new_id(), **values}
    columns = ", ".join(row)
    params = ", ".join(f":{name}" for name in row)
    await conn.execute(text(f"INSERT INTO {table} ({columns}) VALUES ({params})"), row)
    return str(row["id"])


async def insert_offer(
    conn: AsyncConnection,
    category_id: str,
    merchant_id: str,
    **overrides: Any,
) -> str:
    offer_id = new_id()
    return await insert_row(
        conn,
        "offers",
        {
            "id": offer_id,
            "title": "t",
            "slug": f"offer-{offer_id}",
            "summary": "s",
            "category_id": category_id,
            "merchant_id": merchant_id,
            "offer_type": "FREE_SAMPLE",
            "commercial_basis": "EDITORIAL",
            "image_tint": "mint",
            "image_label": "l",
            "image_alt": "a",
            "postage_kind": "FREE",
            "eligibility": "e",
            "availability": "a",
            **overrides,
        },
    )


async def insert_click(conn: AsyncConnection, offer_id: str, **overrides: Any) -> str:
    return await insert_row(conn, "click_events", {"offer_id": offer_id, **overrides})


async def insert_subscriber(conn: AsyncConnection, email: str, **overrides: Any) -> str:
    return await insert_row(
        conn,
        "email_subscribers",
        {
            "email": email,
            "frequency": "WEEKLY",
            "consent_text": "ok",
            "consent_at": datetime.now(UTC),
            "consent_source": "footer",
            **overrides,
        },
    )


async def scalar(conn: AsyncConnection, sql: str, **params: Any) -> Any:
    return (await conn.execute(text(sql), params)).scalar_one()


async def test_offer_defaults_to_draft_with_empty_json_lists(
    conn: AsyncConnection,
) -> None:
    offer_id = await insert_offer(
        conn, await insert_category(conn), await insert_merchant(conn)
    )

    row = (
        await conn.execute(
            text(
                "SELECT status, featured, requires_card, description, steps"
                " FROM offers WHERE id = :id"
            ),
            {"id": offer_id},
        )
    ).one()

    assert tuple(row) == ("DRAFT", False, False, [], [])


@pytest.mark.parametrize(
    ("kind", "amount", "currency"),
    [("FREE", None, None), ("CHECK_TERMS", None, None), ("COST", 299, "GBP")],
)
async def test_postage_accepts_consistent_combinations(
    conn: AsyncConnection, kind: str, amount: int | None, currency: str | None
) -> None:
    category_id = await insert_category(conn)
    merchant_id = await insert_merchant(conn)

    offer_id = await insert_offer(
        conn,
        category_id,
        merchant_id,
        postage_kind=kind,
        postage_amount_minor=amount,
        postage_currency=currency,
    )

    assert offer_id


@pytest.mark.parametrize(
    ("kind", "amount", "currency"),
    [
        ("COST", None, None),
        ("COST", 299, None),
        ("COST", None, "GBP"),
        ("COST", 299, "GB"),
        ("COST", 299, "gbp"),
        ("COST", -1, "GBP"),
        ("FREE", 299, "GBP"),
        ("FREE", 299, None),
        ("CHECK_TERMS", None, "GBP"),
    ],
)
async def test_postage_rejects_inconsistent_combinations(
    conn: AsyncConnection, kind: str, amount: int | None, currency: str | None
) -> None:
    category_id = await insert_category(conn)
    merchant_id = await insert_merchant(conn)

    with pytest.raises(IntegrityError, match="ck_offers_postage_cost_complete"):
        await insert_offer(
            conn,
            category_id,
            merchant_id,
            postage_kind=kind,
            postage_amount_minor=amount,
            postage_currency=currency,
        )


async def test_offer_rejects_an_unknown_status(conn: AsyncConnection) -> None:
    category_id = await insert_category(conn)
    merchant_id = await insert_merchant(conn)

    with pytest.raises(IntegrityError, match="ck_offers_status_valid"):
        await insert_offer(conn, category_id, merchant_id, status="LIVE")


async def test_duplicate_category_slug_is_rejected(conn: AsyncConnection) -> None:
    await insert_category(conn, slug="home")

    with pytest.raises(IntegrityError, match="uq_categories_slug"):
        await insert_category(conn, slug="home")


async def test_duplicate_offer_slug_is_rejected(conn: AsyncConnection) -> None:
    category_id = await insert_category(conn)
    merchant_id = await insert_merchant(conn)
    await insert_offer(conn, category_id, merchant_id, slug="same")

    with pytest.raises(IntegrityError, match="uq_offers_slug"):
        await insert_offer(conn, category_id, merchant_id, slug="same")


async def test_category_with_offers_cannot_be_deleted(conn: AsyncConnection) -> None:
    category_id = await insert_category(conn)
    await insert_offer(conn, category_id, await insert_merchant(conn))

    with pytest.raises(IntegrityError, match="fk_offers_category_id_categories"):
        await conn.execute(
            text("DELETE FROM categories WHERE id = :id"), {"id": category_id}
        )


async def test_merchant_with_offers_cannot_be_deleted(conn: AsyncConnection) -> None:
    merchant_id = await insert_merchant(conn)
    await insert_offer(conn, await insert_category(conn), merchant_id)

    with pytest.raises(IntegrityError, match="fk_offers_merchant_id_merchants"):
        await conn.execute(
            text("DELETE FROM merchants WHERE id = :id"), {"id": merchant_id}
        )


async def insert_link_and_click(
    conn: AsyncConnection, offer_id: str
) -> tuple[str, str]:
    link_id, click_id = new_id(), new_id()
    await conn.execute(
        text(
            "INSERT INTO affiliate_links (id, offer_id, provider, destination_url)"
            " VALUES (:id, :offer_id, 'direct', 'https://example.com/x')"
        ),
        {"id": link_id, "offer_id": offer_id},
    )
    await conn.execute(
        text(
            "INSERT INTO click_events (id, offer_id, affiliate_link_id,"
            " user_agent_class) VALUES (:id, :offer_id, :link_id, 'MOBILE')"
        ),
        {"id": click_id, "offer_id": offer_id, "link_id": link_id},
    )
    return link_id, click_id


async def test_deleting_an_offer_removes_its_links_and_clicks(
    conn: AsyncConnection,
) -> None:
    offer_id = await insert_offer(
        conn, await insert_category(conn), await insert_merchant(conn)
    )
    await insert_link_and_click(conn, offer_id)

    await conn.execute(text("DELETE FROM offers WHERE id = :id"), {"id": offer_id})

    remaining = await scalar(
        conn,
        "SELECT (SELECT count(*) FROM affiliate_links WHERE offer_id = :id)"
        " + (SELECT count(*) FROM click_events WHERE offer_id = :id)",
        id=offer_id,
    )
    assert remaining == 0


async def test_deleting_an_affiliate_link_keeps_the_click_without_a_link(
    conn: AsyncConnection,
) -> None:
    offer_id = await insert_offer(
        conn, await insert_category(conn), await insert_merchant(conn)
    )
    link_id, click_id = await insert_link_and_click(conn, offer_id)

    await conn.execute(
        text("DELETE FROM affiliate_links WHERE id = :id"), {"id": link_id}
    )

    link = await scalar(
        conn, "SELECT affiliate_link_id FROM click_events WHERE id = :id", id=click_id
    )
    assert link is None


async def test_click_event_records_the_time_by_default(conn: AsyncConnection) -> None:
    offer_id = await insert_offer(
        conn, await insert_category(conn), await insert_merchant(conn)
    )
    _, click_id = await insert_link_and_click(conn, offer_id)

    occurred_at = await scalar(
        conn, "SELECT occurred_at FROM click_events WHERE id = :id", id=click_id
    )

    assert occurred_at.tzinfo is not None
    # Truncated to the minute so it cannot be joined to a precise access-log time.
    assert occurred_at.second == 0
    assert occurred_at.microsecond == 0


async def test_click_event_rejects_an_unknown_user_agent_class(
    conn: AsyncConnection,
) -> None:
    offer_id = await insert_offer(
        conn, await insert_category(conn), await insert_merchant(conn)
    )

    with pytest.raises(IntegrityError, match="ck_click_events_user_agent_class_valid"):
        await conn.execute(
            text(
                "INSERT INTO click_events (id, offer_id, user_agent_class)"
                " VALUES (:id, :offer_id, 'SMART_FRIDGE')"
            ),
            {"id": new_id(), "offer_id": offer_id},
        )


async def test_subscriber_defaults_to_pending(conn: AsyncConnection) -> None:
    await insert_subscriber(conn, "pat@example.com")

    row = (
        await conn.execute(
            text(
                "SELECT status, categories FROM email_subscribers"
                " WHERE email = 'pat@example.com'"
            )
        )
    ).one()

    assert tuple(row) == ("PENDING", [])


async def test_subscriber_email_must_be_lower_case(conn: AsyncConnection) -> None:
    with pytest.raises(IntegrityError, match="ck_email_subscribers_email_lowercase"):
        await insert_subscriber(conn, "Pat@Example.com")


async def test_duplicate_subscriber_email_is_rejected(conn: AsyncConnection) -> None:
    await insert_subscriber(conn, "pat@example.com")

    with pytest.raises(IntegrityError, match="uq_email_subscribers_email"):
        await insert_subscriber(conn, "pat@example.com")


async def test_published_offer_without_a_verification_date_is_rejected(
    conn: AsyncConnection,
) -> None:
    category_id = await insert_category(conn)
    merchant_id = await insert_merchant(conn)

    with pytest.raises(IntegrityError, match="ck_offers_published_is_verified"):
        await insert_offer(conn, category_id, merchant_id, status="PUBLISHED")


async def test_draft_without_a_date_and_published_with_one_are_accepted(
    conn: AsyncConnection,
) -> None:
    category_id = await insert_category(conn)
    merchant_id = await insert_merchant(conn)

    await insert_offer(conn, category_id, merchant_id, status="DRAFT")
    await insert_offer(
        conn,
        category_id,
        merchant_id,
        status="PUBLISHED",
        last_verified_at=datetime.now(UTC),
    )


async def test_duplicate_confirmation_token_hash_is_rejected(
    conn: AsyncConnection,
) -> None:
    await insert_subscriber(conn, "a@example.com", confirmation_token_hash="h1")

    with pytest.raises(
        IntegrityError, match="uq_email_subscribers_confirmation_token_hash"
    ):
        await insert_subscriber(conn, "b@example.com", confirmation_token_hash="h1")


async def test_subscribers_without_a_token_hash_do_not_collide(
    conn: AsyncConnection,
) -> None:
    await insert_subscriber(conn, "a@example.com")
    await insert_subscriber(conn, "b@example.com")

    count = await scalar(
        conn,
        "SELECT count(*) FROM email_subscribers"
        " WHERE confirmation_token_hash IS NULL"
        " AND email IN ('a@example.com', 'b@example.com')",
    )
    assert count == 2


# Each enum column: every member is accepted and an unknown value is refused by its
# CHECK. `alembic check` does not compare CHECK constraints, so this is what catches a
# member added to a Python enum without a migration.
ENUM_COLUMNS = [
    ("offers", "offer_type", OfferType),
    ("offers", "commercial_basis", CommercialBasis),
    ("offers", "status", OfferStatus),
    ("offers", "postage_kind", PostageKind),
    ("offers", "freshness", OfferFreshness),
    ("click_events", "user_agent_class", UserAgentClass),
    ("email_subscribers", "status", SubscriberStatus),
    ("email_subscribers", "frequency", AlertFrequency),
]


async def insert_with(
    conn: AsyncConnection, table: str, column: str, value: str
) -> None:
    """Insert one valid row for `table` with `column` set to `value`."""
    if table == "email_subscribers":
        await insert_subscriber(
            conn, f"{new_id().lower()}@example.com", **{column: value}
        )
        return
    category_id = await insert_category(conn)
    merchant_id = await insert_merchant(conn)
    if table == "click_events":
        offer_id = await insert_offer(conn, category_id, merchant_id)
        await insert_click(conn, offer_id, **{column: value})
        return
    extra: dict[str, Any] = {"last_verified_at": datetime.now(UTC)}
    if column == "postage_kind" and value == "COST":
        extra |= {"postage_amount_minor": 299, "postage_currency": "GBP"}
    await insert_offer(conn, category_id, merchant_id, **{**extra, column: value})


@pytest.mark.parametrize(
    ("table", "column", "members"),
    [pytest.param(t, c, m, id=f"{t}.{c}") for t, c, m in ENUM_COLUMNS],
)
async def test_enum_column_accepts_every_member(
    conn: AsyncConnection, table: str, column: str, members: type[enum.Enum]
) -> None:
    for member in members:
        await insert_with(conn, table, column, member.name)


@pytest.mark.parametrize(
    ("table", "column", "members"),
    [pytest.param(t, c, m, id=f"{t}.{c}") for t, c, m in ENUM_COLUMNS],
)
async def test_enum_column_rejects_an_unknown_value(
    conn: AsyncConnection, table: str, column: str, members: type[enum.Enum]
) -> None:
    with pytest.raises(IntegrityError, match=f"ck_{table}_{column}_valid"):
        await insert_with(conn, table, column, "NOT_A_MEMBER")
