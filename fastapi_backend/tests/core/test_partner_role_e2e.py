"""End to end: the restricted ``freejoy_partner`` database role, on real Postgres (ADR-013, FREEJ-57).

Applies the committed script (``commands/sql/partner_role.sql``), connects as the role it creates and shows what
it can and cannot do, asking the CATALOG what the role holds (every relation, every column, every privilege type,
privileges that reach it through PUBLIC) rather than a hand list. Runs only against a throwaway local ``*_test``
database (``TEST_DATABASE_URL``).

Roles are cluster-wide and the test cluster is shared, so the role here is NOT called ``freejoy_partner``: it is
``freejoy_partner_<database name>`` (the script takes the name as a parameter), created if absent by the script,
never dropped, with its grants applied only inside this test's own database. The script is applied once per
module; the tests that dirty the role apply it again at the end.
"""

import asyncio
import os
import subprocess
import sys
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from pathlib import Path

import asyncpg  # type: ignore[import-untyped]
import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

import app.models.platform  # noqa: F401  (registers the tables)
from app.core.database import (
    PartnerRoleNotRestricted,
    PartnerSessionProvider,
    build_engine,
)
from app.core.ids import new_id
from app.core.partner_keys import generate_key
from app.partner.clicks_store import PartnerClickStore
from app.partner.keys import PartnerKeyStore
from tests.api.test_catalogue_e2e import add_category, add_offer
from tests.partner_role_support import (
    DEFAULT_CONNECTION_LIMIT,
    apply_partner_role,
    asyncpg_dsn,
    role_name_for,
    role_password_for,
    role_url,
    script_body,
)

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
BACKEND_ROOT = Path(__file__).resolve().parents[2]
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}

# Exactly what the role may hold in public: column privileges, nothing at table level. Written out here by hand,
# independently of the script, so a slip in either shows up.
SELECT_COLUMNS = {
    "partner_api_keys": "id merchant_id key_hash label revoked_at last_used_at",
    "merchants": "id name website active",
    "offers": (
        "id title slug summary description category_id merchant_id offer_type status featured image_tint "
        "image_label image_alt source_url postage_kind postage_amount_minor postage_currency requires_card "
        "subscription_terms eligibility availability steps start_at end_at last_verified_at freshness "
        "publisher_notes"
    ),
    "affiliate_links": "id offer_id provider destination_url tracking_id active",
    "offer_imports": (
        "id provider external_id offer_id source_hash applied_source_hash applied_content_hash applied_at "
        "source_changed_at"
    ),
    "categories": "id slug enabled",
    "partner_submissions": (
        "id merchant_id ref offer_id content_hash pending_payload pending_hash pending_at submitted_at "
        "key_id first_published_at created_at"
    ),
    "click_events": "offer_id occurred_at user_agent_class",
}
INSERT_COLUMNS = {
    "offers": (
        "id title slug summary description category_id merchant_id offer_type image_tint image_label image_alt "
        "postage_kind postage_amount_minor postage_currency requires_card subscription_terms eligibility "
        "availability steps start_at end_at"
    ),
    "affiliate_links": "id offer_id destination_url",
    "offer_imports": (
        "id provider external_id offer_id source_hash applied_source_hash applied_content_hash applied_at"
    ),
    "partner_submissions": "id merchant_id ref offer_id content_hash key_id",
    "audit_logs": "id action resource resource_id metadata",
}
UPDATE_COLUMNS = {
    "partner_api_keys": "last_used_at",
    "offers": (
        "title summary description category_id offer_type postage_kind postage_amount_minor postage_currency "
        "requires_card subscription_terms eligibility availability steps start_at end_at updated_at"
    ),
    "affiliate_links": "destination_url updated_at",
    "offer_imports": "source_hash applied_source_hash applied_content_hash applied_at source_changed_at updated_at",
    "partner_submissions": (
        "content_hash pending_payload pending_hash pending_at submitted_at key_id updated_at"
    ),
}
EXPECTED_COLUMN_PRIVILEGES = {
    (table, column, privilege)
    for privilege, columns in (
        ("SELECT", SELECT_COLUMNS),
        ("INSERT", INSERT_COLUMNS),
        ("UPDATE", UPDATE_COLUMNS),
    )
    for table, names in columns.items()
    for column in names.split()
}
# Tables the role may touch at all; the "refused everywhere else" tests skip them.
PARTNER_TABLES = sorted(
    {
        name
        for mapping in (SELECT_COLUMNS, INSERT_COLUMNS, UPDATE_COLUMNS)
        for name in mapping
    }
)
TABLE_PRIVILEGES = "SELECT, INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER"
CONFIGURE = (
    "SELECT set_config('freejoy.partner_role', $1, true), "
    "set_config('freejoy.partner_password', $2, true), "
    "set_config('freejoy.partner_connection_limit', $3, true)"
)

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
    name = role_name_for(make_url(database_url).database or "")
    asyncio.run(apply_partner_role(database_url, name, role_password_for(name)))
    return name


