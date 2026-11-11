"""The shape of ``partner_submissions`` (model only; the constraints and triggers run in the ``*_e2e.py`` suites)."""

from sqlalchemy import Table

from app.core.base import Base
from app.core.platform_schema import PLATFORM_HEAD, PLATFORM_REVISIONS
from app.models.platform import PartnerSubmission


def table() -> Table:
    return Base.metadata.tables["partner_submissions"]


def test_the_model_is_the_table() -> None:
    assert PartnerSubmission.__table__ is table()


def test_the_columns_are_these() -> None:
    assert {c.name for c in table().columns} == {
        "id",
        "merchant_id",
        "ref",
        "offer_id",
        "content_hash",
        "pending_payload",
        "pending_hash",
        "pending_at",
        "submitted_at",
        "key_id",
        "first_published_at",
        "created_at",
        "updated_at",
    }


def test_tenancy_is_a_column_and_a_ref_is_unique_per_merchant_and_an_offer_has_one_submission() -> (
    None
):
    unique = {
        tuple(sorted(c.name for c in constraint.columns))
        for constraint in table().constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }

    assert ("merchant_id", "ref") in unique
    assert ("offer_id",) in unique
    assert not table().c.merchant_id.nullable


def test_both_foreign_keys_cascade() -> None:
    actions = {fk.parent.name: fk.ondelete for fk in table().foreign_keys}

    assert actions == {"merchant_id": "CASCADE", "offer_id": "CASCADE"}


def test_the_revision_is_registered_and_follows_0007() -> None:
    assert "0008" in PLATFORM_REVISIONS
    assert PLATFORM_REVISIONS.index("0008") == PLATFORM_REVISIONS.index("0007") + 1
    assert PLATFORM_HEAD == PLATFORM_REVISIONS[-1]


def test_the_database_checks_cover_the_ref_the_key_id_and_the_pending_shape() -> None:
    checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in table().constraints
        if constraint.__class__.__name__ == "CheckConstraint"
    }

    assert "ck_partner_submissions_ref_valid" in checks
    assert (
        "[A-Za-z0-9][A-Za-z0-9._-]{0,63}" in checks["ck_partner_submissions_ref_valid"]
    )
    assert "[a-z0-9]{24}" in checks["ck_partner_submissions_key_id_valid"]
    pending = checks["ck_partner_submissions_pending_complete"]
    assert "jsonb_typeof(pending_payload) = 'object'" in pending
    assert "49152" in pending


def test_the_stored_proposal_limit_is_above_the_largest_body_the_api_accepts() -> None:
    from app.models.platform.partner_submission import MAX_PENDING_BYTES

    assert MAX_PENDING_BYTES == 49_152
