"""End to end: the restricted ``freejoy_partner`` database role, on real Postgres (ADR-013, FREEJ-57).

Applies the committed script (``commands/sql/partner_role.sql``), connects as the role it creates and shows what
it can and cannot do. Runs only against a throwaway local ``*_test`` database (``TEST_DATABASE_URL``).

Roles are cluster-wide and the test cluster is shared, so the role here is NOT called ``freejoy_partner``: it is
``freejoy_partner_<database name>`` (the script takes the name as a parameter), created if absent by the script,
never dropped, with its grants applied only inside this test's own database.
"""

import os
import subprocess
import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

import app.models.platform  # noqa: F401  (registers the tables)
from app.core.base import Base
from app.core.database import PartnerSessionProvider, build_engine
from app.core.ids import new_id
from app.core.partner_keys import generate_key
from app.partner.keys import PartnerKeyStore
from tests.partner_role_support import (
    SCRIPT,
    apply_partner_role,
    role_name_for,
    role_password_for,
    role_url,
    script_body,
)

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
BACKEND_ROOT = Path(__file__).resolve().parents[2]
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
ALLOWED_SELECT = {"partner_api_keys", "merchants"}

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


@pytest.fixture(scope="module")
def role(database_url: str) -> str:
    return role_name_for(make_url(database_url).database or "")


@pytest.fixture
async def owner(database_url: str, role: str) -> AsyncIterator[AsyncEngine]:
    engine = build_engine(database_url)
    await apply_partner_role(database_url, role, role_password_for(role))
    async with engine.begin() as connection:
        await connection.execute(
            text("TRUNCATE users, audit_logs, offers, categories, merchants CASCADE")
        )
    yield engine
    await engine.dispose()


@pytest.fixture
def partner_url(database_url: str, role: str) -> str:
    return role_url(database_url, role, role_password_for(role))


@pytest.fixture
async def partner(partner_url: str, owner: AsyncEngine) -> AsyncIterator[AsyncEngine]:
    engine = build_engine(partner_url)
    yield engine
    await engine.dispose()


async def denied(engine: AsyncEngine, statement: str) -> bool:
    """True when running ``statement`` as the role fails with a permission error."""
    try:
        async with engine.begin() as connection:
            await connection.execute(text(statement))
    except DBAPIError as error:
        return "permission denied" in str(error.orig).lower()
    return False


async def seed_key(owner: AsyncEngine) -> tuple[str, str]:
    """A merchant and a key row; returns (merchant id, key id)."""
    merchant_id, key = new_id(), generate_key("test", new_id())
    async with owner.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO merchants (id, name, slug, website, active) "
                "VALUES (:id, 'Shop', :slug, 'https://shop.example.com', true)"
            ),
            {"id": merchant_id, "slug": f"shop-{merchant_id}"},
        )
        await connection.execute(
            text(
                "INSERT INTO partner_api_keys (id, merchant_id, key_hash, label, created_by) "
                "VALUES (:id, :merchant, :hash, 'Feed', 'admin1')"
            ),
            {"id": key.key_id, "merchant": merchant_id, "hash": key.digest},
        )
    return merchant_id, key.key_id


# -------------------------------------------------------------------------------- what it is


async def test_the_role_has_no_special_attribute_and_no_membership(
    owner: AsyncEngine, role: str
) -> None:
    async with owner.connect() as connection:
        row = (
            await connection.execute(
                text(
                    "SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls, "
                    "rolinherit, rolcanlogin FROM pg_roles WHERE rolname = :r"
                ),
                {"r": role},
            )
        ).one()
        memberships = (
            await connection.execute(
                text(
                    "SELECT count(*) FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.member "
                    "WHERE r.rolname = :r"
                ),
                {"r": role},
            )
        ).scalar_one()

    assert tuple(row) == (False, False, False, False, False, False, True)
    assert memberships == 0


