"""Text checks on ``commands/sql/partner_role.sql`` that need no database (ADR-013, FREEJ-57).

The real proof is the Postgres test (``tests/core/test_partner_role_e2e.py``) and the owner's run on the hosted
database. These checks keep the things a Postgres test on a local superuser cannot see: what the script says
about attributes (a managed Postgres refuses a non-superuser that mentions them in ALTER ROLE), what it prints,
and that its grants are the columns the partner package reads.
"""

import re
from pathlib import Path

from tests.api.partner.test_partner_table_definitions import discovered_core_tables
from tests.partner_role_support import SCRIPT, script_body

REPO_ROOT = Path(__file__).resolve().parents[3]
SQL = SCRIPT.read_text(encoding="utf-8")
GRANT_SELECT = re.compile(r"GRANT SELECT \(([^)]*)\) ON (\w+) TO", re.IGNORECASE)
GRANT_UPDATE = re.compile(r"GRANT UPDATE \(([^)]*)\) ON (\w+) TO", re.IGNORECASE)
GRANT_INSERT = re.compile(r"GRANT INSERT \(([^)]*)\) ON (\w+) TO", re.IGNORECASE)
# Columns no grant may ever cover, per table and privilege: what makes an offer live, verified, featured or
# differently paid for, how a link is paid, whose a row is, and who an audit row blames.
NEVER_INSERT = {
    "offers": {
        "status",
        "last_verified_at",
        "featured",
        "commercial_basis",
        "freshness",
        "publisher_notes",
    },
    "affiliate_links": {"provider", "active", "tracking_id"},
    "audit_logs": {
        "user_id",
        "organization_id",
        "ip_address",
        "user_agent",
        "created_at",
    },
    "partner_submissions": {
        "pending_payload",
        "pending_hash",
        "pending_at",
        "first_published_at",
    },
}
NEVER_UPDATE = {
    "offers": NEVER_INSERT["offers"]
    | {"slug", "merchant_id", "source_url", "image_tint", "image_alt"},
    "affiliate_links": {"provider", "active", "tracking_id", "offer_id"},
    "offer_imports": {"provider", "external_id", "offer_id"},
    "partner_submissions": {"merchant_id", "ref", "offer_id", "first_published_at"},
}
AUDIT_INSERT_COLUMNS = {"id", "action", "resource", "resource_id", "metadata"}


def test_no_alter_role_mentions_an_attribute_a_managed_postgres_reserves_to_superusers() -> (
    None
):
    statements = [line for line in SQL.splitlines() if "ALTER ROLE" in line.upper()]

    assert statements  # the check is looking at something
    for statement in statements:
        for word in ("SUPERUSER", "REPLICATION", "BYPASSRLS", "CREATEROLE", "CREATEDB"):
            assert word not in statement.upper(), statement


def test_the_attributes_are_stated_on_create_role_with_a_connection_limit() -> None:
    create = next(
        line
        for line in SQL.splitlines()
        if "CREATE ROLE %I" in line.upper() or "'CREATE ROLE" in line
    )

    for word in (
        "NOSUPERUSER",
        "NOCREATEDB",
        "NOCREATEROLE",
        "NOREPLICATION",
        "NOBYPASSRLS",
    ):
        assert word in create
    assert "CONNECTION LIMIT" in SQL


def test_no_set_config_result_is_printed_or_kept_for_the_session() -> None:
    assert ", false)" not in SQL.lower()
    statements = re.findall(
        r"SELECT set_config\(.*?(?=\n\n|\nDO |\n-- parameters \(end\))", SQL, re.DOTALL
    )
    assert statements
    for statement in statements:
        assert "\\gset" in statement  # the result is consumed, not printed
        assert ", true)" in statement  # transaction-local


def test_the_whole_script_is_one_transaction() -> None:
    assert SQL.count("\nBEGIN;") == 1
    assert "COMMIT;" in SQL


def test_the_password_is_read_from_the_environment_and_never_from_a_command_line_variable() -> (
    None
):
    assert "\\getenv partner_password PARTNER_ROLE_PASSWORD" in SQL
    for path in (
        SCRIPT,
        REPO_ROOT / "docs" / "developer" / "backend-partner-api.md",
        REPO_ROOT / "docs" / "adr" / "0013-partner-api.md",
    ):
        assert "-v partner_password" not in path.read_text(encoding="utf-8"), path


