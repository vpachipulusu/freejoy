"""The shape of ``partner_api_keys`` (model only; the constraints run in the key repository e2e test)."""

from sqlalchemy import CheckConstraint, Table, UniqueConstraint

import app.models.platform  # noqa: F401  (registers the tables)
from app.core.base import Base
from app.core.platform_schema import PLATFORM_HEAD, PLATFORM_REVISIONS

COLUMNS = {
    "id",
    "merchant_id",
    "key_hash",
    "label",
    "created_by",
    "created_at",
    "revoked_at",
    "last_used_at",
}


def keys_table() -> Table:
    return Base.metadata.tables["partner_api_keys"]


def test_the_column_set_is_the_stories() -> None:
    assert set(keys_table().columns.keys()) == COLUMNS


def test_a_key_belongs_to_a_merchant_and_goes_with_it() -> None:
    (key,) = keys_table().columns["merchant_id"].foreign_keys

    assert key.target_fullname == "merchants.id"
    assert key.ondelete == "CASCADE"


def test_the_key_hash_is_unique() -> None:
    uniques = {
        tuple(column.name for column in c.columns)
        for c in keys_table().constraints
        if isinstance(c, UniqueConstraint)
    }

    assert ("key_hash",) in uniques


def test_only_the_columns_that_change_are_nullable() -> None:
    nullable = {c.name for c in keys_table().columns if c.nullable}

    assert nullable == {"revoked_at", "last_used_at"}


def test_the_check_constraints_are_named() -> None:
    names = {
        str(c.name)
        for c in keys_table().constraints
        if isinstance(c, CheckConstraint) and c.name
    }

    assert names == {
        "ck_partner_api_keys_id_valid",
        "ck_partner_api_keys_key_hash_valid",
        "ck_partner_api_keys_label_valid",
        "ck_partner_api_keys_revoked_after_created",
    }


def test_the_revision_is_registered_as_the_head() -> None:
    assert PLATFORM_HEAD == "0007"
    assert PLATFORM_REVISIONS[-2:] == ("0006", "0007")