@pytest.fixture
async def owner(database_url: str, role: str) -> AsyncIterator[AsyncEngine]:
    engine = build_engine(database_url)
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


async def catalog_privileges(
    owner: AsyncEngine, role: str
) -> tuple[set[tuple[str, str]], set[tuple[str, str, str]], set[str]]:
    """(table-level, column-level, sequence-level) privileges the role holds in public, from the catalog."""
    relations = "c.relkind IN ('r', 'p', 'v', 'm', 'f') AND n.nspname = 'public'"
    async with owner.connect() as connection:
        tables = (
            await connection.execute(
                text(
                    "SELECT c.relname, p.priv FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    f"CROSS JOIN unnest(string_to_array('{TABLE_PRIVILEGES}', ', ')) AS p(priv) "
                    f"WHERE {relations} AND has_table_privilege(:r, c.oid, p.priv)"
                ),
                {"r": role},
            )
        ).all()
        columns = (
            await connection.execute(
                text(
                    "SELECT c.relname, a.attname, p.priv FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped "
                    "CROSS JOIN unnest(ARRAY['SELECT', 'INSERT', 'UPDATE', 'REFERENCES']) AS p(priv) "
                    f"WHERE {relations} AND has_column_privilege(:r, c.oid, a.attnum, p.priv)"
                ),
                {"r": role},
            )
        ).all()
        sequences = (
            await connection.execute(
                text(
                    "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "CROSS JOIN unnest(ARRAY['USAGE', 'SELECT', 'UPDATE']) AS p(priv) "
                    "WHERE c.relkind = 'S' AND n.nspname = 'public' "
                    "AND has_sequence_privilege(:r, c.oid, p.priv)"
                ),
                {"r": role},
            )
        ).all()
    return (
        {(a, b) for a, b in tables},
        {(a, b, c) for a, b, c in columns},
        {row[0] for row in sequences},
    )


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


async def role_exists(owner: AsyncEngine, name: str) -> bool:
    async with owner.connect() as connection:
        found = await connection.execute(
            text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": name}
        )
        return found.scalar_one_or_none() is not None


# -------------------------------------------------------------------------------- what it is