async def test_the_role_name_is_unique_to_the_database_and_is_not_the_production_name(
    role: str,
) -> None:
    assert role != "freejoy_partner"
    assert role.startswith("freejoy_partner_")
    assert len(role) <= 63


async def test_running_the_script_again_changes_nothing(
    database_url: str, role: str, partner: AsyncEngine
) -> None:
    await apply_partner_role(database_url, role, role_password_for(role))
    await apply_partner_role(database_url, role, "")

    async with partner.connect() as connection:
        assert (await connection.execute(text("SELECT 1"))).scalar_one() == 1


async def test_the_script_refuses_a_bad_role_name_and_a_missing_first_password(
    database_url: str,
) -> None:
    for bad in ("Freejoy-Partner", "x; DROP ROLE y", ""):
        with pytest.raises(Exception, match="partner_role must be"):
            await apply_partner_role(database_url, bad, "pw")
    with pytest.raises(Exception, match="partner_password is required"):
        await apply_partner_role(database_url, "freejoy_partner_never_created_x", "")


def test_the_script_defaults_to_the_production_role_name() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "\\set partner_role freejoy_partner" in source
    assert "-- parameters (begin)" in source and "-- parameters (end)" in source
    assert ":'partner_" not in script_body()  # nothing psql-specific is left to run


# ------------------------------------------------------------------------------ what it cannot do


async def test_it_cannot_read_a_table_the_partner_surface_does_not_need(
    partner: AsyncEngine,
) -> None:
    tables = sorted(set(Base.metadata.tables) - ALLOWED_SELECT)

    assert len(tables) > 15
    refused = [t for t in tables if not await denied(partner, f"SELECT 1 FROM {t}")]
    assert refused == []


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT 1 FROM users",
        "SELECT 1 FROM admin_users",
        "SELECT 1 FROM organizations",
        "SELECT 1 FROM memberships",
        "SELECT 1 FROM email_subscribers",
        "SELECT 1 FROM stripe_events",
        "SELECT 1 FROM login_history",
        "SELECT 1 FROM user_settings",
        "SELECT 1 FROM audit_logs",
        "UPDATE offers SET status = 'PUBLISHED'",
        "UPDATE users SET email = 'x'",
        "DELETE FROM offers",
        "DELETE FROM users",
        "INSERT INTO offers (id) VALUES ('x')",
    ],
)
async def test_it_is_refused_on_users_admins_billing_offers_and_the_like(
    partner: AsyncEngine, statement: str
) -> None:
    assert await denied(partner, statement)


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE merchants SET name = 'x'",
        "UPDATE merchants SET active = false",
        "DELETE FROM merchants",
        "INSERT INTO merchants (id, name, slug) VALUES ('x', 'x', 'x')",
        (
            "INSERT INTO partner_api_keys (id, merchant_id, key_hash, label, created_by) "
            "VALUES ('x', 'x', 'x', 'x', 'x')"
        ),
        "UPDATE partner_api_keys SET revoked_at = now()",
        "UPDATE partner_api_keys SET key_hash = 'x'",
        "UPDATE partner_api_keys SET label = 'x'",
        "UPDATE partner_api_keys SET merchant_id = 'x'",
        "DELETE FROM partner_api_keys",
    ],
)
async def test_it_cannot_change_merchants_and_cannot_change_a_key_except_last_used(
    partner: AsyncEngine, statement: str
) -> None:
    assert await denied(partner, statement)


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT 1 FROM audit_logs",
        "SELECT id FROM audit_logs",
        "UPDATE audit_logs SET action = 'x'",
        "DELETE FROM audit_logs",
        "TRUNCATE audit_logs",
    ],
)
async def test_the_audit_trail_is_append_only_for_the_role(
    partner: AsyncEngine, statement: str
) -> None:
    assert await denied(partner, statement)