def test_the_password_statements_cannot_leak_into_an_error() -> None:
    assert SQL.count("EXCEPTION WHEN OTHERS THEN") == 2
    assert "could not create the role (SQLSTATE %)" in SQL
    assert "could not set the role password (SQLSTATE %)" in SQL


def test_the_rebuild_and_the_verification_are_in_the_script() -> None:
    for fragment in (
        "REVOKE ALL ON ALL TABLES IN SCHEMA public",
        "REVOKE ALL ON ALL SEQUENCES IN SCHEMA public",
        "REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public",
        "REVOKE ALL ON SCHEMA public",
        "REVOKE ALL ON DATABASE",
        "has_table_privilege",
        "has_column_privilege",
        "has_schema_privilege(role_name, 'public', 'CREATE')",
        "pg_auth_members",
    ):
        assert fragment in SQL


def test_the_three_role_timeouts_are_set() -> None:
    for setting in (
        "statement_timeout",
        "lock_timeout",
        "idle_in_transaction_session_timeout",
    ):
        assert f"SET {setting}" in SQL


def _columns(pattern: re.Pattern[str]) -> dict[str, set[str]]:
    return {
        table: {c.strip() for c in cols.split(",")}
        for cols, table in pattern.findall(SQL)
    }


def test_the_audit_trail_is_append_only_and_the_role_cannot_name_a_user() -> None:
    granted = [
        line
        for line in SQL.splitlines()
        if "GRANT" in line.upper() and "--" not in line.split("GRANT")[0]
    ]

    assert _columns(GRANT_INSERT)["audit_logs"] == AUDIT_INSERT_COLUMNS
    assert "audit_logs" not in _columns(GRANT_SELECT)
    assert "audit_logs" not in _columns(GRANT_UPDATE)
    assert not any("DELETE" in line.upper() for line in granted)
    assert not any(
        "audit_logs" in line for line in granted if "INSERT" not in line.upper()
    )


def test_the_stripped_body_has_no_psql_only_line() -> None:
    body = script_body()

    assert not any(line.lstrip().startswith(chr(92)) for line in body.splitlines())
    assert "BEGIN;" not in body and "COMMIT;" not in body


def test_every_column_the_partner_package_names_is_granted_and_nothing_else_is() -> (
    None
):
    granted: dict[str, set[str]] = {}
    for pattern in (GRANT_SELECT, GRANT_INSERT, GRANT_UPDATE):
        for table, columns in _columns(pattern).items():
            granted[table] = granted.get(table, set()) | columns
    core = {t.name: {c.name for c in t.columns} for t in discovered_core_tables()}

    assert granted == core


def test_the_select_grants_are_a_subset_of_the_columns_the_package_names() -> None:
    core = {t.name: {c.name for c in t.columns} for t in discovered_core_tables()}

    for table, columns in _columns(GRANT_SELECT).items():
        assert columns <= core[table], table


def test_the_update_grants_are_exactly_these() -> None:
    assert _columns(GRANT_UPDATE) == {
        "partner_api_keys": {"last_used_at"},
        "offers": {
            "title",
            "summary",
            "description",
            "category_id",
            "offer_type",
            "postage_kind",
            "postage_amount_minor",
            "postage_currency",
            "requires_card",
            "subscription_terms",
            "eligibility",
            "availability",
            "steps",
            "start_at",
            "end_at",
            "updated_at",
        },
        "affiliate_links": {"destination_url", "updated_at"},
        "offer_imports": {
            "source_hash",
            "applied_source_hash",
            "applied_content_hash",
            "applied_at",
            "source_changed_at",
            "updated_at",
        },
        "partner_submissions": {
            "content_hash",
            "pending_payload",
            "pending_hash",
            "pending_at",
            "submitted_at",
            "key_id",
            "updated_at",
        },
    }


def test_no_grant_covers_an_editor_owned_or_identity_column_and_merchants_is_read_only() -> (
    None
):
    for table, columns in _columns(GRANT_UPDATE).items():
        assert not columns & NEVER_UPDATE.get(table, set()), table
    for table, columns in _columns(GRANT_INSERT).items():
        assert not columns & NEVER_INSERT.get(table, set()), table
    assert "merchants" not in _columns(GRANT_INSERT)
    assert "merchants" not in _columns(GRANT_UPDATE)