async def test_the_role_has_no_special_attribute_a_connection_limit_and_no_membership(
    owner: AsyncEngine, role: str
) -> None:
    async with owner.connect() as connection:
        row = (
            await connection.execute(
                text(
                    "SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls, "
                    "rolinherit, rolcanlogin, rolconnlimit FROM pg_roles WHERE rolname = :r"
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

    assert tuple(row) == (
        False,
        False,
        False,
        False,
        False,
        False,
        True,
        DEFAULT_CONNECTION_LIMIT,
    )
    assert memberships == 0


async def test_the_role_carries_its_three_timeouts(
    owner: AsyncEngine, partner: AsyncEngine
) -> None:
    async with partner.connect() as connection:
        shown = {
            name: (await connection.execute(text(f"SHOW {name}"))).scalar_one()
            for name in (
                "statement_timeout",
                "lock_timeout",
                "idle_in_transaction_session_timeout",
            )
        }

    assert shown == {
        "statement_timeout": "10s",
        "lock_timeout": "2s",
        "idle_in_transaction_session_timeout": "15s",
    }


async def test_the_role_name_is_unique_to_the_database_and_is_not_the_production_name(
    role: str,
) -> None:
    assert role != "freejoy_partner"
    assert role.startswith("freejoy_partner_")
    assert len(role) <= 63


async def test_the_connection_limit_is_enforced(
    partner_url: str, owner: AsyncEngine
) -> None:
    url = make_url(partner_url)
    connections = []
    try:
        for _ in range(DEFAULT_CONNECTION_LIMIT):
            connections.append(await asyncpg.connect(**asyncpg_dsn(url)))
        with pytest.raises(asyncpg.TooManyConnectionsError):
            await asyncpg.connect(**asyncpg_dsn(url))
    finally:
        for connection in connections:
            await connection.close()


async def test_running_the_script_again_changes_nothing(
    database_url: str, role: str, owner: AsyncEngine
) -> None:
    before = await catalog_privileges(owner, role)

    await apply_partner_role(database_url, role, role_password_for(role))
    await apply_partner_role(database_url, role, "")

    assert await catalog_privileges(owner, role) == before


async def test_the_script_refuses_a_bad_name_a_bad_limit_the_runner_and_a_missing_first_password(
    database_url: str,
) -> None:
    for bad in ("Freejoy-Partner", "x; DROP ROLE y", ""):
        with pytest.raises(Exception, match="partner_role must be"):
            await apply_partner_role(database_url, bad, "pw")
    with pytest.raises(Exception, match="partner_connection_limit"):
        await apply_partner_role(database_url, "freejoy_partner_never_x", "pw", 0)
    with pytest.raises(Exception, match="PARTNER_ROLE_PASSWORD is required"):
        await apply_partner_role(database_url, "freejoy_partner_never_created_x", "")
    runner = make_url(database_url).username or ""
    with pytest.raises(Exception, match="must not be the role that runs"):
        await apply_partner_role(database_url, runner, "pw")


# ------------------------------------------------------- what it holds, from the catalog


async def test_the_catalog_says_the_role_holds_exactly_the_listed_column_privileges(
    owner: AsyncEngine, role: str
) -> None:
    tables, columns, sequences = await catalog_privileges(owner, role)

    assert tables == set()
    assert columns == EXPECTED_COLUMN_PRIVILEGES
    assert sequences == set()
    async with owner.connect() as connection:
        creates = (
            await connection.execute(
                text(
                    "SELECT has_schema_privilege(:r, 'public', 'CREATE'), "
                    "has_database_privilege(:r, current_database(), 'CREATE')"
                ),
                {"r": role},
            )
        ).one()
        default_acls = (
            await connection.execute(
                text(
                    "SELECT count(*) FROM pg_default_acl d "
                    "WHERE d.defaclacl::text LIKE '%' || :r || '=%'"
                ),
                {"r": role},
            )
        ).scalar_one()
    assert tuple(creates) == (False, False)
    assert default_acls == 0


async def test_dirty_grants_are_taken_away_by_the_next_run(
    database_url: str, role: str, owner: AsyncEngine
) -> None:
    async with owner.begin() as connection:
        for statement in (
            f'GRANT SELECT ON users TO "{role}"',
            f'GRANT UPDATE (name) ON merchants TO "{role}"',
            f'GRANT INSERT ON audit_logs TO "{role}"',
            f'GRANT SELECT ON merchants TO "{role}"',
        ):
            await connection.execute(text(statement))
    dirty_tables, dirty_columns, _ = await catalog_privileges(owner, role)
    assert dirty_tables
    assert ("merchants", "name", "UPDATE") in dirty_columns

    await apply_partner_role(database_url, role, "")

    tables, columns, _ = await catalog_privileges(owner, role)
    assert tables == set()
    assert columns == EXPECTED_COLUMN_PRIVILEGES


async def test_a_membership_makes_the_script_raise(
    database_url: str, role: str, owner: AsyncEngine
) -> None:
    async with owner.begin() as connection:
        await connection.execute(text(f'GRANT pg_read_all_data TO "{role}"'))
    try:
        with pytest.raises(Exception, match="member of another role"):
            await apply_partner_role(database_url, role, "")
    finally:
        async with owner.begin() as connection:
            await connection.execute(text(f'REVOKE pg_read_all_data FROM "{role}"'))
    await apply_partner_role(database_url, role, "")


async def test_a_public_grant_makes_the_script_raise_and_is_rolled_back(
    database_url: str, role: str, owner: AsyncEngine
) -> None:
    connection = await asyncpg.connect(**asyncpg_dsn(make_url(database_url)))
    try:
        transaction = connection.transaction()
        await transaction.start()
        await connection.execute("GRANT SELECT ON users TO PUBLIC")
        await connection.execute(CONFIGURE, role, "", "20")
        with pytest.raises(asyncpg.PostgresError, match="verification failed"):
            await connection.execute(script_body())
        await transaction.rollback()
    finally:
        await connection.close()

    async with owner.connect() as check:
        public_grants = (
            await check.execute(
                text(
                    "SELECT count(*) FROM information_schema.role_table_grants "
                    "WHERE grantee = 'PUBLIC' AND table_name = 'users'"
                )
            )
        ).scalar_one()
    assert public_grants == 0


async def test_a_role_that_holds_a_special_attribute_is_refused_not_altered(
    database_url: str, owner: AsyncEngine
) -> None:
    name = role_name_for(make_url(database_url).database or "")[:58] + "_sa"
    if not await role_exists(owner, name):
        async with owner.begin() as connection:
            await connection.execute(text(f'CREATE ROLE "{name}" LOGIN CREATEDB'))

    with pytest.raises(Exception, match="special attribute"):
        await apply_partner_role(database_url, name, "")

    async with owner.connect() as connection:
        still = (
            await connection.execute(
                text("SELECT rolcreatedb FROM pg_roles WHERE rolname = :r"), {"r": name}
            )
        ).scalar_one()
    assert still is True  # refused, not altered


async def test_the_script_runs_as_a_non_superuser_that_may_create_roles(
    database_url: str, owner: AsyncEngine
) -> None:
    """A managed Postgres gives the owner CREATEROLE, not SUPERUSER: the script must work as such a role."""
    base = make_url(database_url).database or ""
    runner = f"freejoy_owner_{base}"[:63]
    target = role_name_for(base)[:55] + "_nsu"
    if not await role_exists(owner, runner):
        async with owner.begin() as connection:
            await connection.execute(
                text(f'CREATE ROLE "{runner}" NOLOGIN CREATEROLE NOSUPERUSER')
            )
            await connection.execute(
                text(f'GRANT "{make_url(database_url).username}" TO "{runner}"')
            )
    connection = await asyncpg.connect(**asyncpg_dsn(make_url(database_url)))
    try:
        async with connection.transaction():
            await connection.execute(f'SET LOCAL ROLE "{runner}"')
            superuser = await connection.fetchval(
                "SELECT rolsuper FROM pg_roles WHERE rolname = current_user"
            )
            assert superuser is False
            await connection.execute(CONFIGURE, target, "pw-non-superuser-test", "5")
            await connection.execute(script_body())
    finally:
        await connection.close()

    async with owner.connect() as check:
        limit = (
            await check.execute(
                text("SELECT rolconnlimit FROM pg_roles WHERE rolname = :r"),
                {"r": target},
            )
        ).scalar_one()
    assert limit == 5


# ------------------------------------------------------------------------------ what it cannot do


async def test_it_cannot_read_a_table_the_partner_surface_does_not_need(
    owner: AsyncEngine, partner: AsyncEngine
) -> None:
    async with owner.connect() as connection:
        tables = [
            row[0]
            for row in (
                await connection.execute(
                    text(
                        "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm', 'f') "
                        "AND c.relname <> ALL (:partner_tables) ORDER BY 1"
                    ),
                    {"partner_tables": PARTNER_TABLES},
                )
            ).all()
        ]

    assert len(tables) > 15
    assert "alembic_version" in tables  # from the catalog, not from the ORM
    refused = [t for t in tables if not await denied(partner, f'SELECT 1 FROM "{t}"')]
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
        "UPDATE offers SET status = 'PUBLISHED'",
        "UPDATE users SET email = 'x'",
        "DELETE FROM offers",
        "DELETE FROM users",
        "UPDATE offers SET merchant_id = 'x'",
        "INSERT INTO click_events (id, offer_id) VALUES ('x', 'x')",
        "UPDATE click_events SET user_agent_class = 'OTHER'",
        "UPDATE click_events SET occurred_at = now()",
        "DELETE FROM click_events",
        "TRUNCATE click_events",
        "TRUNCATE offers",
    ],
)
async def test_it_is_refused_on_users_admins_billing_offers_and_the_like(
    partner: AsyncEngine, statement: str
) -> None:
    assert await denied(partner, statement)


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT created_by FROM partner_api_keys",
        "SELECT * FROM partner_api_keys",
        "SELECT slug FROM merchants",
        "SELECT logo_url FROM merchants",
        "SELECT * FROM merchants",
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
async def test_it_reads_only_the_listed_columns_and_changes_nothing_but_last_used(
    partner: AsyncEngine, statement: str
) -> None:
    assert await denied(partner, statement)


# (The offer columns the submissions of FREEJ-58 read - summary, description, source_url, publisher_notes and the
# others of the grant - are no longer refused: only the columns still outside the grant are listed here.)
@pytest.mark.parametrize(
    "statement",
    [
        "SELECT commercial_basis FROM offers",
        "SELECT * FROM offers",
        "SELECT referrer_host FROM click_events",
        "SELECT affiliate_link_id FROM click_events",
        "SELECT id FROM click_events",
        "SELECT * FROM click_events",
        "SELECT offer_id, referrer_host FROM click_events",
    ],
)
async def test_it_reads_only_the_click_columns_the_query_needs(
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
async def test_the_role_has_no_access_to_the_audit_trail_at_all(
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
        "CREATE INDEX ix_partner_made_this ON click_events (offer_id)",
        "DROP TABLE click_events",
        "DROP TABLE offers",
        "ALTER TABLE click_events ADD COLUMN x int",
        "CREATE SCHEMA partner_made_this",
        "CREATE ROLE partner_made_this",
        "TRUNCATE merchants",
        "TRUNCATE partner_api_keys",
        "GRANT SELECT ON users TO PUBLIC",
        "DROP ROLE postgres",
    ],
)
async def test_it_can_run_no_ddl_in_public(
    partner: AsyncEngine, statement: str
) -> None:
    async with partner.connect() as connection:
        with pytest.raises(DBAPIError) as caught:
            await connection.execute(text(statement))

    message = str(caught.value.orig).lower()
    assert "permission denied" in message or "must be" in message


async def test_temporary_tables_are_the_accepted_residual_it_keeps_through_public(
    partner: AsyncEngine,
) -> None:
    """Documented: TEMP on the database reaches every role through PUBLIC, so 'no DDL' means no DDL in public."""
    async with partner.begin() as connection:
        await connection.execute(
            text("CREATE TEMP TABLE partner_scratch (id int) ON COMMIT DROP")
        )


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
    assert (stored.merchant_id, stored.usable, stored.touch_due) == (
        merchant_id,
        True,
        True,
    )
    assert profile is not None
    assert (profile.merchant_name, profile.key_label) == ("Shop", "Feed")
    assert missing is None


async def test_the_role_runs_the_clicks_query_and_nothing_of_another_merchant_comes_back(
    owner: AsyncEngine, partner_url: str, partner: AsyncEngine
) -> None:
    mine, _ = await seed_key(owner)
    other, _ = await seed_key(owner)
    category = await add_category(owner, "beauty")
    mine_offer = await add_offer(owner, category, mine, "mine")
    theirs = await add_offer(owner, category, other, "theirs")
    at = datetime(2026, 3, 10, 9, 0, tzinfo=UTC)
    for offer in (mine_offer, theirs, theirs):
        await seed_click(owner, offer, at)
    start, end = datetime(2026, 3, 10, tzinfo=UTC), datetime(2026, 3, 11, tzinfo=UTC)
    provider = PartnerSessionProvider(partner_url)

    async with provider.session() as session:
        store = PartnerClickStore(session)
        page = await store.offers(mine, start, end, 100)
        days = await store.daily(mine, [mine_offer, theirs], start, end)

    assert [(o.offer_id, o.clicks) for o in page.offers] == [(mine_offer, 1)]
    assert [(d.offer_id, d.clicks) for d in days] == [(mine_offer, 1)]


async def seed_click(owner: AsyncEngine, offer_id: str, at: datetime) -> None:
    async with owner.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO click_events (id, offer_id, occurred_at, user_agent_class) "
                "VALUES (:id, :offer, :at, 'DESKTOP')"
            ),
            {"id": new_id(), "offer": offer_id, "at": at},
        )


