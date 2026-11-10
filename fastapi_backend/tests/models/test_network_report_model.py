"""The shape of ``network_report_rows`` (model only; the constraints run in the e2e test)."""

from sqlalchemy import CheckConstraint, ForeignKey, Table, UniqueConstraint

import app.models.platform  # noqa: F401  (registers the tables)
from app.core.base import Base
from app.core.platform_schema import PLATFORM_REVISIONS

COLUMNS = {
    "id",
    "provider",
    "external_id",
    "offer_id",
    "affiliate_link_id",
    "merchant_external_id",
    "merchant_id",
    "transaction_at",
    "commission_status",
    "commission_status_raw",
    "commission_amount_minor",
    "sale_amount_minor",
    "currency",
    "click_ref",
    "report_hash",
    "first_seen_at",
    "last_seen_at",
    "created_at",
    "updated_at",
}


def rows_table() -> Table:
    return Base.metadata.tables["network_report_rows"]


def foreign_key(column: str) -> ForeignKey:
    (key,) = rows_table().columns[column].foreign_keys
    return key


def test_the_column_set_is_pinned() -> None:
    assert set(rows_table().columns.keys()) == COLUMNS


def test_a_row_is_unique_per_provider_and_external_id() -> None:
    uniques = {
        tuple(column.name for column in c.columns)
        for c in rows_table().constraints
        if isinstance(c, UniqueConstraint)
    }

    assert ("provider", "external_id") in uniques


def test_the_check_constraints_are_named() -> None:
    names = {
        str(c.name)
        for c in rows_table().constraints
        if isinstance(c, CheckConstraint) and c.name
    }

    assert names == {
        f"ck_network_report_rows_{name}"
        for name in (
            "provider_valid",
            "external_id_valid",
            "merchant_external_id_valid",
            "commission_status_valid",
            "commission_status_raw_valid",
            "amounts_valid",
            "currency_valid",
            "click_ref_valid",
            "report_hash_valid",
            "seen_order_valid",
        )
    }


def test_the_links_to_the_catalogue_are_nullable_and_set_null() -> None:
    for column, target in (
        ("offer_id", "offers.id"),
        ("affiliate_link_id", "affiliate_links.id"),
        ("merchant_id", "merchants.id"),
    ):
        assert rows_table().columns[column].nullable, column
        assert foreign_key(column).ondelete == "SET NULL", column
        assert foreign_key(column).target_fullname == target


def test_the_indexes_serve_the_reports_by_time_and_by_offer() -> None:
    indexes = {
        tuple(column.name for column in index.columns) for index in rows_table().indexes
    }

    assert ("provider", "transaction_at") in indexes
    assert ("offer_id",) in indexes
    assert ("affiliate_link_id",) in indexes
    assert ("merchant_id",) in indexes


def test_migration_0005_is_registered() -> None:
    assert "0005" in PLATFORM_REVISIONS