@pytest.mark.parametrize(
    "statement",
    [
        "CREATE TABLE partner_made_this (id int)",
        "DROP TABLE partner_api_keys",
        "ALTER TABLE partner_api_keys ADD COLUMN x int",
        "ALTER TABLE merchants DROP COLUMN name",
        "CREATE INDEX ix_partner_made_this ON merchants (name)",
        "CREATE SCHEMA partner_made_this",
        "CREATE ROLE partner_made_this",
        "TRUNCATE merchants",
        "TRUNCATE partner_api_keys",
        "GRANT SELECT ON users TO PUBLIC",
        "DROP ROLE postgres",
    ],
)
async def test_it_can_run_no_ddl(partner: AsyncEngine, statement: str) -> None:
    async with partner.connect() as connection:
        with pytest.raises(DBAPIError) as caught:
            await connection.execute(text(statement))

    message = str(caught.value.orig).lower()
    assert "permission denied" in message or "must be" in message


# ------------------------------------------------------------------------------ what it can do


async def test_the_key_lookup_works_as_the_role(
    owner: AsyncEngine, partner_url: str, partner: AsyncEngine
) -> None:
    merchant_id, key_id = await seed_key(owner)
    provider = PartnerSessionProvider(partner_url)

    async with provider.session() as session:
        store = PartnerKeyStore(session)
        stored = await store.find(key_id)
        profile = await store.profile(key_id)
        missing = await store.find("nosuchkey000000000000000")

    assert stored is not None
    assert (stored.merchant_id, stored.usable) == (merchant_id, True)
    assert profile is not None
    assert (profile.merchant_name, profile.key_label) == ("Shop", "Feed")
    assert missing is None


async def test_the_role_can_move_last_used_at_and_nothing_else_on_the_key(
    owner: AsyncEngine, partner_url: str, partner: AsyncEngine
) -> None:
    _, key_id = await seed_key(owner)
    provider = PartnerSessionProvider(partner_url)

    async with provider.session() as session:
        await PartnerKeyStore(session).touch(key_id)
        await session.commit()
    async with owner.connect() as connection:
        used = (
            await connection.execute(
                text("SELECT last_used_at FROM partner_api_keys WHERE id = :i"),
                {"i": key_id},
            )
        ).scalar_one()

    assert used is not None


async def test_the_role_can_insert_an_audit_row_and_cannot_read_it_back(
    owner: AsyncEngine, partner: AsyncEngine
) -> None:
    async with partner.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO audit_logs (id, action, resource) "
                "VALUES ('partnerauditrow000000000', 'partner.test', 'partner_api_keys')"
            )
        )

    async with owner.connect() as connection:
        found = (
            await connection.execute(
                text(
                    "SELECT count(*) FROM audit_logs WHERE id = 'partnerauditrow000000000'"
                )
            )
        ).scalar_one()
    assert found == 1
    assert await denied(partner, "SELECT id FROM audit_logs")


async def test_a_revoked_key_or_an_inactive_merchant_is_found_but_not_usable(
    owner: AsyncEngine, partner_url: str, partner: AsyncEngine
) -> None:
    merchant_id, key_id = await seed_key(owner)
    provider = PartnerSessionProvider(partner_url)
    async with owner.begin() as connection:
        await connection.execute(
            text("UPDATE partner_api_keys SET revoked_at = now() WHERE id = :i"),
            {"i": key_id},
        )
    async with provider.session() as session:
        revoked = await PartnerKeyStore(session).find(key_id)
    async with owner.begin() as connection:
        await connection.execute(
            text("UPDATE partner_api_keys SET revoked_at = NULL WHERE id = :i"),
            {"i": key_id},
        )
        await connection.execute(
            text("UPDATE merchants SET active = false WHERE id = :m"),
            {"m": merchant_id},
        )
    async with provider.session() as session:
        dormant = await PartnerKeyStore(session).find(key_id)

    assert revoked is not None and revoked.usable is False
    assert dormant is not None and dormant.usable is False
