"""End to end: the CHECK and UNIQUE constraints of ``partner_api_keys`` refuse bad rows on real Postgres.

``alembic check`` does not compare CHECK constraints, so the hand-written expressions of migration 0007 and of the
model are proved here, by inserting rows that each one must refuse. Runs only against a throwaway local ``*_test``
database (``TEST_DATABASE_URL``).
"""

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
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.database import build_engine
from app.core.ids import new_id

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
BACKEND_ROOT = Path(__file__).resolve().parents[2]
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
GOOD_ID = "a" * 24
GOOD_HASH = "0" * 64

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
    assert (target.database or "").endswith("_test")
    assert target.host in LOCAL_HOSTS
    _migrate(TEST_DATABASE_URL, "downgrade", "base")
    _migrate(TEST_DATABASE_URL, "upgrade", "head")
    yield TEST_DATABASE_URL


@pytest.fixture
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    built = build_engine(database_url)
    async with built.begin() as connection:
        await connection.execute(text("TRUNCATE merchants CASCADE"))
    yield built
    await built.dispose()


@pytest.fixture
async def merchant_id(engine: AsyncEngine) -> str:
    identifier = new_id()
    async with engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO merchants (id, name, slug) VALUES (:i, 'Shop', :s)"),
            {"i": identifier, "s": f"shop-{identifier}"},
        )
    return identifier


async def insert(engine: AsyncEngine, merchant_id: str, **overrides: Any) -> None:
    row = {
        "id": GOOD_ID,
        "merchant_id": merchant_id,
        "key_hash": GOOD_HASH,
        "label": "Feed",
        "created_by": "admin1",
        "revoked_at": None,
        **overrides,
    }
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO partner_api_keys (id, merchant_id, key_hash, label, created_by, revoked_at) "
                "VALUES (:id, :merchant_id, :key_hash, :label, :created_by, :revoked_at)"
            ),
            row,
        )


async def test_a_good_row_is_accepted(engine: AsyncEngine, merchant_id: str) -> None:
    await insert(engine, merchant_id)


@pytest.mark.parametrize(
    ("override", "constraint"),
    [
        ({"id": "a" * 23}, "ck_partner_api_keys_id_valid"),
        ({"id": "a" * 25}, "ck_partner_api_keys_id_valid"),
        ({"id": "A" * 24}, "ck_partner_api_keys_id_valid"),
        ({"id": "a" * 23 + "_"}, "ck_partner_api_keys_id_valid"),
        ({"key_hash": "0" * 63}, "ck_partner_api_keys_key_hash_valid"),
        ({"key_hash": "A" * 64}, "ck_partner_api_keys_key_hash_valid"),
        ({"key_hash": "g" * 64}, "ck_partner_api_keys_key_hash_valid"),
        ({"label": ""}, "ck_partner_api_keys_label_valid"),
        ({"label": "x" * 81}, "ck_partner_api_keys_label_valid"),
    ],
)
async def test_a_bad_value_is_refused_by_its_check(
    engine: AsyncEngine, merchant_id: str, override: dict[str, Any], constraint: str
) -> None:
    with pytest.raises(IntegrityError, match=constraint):
        await insert(engine, merchant_id, **override)


async def test_a_revoke_time_before_the_creation_time_is_refused(
    engine: AsyncEngine, merchant_id: str
) -> None:
    with pytest.raises(
        IntegrityError, match="ck_partner_api_keys_revoked_after_created"
    ):
        await insert(engine, merchant_id, revoked_at=datetime(2000, 1, 1, tzinfo=UTC))


async def test_a_duplicate_hash_is_refused(
    engine: AsyncEngine, merchant_id: str
) -> None:
    await insert(engine, merchant_id)

    with pytest.raises(IntegrityError, match="uq_partner_api_keys_key_hash"):
        await insert(engine, merchant_id, id="b" * 24)


async def test_a_key_needs_an_existing_merchant(engine: AsyncEngine) -> None:
    with pytest.raises(
        IntegrityError, match="fk_partner_api_keys_merchant_id_merchants"
    ):
        await insert(engine, "no-such-merchant")


async def test_deleting_a_merchant_deletes_its_keys(
    engine: AsyncEngine, merchant_id: str
) -> None:
    await insert(engine, merchant_id)

    async with engine.begin() as connection:
        await connection.execute(
            text("DELETE FROM merchants WHERE id = :i"), {"i": merchant_id}
        )
        remaining = (
            await connection.execute(text("SELECT count(*) FROM partner_api_keys"))
        ).scalar_one()

    assert remaining == 0
