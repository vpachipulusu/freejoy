"""The Core table definitions in ``app/partner/keys.py`` match the ORM models (ADR-013).

``app/partner`` may not import the models, so it names the tables and columns it reads itself. Every table
and column named there must exist, with the same name, in the ORM metadata, so the two cannot drift.
"""

from sqlalchemy import Table

import app.models.platform  # noqa: F401  (registers the tables)
from app.core.base import Base
from app.partner import keys


def definition_problems(
    core_tables: list[Table], orm_tables: dict[str, Table]
) -> list[str]:
    problems: list[str] = []
    for table in core_tables:
        orm = orm_tables.get(table.name)
        if orm is None:
            problems.append(f"table {table.name} is not in the ORM metadata")
            continue
        problems.extend(
            f"column {table.name}.{column.name} is not in the ORM model"
            for column in table.columns
            if column.name not in orm.columns
        )
    return problems


def core_tables() -> list[Table]:
    return [keys.partner_api_keys, keys.merchants]  # type: ignore[list-item]


def test_every_table_and_column_the_partner_package_names_is_in_the_orm_metadata() -> (
    None
):
    assert definition_problems(core_tables(), dict(Base.metadata.tables)) == []


def test_the_partner_package_reads_exactly_these_tables() -> None:
    assert [t.name for t in core_tables()] == ["partner_api_keys", "merchants"]


def test_the_check_notices_a_table_or_column_that_is_not_there() -> None:
    from sqlalchemy import column, table

    drifted = [
        table("partner_api_keys", column("id"), column("hash_of_key")),
        table("nonsense", column("id")),
    ]

    assert definition_problems(drifted, dict(Base.metadata.tables)) == [  # type: ignore[arg-type]
        "column partner_api_keys.hash_of_key is not in the ORM model",
        "table nonsense is not in the ORM metadata",
    ]
