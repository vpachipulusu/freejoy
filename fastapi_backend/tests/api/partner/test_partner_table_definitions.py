"""The Core table definitions in ``app/partner`` match the ORM models (ADR-013).

``app/partner`` may not import the models, so it names the tables, columns and types it reads itself. The
definitions are DISCOVERED (every ``table(...)`` in every module of the package), so a table added later is
checked without editing this test. For each one the table, every column, its type and (for the columns the code
depends on) its nullability must agree with the ORM metadata.
"""

import importlib
import pkgutil
from typing import Any

import pytest
from sqlalchemy import Integer, Table, Text, column, table
from sqlalchemy.sql.expression import TableClause

import app.models.platform
import app.partner
from app.core.base import Base

# Nullability the partner code relies on, per table.column (ORM side; the Core definition carries none).
EXPECTED_NULLABLE = {
    ("partner_api_keys", "id"): False,
    ("partner_api_keys", "merchant_id"): False,
    ("partner_api_keys", "key_hash"): False,
    ("partner_api_keys", "label"): False,
    ("partner_api_keys", "revoked_at"): True,
    ("partner_api_keys", "last_used_at"): True,
    ("merchants", "id"): False,
    ("merchants", "name"): False,
    ("merchants", "active"): False,
}


def discovered_core_tables() -> list[TableClause]:
    """Every Core ``table()`` (not a mapped ``Table``) in any module of ``app.partner``."""
    found: dict[str, TableClause] = {}
    for info in pkgutil.walk_packages(app.partner.__path__, "app.partner."):
        module = importlib.import_module(info.name)
        for value in vars(module).values():
            if isinstance(value, TableClause) and not isinstance(value, Table):
                found[value.name] = value
    return list(found.values())


def definition_problems(
    core_tables: list[Any], orm_tables: dict[str, Table]
) -> list[str]:
    problems: list[str] = []
    for core in core_tables:
        orm = orm_tables.get(core.name)
        if orm is None:
            problems.append(f"table {core.name} is not in the ORM metadata")
            continue
        for col in core.columns:
            mapped = orm.columns.get(col.name)
            if mapped is None:
                problems.append(
                    f"column {core.name}.{col.name} is not in the ORM model"
                )
            elif type(col.type) is not type(mapped.type):
                problems.append(
                    f"column {core.name}.{col.name} is {type(col.type).__name__} here "
                    f"and {type(mapped.type).__name__} in the model"
                )
    return problems


def nullability_problems(orm_tables: dict[str, Table]) -> list[str]:
    return [
        f"{name}.{col} nullable is {orm_tables[name].columns[col].nullable}, expected {expected}"
        for (name, col), expected in EXPECTED_NULLABLE.items()
        if orm_tables[name].columns[col].nullable is not expected
    ]


def test_every_table_column_and_type_the_partner_package_names_matches_the_orm() -> (
    None
):
    assert (
        definition_problems(discovered_core_tables(), dict(Base.metadata.tables)) == []
    )


def test_the_nullability_the_partner_code_relies_on_still_holds() -> None:
    assert nullability_problems(dict(Base.metadata.tables)) == []


def test_every_discovered_column_has_a_pinned_nullability() -> None:
    discovered = {(t.name, c.name) for t in discovered_core_tables() for c in t.columns}

    assert discovered == set(EXPECTED_NULLABLE)


def test_the_partner_package_reads_exactly_these_tables() -> None:
    names = sorted(t.name for t in discovered_core_tables())

    assert names == ["merchants", "partner_api_keys"]


def test_the_check_notices_a_table_a_column_or_a_type_that_is_not_there() -> None:
    drifted = [
        table("partner_api_keys", column("id", Text), column("hash_of_key", Text)),
        table("merchants", column("active", Integer)),
        table("nonsense", column("id", Text)),
    ]

    assert definition_problems(drifted, dict(Base.metadata.tables)) == [
        "column partner_api_keys.hash_of_key is not in the ORM model",
        "column merchants.active is Integer here and Boolean in the model",
        "table nonsense is not in the ORM metadata",
    ]


def test_a_table_added_to_a_partner_module_is_found_without_editing_this_test(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.partner import keys

    monkeypatch.setattr(
        keys,
        "partner_submissions",
        table("partner_submissions", column("id", Text)),
        raising=False,
    )

    assert "partner_submissions" in {t.name for t in discovered_core_tables()}
    assert (
        "table partner_submissions is not in the ORM metadata"
        in definition_problems(discovered_core_tables(), dict(Base.metadata.tables))
    )