def test_the_verification_list_matches_the_grants() -> None:
    expected = set(re.findall(r"'(\w+\.\w+\.(?:SELECT|INSERT|UPDATE))'", SQL))
    from_grants = {
        f"{table}.{column}.{privilege}"
        for privilege, pattern in (
            ("SELECT", GRANT_SELECT),
            ("INSERT", GRANT_INSERT),
            ("UPDATE", GRANT_UPDATE),
        )
        for table, columns in _columns(pattern).items()
        for column in columns
    }

    assert expected == from_grants


def test_the_script_refuses_to_grant_anything_without_the_guard_triggers() -> None:
    for trigger in (
        "trg_offers_insert_defaults",
        "trg_affiliate_links_insert_defaults",
        "trg_audit_logs_insert_guard",
        "trg_offers_note_first_publish",
    ):
        assert trigger in SQL
    assert "run alembic upgrade head first" in SQL


# --- exact offline pins (the Postgres suites prove the same against the catalog) -------------------------------------

EXPECTED_SELECT = {
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
        "id merchant_id ref offer_id content_hash pending_payload pending_hash pending_at submitted_at key_id "
        "first_published_at created_at"
    ),
    "click_events": "offer_id occurred_at user_agent_class",
}
EXPECTED_INSERT = {
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


def test_the_select_grants_are_exactly_these() -> None:
    assert _columns(GRANT_SELECT) == {
        table: set(names.split()) for table, names in EXPECTED_SELECT.items()
    }


def test_the_insert_grants_are_exactly_these_so_an_added_column_fails_offline() -> None:
    assert _columns(GRANT_INSERT) == {
        table: set(names.split()) for table, names in EXPECTED_INSERT.items()
    }


def test_every_column_the_store_reads_or_writes_is_granted_in_the_way_it_is_used() -> (
    None
):
    from app.partner import submission_store as store

    selected = _columns(GRANT_SELECT)
    for table, names in (
        ("offers", store.OFFER_FIELDS),
        ("affiliate_links", store.LINK_FIELDS),
        ("offer_imports", store.IMPORT_FIELDS),
        ("partner_submissions", store.SUBMISSION_FIELDS),
    ):
        assert set(names) <= selected[table], table


TRIGGER_TUPLE = re.compile(r"\('(\w+)', '(trg_\w+)', '(freejoy_\w+)', (\d+)\)")
TGTYPE = {
    "BEFORE INSERT": 7,
    "BEFORE UPDATE": 19,
    "BEFORE INSERT OR UPDATE": 23,
    "AFTER UPDATE OF status": 17,
}


def _migration():  # type: ignore[no-untyped-def]
    import importlib.util

    path = (
        REPO_ROOT
        / "fastapi_backend"
        / "alembic_migrations"
        / "versions"
        / "0008_partner_submissions.py"
    )
    spec = importlib.util.spec_from_file_location("migration_0008", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_script_the_live_check_and_the_migration_name_the_same_eight_triggers() -> (
    None
):
    from app.core.database import PARTNER_GUARD_TRIGGERS, RESTRICTED_ROLE_QUERY

    in_script = {(t, n, f, int(k)) for t, n, f, k in TRIGGER_TUPLE.findall(SQL)}
    in_migration = {
        (table, name, function, TGTYPE[event])
        for name, table, event, function in _migration().TRIGGERS
    }

    assert len(PARTNER_GUARD_TRIGGERS) == 8
    assert in_script == set(PARTNER_GUARD_TRIGGERS) == in_migration
    for _table, name, function, _kind in PARTNER_GUARD_TRIGGERS:
        assert name in str(RESTRICTED_ROLE_QUERY) and function in str(
            RESTRICTED_ROLE_QUERY
        )


def test_the_script_checks_the_trigger_shape_and_that_the_role_cannot_switch_triggers_off() -> (
    None
):
    from app.core.database import RESTRICTED_ROLE_QUERY

    assert "t.tgtype::int" in SQL and "p.proname" in SQL and "c.relname" in SQL
    assert (
        "has_parameter_privilege(role_name, 'session_replication_role', 'SET')" in SQL
    )
    assert "session_replication_role" in str(RESTRICTED_ROLE_QUERY)


def test_every_migration_function_is_invoker_rights_with_a_pinned_search_path() -> None:
    functions = _migration().FUNCTIONS

    assert len(functions) == 8
    for name, statement in functions.items():
        assert "SECURITY DEFINER" not in statement.upper(), name
        assert "SET search_path = pg_catalog, public" in statement, name