async def test_the_role_can_move_last_used_at_and_the_lookup_then_says_it_is_not_due(
    owner: AsyncEngine, partner_url: str, partner: AsyncEngine
) -> None:
    _, key_id = await seed_key(owner)
    provider = PartnerSessionProvider(partner_url)

    async with provider.session() as session:
        await PartnerKeyStore(session).touch(key_id)
        await session.commit()
    async with provider.session() as session:
        after = await PartnerKeyStore(session).find(key_id)
    async with owner.connect() as connection:
        used = (
            await connection.execute(
                text("SELECT last_used_at FROM partner_api_keys WHERE id = :i"),
                {"i": key_id},
            )
        ).scalar_one()

    assert used is not None
    assert after is not None and after.touch_due is False


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


async def test_a_provider_built_with_the_owner_url_is_refused_by_the_live_check(
    database_url: str, owner: AsyncEngine
) -> None:
    provider = PartnerSessionProvider(database_url)

    with pytest.raises(PartnerRoleNotRestricted):
        async with provider.session():
            pass


async def test_accepted_residual_any_sql_on_the_partner_session_reads_every_merchants_offers_and_clicks(
    owner: AsyncEngine, partner: AsyncEngine
) -> None:
    """Documented (ADR-013): column grants cannot limit rows, so tenancy rests on the WHERE clauses of the code.

    Whoever can run SQL on the partner session (a flaw in partner code, not a retailer) can read the granted columns of
    EVERY merchant's offers, drafts included, and of every click. No visitor data is among them
    (``test_it_reads_only_the_click_columns_the_query_needs``).
    """
    mine, _ = await seed_key(owner)
    theirs, _ = await seed_key(owner)
    category = await add_category(owner, "beauty")
    published = await add_offer(owner, category, mine, "mine")
    draft = await add_offer(
        owner, category, theirs, "their-draft", status="DRAFT", last_verified_at=None
    )
    await seed_click(owner, published, datetime(2026, 3, 10, 9, 0, tzinfo=UTC))
    await seed_click(owner, draft, datetime(2026, 3, 10, 9, 0, tzinfo=UTC))

    async with partner.connect() as connection:
        offers = (
            await connection.execute(
                text("SELECT id, merchant_id, title, slug, status FROM offers")
            )
        ).all()
        clicks = (
            await connection.execute(
                text("SELECT offer_id, occurred_at, user_agent_class FROM click_events")
            )
        ).all()

    assert {(row.merchant_id, row.status) for row in offers} == {
        (mine, "PUBLISHED"),
        (theirs, "DRAFT"),
    }
    assert {row.offer_id for row in clicks} == {published, draft}
