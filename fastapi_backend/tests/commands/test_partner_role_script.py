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


def test_nothing_is_granted_on_the_audit_trail() -> None:
    granted = [
        line
        for line in SQL.splitlines()
        if "GRANT" in line.upper() and "--" not in line.split("GRANT")[0]
    ]

    assert not any("audit_logs" in line for line in granted)


def test_the_stripped_body_has_no_psql_only_line() -> None:
    body = script_body()

    assert not any(line.lstrip().startswith(chr(92)) for line in body.splitlines())
    assert "BEGIN;" not in body and "COMMIT;" not in body


def test_the_select_grants_are_exactly_the_columns_the_partner_package_names() -> None:
    granted = {
        table: {c.strip() for c in cols.split(",")}
        for cols, table in GRANT_SELECT.findall(SQL)
    }
    core = {t.name: {c.name for c in t.columns} for t in discovered_core_tables()}

    assert granted == core


def test_the_only_update_grant_is_last_used_at() -> None:
    updates = {
        table: {c.strip() for c in cols.split(",")}
        for cols, table in GRANT_UPDATE.findall(SQL)
    }

    assert updates == {"partner_api_keys": {"last_used_at"}}


def test_the_verification_list_matches_the_grants() -> None:
    expected = set(re.findall(r"'(\w+\.\w+\.(?:SELECT|UPDATE))'", SQL))
    from_grants = {
        f"{table}.{column.strip()}.SELECT"
        for cols, table in GRANT_SELECT.findall(SQL)
        for column in cols.split(",")
    } | {
        f"{table}.{column.strip()}.UPDATE"
        for cols, table in GRANT_UPDATE.findall(SQL)
        for column in cols.split(",")
    }

    assert expected == from_grants
