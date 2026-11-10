"""End to end: migration 0006 backfills ``offers.commercial_basis`` from the links, and reverses.

Runs only when ``TEST_DATABASE_URL`` points at a throwaway local ``*_test`` database (same contract as
the other ``*_e2e.py`` suites). The schema is taken to revision 0005, offers and links are written the
way 0005 stored them (no basis column), then 0006 runs and the stored values are read back.
"""

import os
import subprocess
import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.database import build_engine
from app.core.ids import new_id

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


@pytest.fixture
def database_url() -> Iterator[str]:
    assert TEST_DATABASE_URL is not None
    target = make_url(TEST_DATABASE_URL)
    assert (target.database or "").endswith("_test"), (
        "refusing to rebuild a database whose name does not end in _test"
    )
    assert target.host in LOCAL_HOSTS, "refusing to rebuild a non-local database"
    _migrate(TEST_DATABASE_URL, "downgrade", "base")
    _migrate(TEST_DATABASE_URL, "upgrade", "0005")
    yield TEST_DATABASE_URL
    # Leave the schema at head for the suites that follow.
    _migrate(TEST_DATABASE_URL, "downgrade", "base")
    _migrate(TEST_DATABASE_URL, "upgrade", "head")


@pytest.fixture
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    built = build_engine(database_url)
    yield built
    await built.dispose()


async def _insert(engine: AsyncEngine, table: str, values: dict[str, Any]) -> str:
    row = {"id": new_id(), **values}
    columns, params = ", ".join(row), ", ".join(f":{name}" for name in row)
    async with engine.begin() as connection:
        await connection.execute(
            text(f"INSERT INTO {table} ({columns}) VALUES ({params})"), row
        )
    return str(row["id"])


async def _offer_with_links(
    engine: AsyncEngine,
    category_id: str,
    merchant_id: str,
    links: list[tuple[str, bool]],
) -> str:
    """An offer as 0005 stored it, with ``(provider, active)`` links."""
    offer_id = await _insert(
        engine,
        "offers",
        {
            "title": "t",
            "slug": f"offer-{new_id()}",
            "summary": "s",
            "category_id": category_id,
            "merchant_id": merchant_id,
            "offer_type": "FREE_SAMPLE",
            "image_tint": "mint",
            "image_label": "l",
            "image_alt": "a",
            "postage_kind": "FREE",
            "eligibility": "e",
            "availability": "a",
        },
    )
    for provider, active in links:
        await _insert(
            engine,
            "affiliate_links",
            {
                "offer_id": offer_id,
                "provider": provider,
                "destination_url": "https://shop.example.com/x",
                "active": active,
            },
        )
    return offer_id


async def _basis(engine: AsyncEngine) -> dict[str, str]:
    async with engine.connect() as connection:
        result = await connection.execute(
            text("SELECT id, commercial_basis FROM offers")
        )
        return {row.id: row.commercial_basis for row in result}


async def test_backfill_follows_the_active_links_and_never_sponsors(
    engine: AsyncEngine, database_url: str
) -> None:
    category_id = await _insert(
        engine,
        "categories",
        {
            "name": "B",
            "slug": f"b-{new_id()}",
            "short_name": "B",
            "description": "d",
            "intro": "i",
            "tint": "blush",
        },
    )
    merchant_id = await _insert(
        engine, "merchants", {"name": "Acme", "slug": f"acme-{new_id()}"}
    )

    def offer(links: list[tuple[str, bool]]) -> Any:
        return _offer_with_links(engine, category_id, merchant_id, links)

    cases = {
        "network_active": await offer([("awin", True)]),
        "other_network_active": await offer([("partnerize", True), ("direct", True)]),
        "network_inactive_only": await offer([("awin", False)]),
        "network_inactive_direct_active": await offer(
            [("awin", False), ("direct", True)]
        ),
        "direct_only": await offer([("direct", True)]),
        "sample_only": await offer([("sample", True)]),
        "no_links": await offer([]),
    }

    _migrate(database_url, "upgrade", "0006")

    basis = await _basis(engine)
    assert {name: basis[offer_id] for name, offer_id in cases.items()} == {
        "network_active": "AFFILIATE",
        "other_network_active": "AFFILIATE",
        "network_inactive_only": "EDITORIAL",
        "network_inactive_direct_active": "EDITORIAL",
        "direct_only": "EDITORIAL",
        "sample_only": "EDITORIAL",
        "no_links": "EDITORIAL",
    }
    assert "SPONSORED" not in basis.values()


async def test_the_column_is_required_checked_and_has_no_default(
    engine: AsyncEngine, database_url: str
) -> None:
    category_id = await _insert(
        engine,
        "categories",
        {
            "name": "B",
            "slug": f"b-{new_id()}",
            "short_name": "B",
            "description": "d",
            "intro": "i",
            "tint": "blush",
        },
    )
    merchant_id = await _insert(
        engine, "merchants", {"name": "Acme", "slug": f"acme-{new_id()}"}
    )
    _migrate(database_url, "upgrade", "0006")

    async with engine.connect() as connection:
        column = (
            await connection.execute(
                text(
                    "SELECT is_nullable, column_default FROM information_schema.columns"
                    " WHERE table_name = 'offers' AND column_name = 'commercial_basis'"
                )
            )
        ).one()
    assert tuple(column) == ("NO", None)

    async def insert_with(basis_clause: str, columns: str) -> None:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO offers (id, title, slug, summary, category_id, merchant_id,"
                    f" offer_type, {columns} image_tint, image_label, image_alt,"
                    " postage_kind, eligibility, availability)"
                    f" VALUES (:id, 't', :id, 's', :c, :m, 'FREE_SAMPLE', {basis_clause}"
                    " 'mint', 'l', 'a', 'FREE', 'e', 'a')"
                ),
                {"id": new_id(), "c": category_id, "m": merchant_id},
            )

    with pytest.raises(IntegrityError, match="commercial_basis"):
        await insert_with("", "")  # left out: no default to fall back on
    with pytest.raises(IntegrityError, match="ck_offers_commercial_basis_valid"):
        await insert_with("'PAID',", "commercial_basis,")
    await insert_with("'SPONSORED',", "commercial_basis,")


async def test_downgrade_removes_the_column(
    engine: AsyncEngine, database_url: str
) -> None:
    _migrate(database_url, "upgrade", "0006")
    _migrate(database_url, "downgrade", "0005")

    async with engine.connect() as connection:
        found = await connection.scalar(
            text(
                "SELECT count(*) FROM information_schema.columns"
                " WHERE table_name = 'offers' AND column_name = 'commercial_basis'"
            )
        )
    assert found == 0
