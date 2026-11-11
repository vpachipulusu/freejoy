"""Partner submissions

Adds ``partner_submissions`` (model in ``app/models/platform/partner_submission.py``): one row per offer a retailer
submits through the partner API (ADR-013, FREEJ-58), unique on ``(merchant_id, ref)``, with the sanitised content
hash, an optional pending (not yet applied) update, the key that submitted it and when the offer was first
published. Also four database triggers, because the restricted partner role may not write some columns and the
database has to supply or police them (the role itself is created by ``commands/sql/partner_role.sql``):

* ``offers`` BEFORE INSERT: ``commercial_basis`` is NOT NULL with no default, so every ordinary writer states it. A
  writer that has no ``INSERT`` privilege on that column (the partner role) cannot, and gets ``EDITORIAL`` as a
  placeholder; the editor sets the real basis at review, and a direct-only offer needs the editor's confirmation
  before it can be published whatever the stored value is. A writer that may write the column and omits it still
  fails on NOT NULL, as before.
* ``affiliate_links`` BEFORE INSERT: the same for ``provider``, which a writer without ``INSERT`` on it gets as
  ``direct`` (partner links are always direct).
* ``audit_logs`` BEFORE INSERT: a writer that may not name a user (the partner role: it has ``INSERT`` on the
  columns an audit row needs, but not ``user_id``) can only write a row whose action is ``partner.<area>.<verb>``
  with no user and no organisation, so it cannot forge an ``admin.`` row. Every other writer is unaffected.
* ``offers`` AFTER UPDATE OF status: the first time an offer becomes PUBLISHED its submission's
  ``first_published_at`` is set (a fact the partner API needs to tell ``declined`` from ``ended``).

The downgrade drops the triggers, their functions and the table, which loses every submission (the offers
stay): run it only against a throwaway database.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

HASH_PATTERN = "^[0-9a-f]{64}$"
MAX_PENDING_BYTES = 24576

OFFER_DEFAULTS_FUNCTION = """
CREATE FUNCTION freejoy_offer_insert_defaults() RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, public AS $fn$
BEGIN
  IF NEW.commercial_basis IS NULL
     AND NOT has_column_privilege(current_user, TG_RELID, 'commercial_basis', 'INSERT') THEN
    NEW.commercial_basis := 'EDITORIAL';
  END IF;
  RETURN NEW;
END
$fn$
"""
LINK_DEFAULTS_FUNCTION = """
CREATE FUNCTION freejoy_link_insert_defaults() RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, public AS $fn$
BEGIN
  IF NEW.provider IS NULL
     AND NOT has_column_privilege(current_user, TG_RELID, 'provider', 'INSERT') THEN
    NEW.provider := 'direct';
  END IF;
  RETURN NEW;
END
$fn$
"""
AUDIT_GUARD_FUNCTION = """
CREATE FUNCTION freejoy_audit_insert_guard() RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, public AS $fn$
BEGIN
  IF NOT has_column_privilege(current_user, TG_RELID, 'user_id', 'INSERT') THEN
    IF NEW.user_id IS NOT NULL OR NEW.organization_id IS NOT NULL
       OR NEW.action !~ '^partner\\.[a-z_]+\\.[a-z_]+$' THEN
      RAISE EXCEPTION 'this writer may only append partner audit rows' USING ERRCODE = '42501';
    END IF;
  END IF;
  RETURN NEW;
END
$fn$
"""
FIRST_PUBLISH_FUNCTION = """
CREATE FUNCTION freejoy_note_first_publish() RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, public AS $fn$
BEGIN
  UPDATE public.partner_submissions SET first_published_at = now()
   WHERE offer_id = NEW.id AND first_published_at IS NULL;
  RETURN NULL;
END
$fn$
"""

TRIGGERS = (
    (
        "trg_offers_insert_defaults",
        "BEFORE INSERT ON offers FOR EACH ROW",
        "freejoy_offer_insert_defaults",
    ),
    (
        "trg_affiliate_links_insert_defaults",
        "BEFORE INSERT ON affiliate_links FOR EACH ROW",
        "freejoy_link_insert_defaults",
    ),
    (
        "trg_audit_logs_insert_guard",
        "BEFORE INSERT ON audit_logs FOR EACH ROW",
        "freejoy_audit_insert_guard",
    ),
    (
        "trg_offers_note_first_publish",
        (
            "AFTER UPDATE OF status ON offers FOR EACH ROW"
            " WHEN (NEW.status = 'PUBLISHED' AND OLD.status IS DISTINCT FROM 'PUBLISHED')"
        ),
        "freejoy_note_first_publish",
    ),
)
FUNCTIONS = (
    OFFER_DEFAULTS_FUNCTION,
    LINK_DEFAULTS_FUNCTION,
    AUDIT_GUARD_FUNCTION,
    FIRST_PUBLISH_FUNCTION,
)
TRIGGER_TABLES = {
    "trg_offers_insert_defaults": "offers",
    "trg_affiliate_links_insert_defaults": "affiliate_links",
    "trg_audit_logs_insert_guard": "audit_logs",
    "trg_offers_note_first_publish": "offers",
}


def upgrade() -> None:
    op.create_table(
        "partner_submissions",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("merchant_id", sa.Text(), nullable=False),
        sa.Column("ref", sa.Text(), nullable=False),
        sa.Column("offer_id", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column(
            "pending_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("pending_hash", sa.Text(), nullable=True),
        sa.Column("pending_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "submitted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("key_id", sa.Text(), nullable=False),
        sa.Column("first_published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "ref ~ '^[A-Za-z0-9._-]{1,64}$'",
            name=op.f("ck_partner_submissions_ref_valid"),
        ),
        sa.CheckConstraint(
            f"content_hash ~ '{HASH_PATTERN}'",
            name=op.f("ck_partner_submissions_content_hash_valid"),
        ),
        sa.CheckConstraint(
            "(pending_payload IS NULL AND pending_hash IS NULL AND pending_at IS NULL)"
            " OR (pending_payload IS NOT NULL AND pending_hash IS NOT NULL AND pending_at IS NOT NULL"
            f" AND pending_hash ~ '{HASH_PATTERN}'"
            f" AND octet_length(pending_payload::text) <= {MAX_PENDING_BYTES})",
            name=op.f("ck_partner_submissions_pending_complete"),
        ),
        sa.ForeignKeyConstraint(
            ["merchant_id"],
            ["merchants.id"],
            name=op.f("fk_partner_submissions_merchant_id_merchants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["offer_id"],
            ["offers.id"],
            name=op.f("fk_partner_submissions_offer_id_offers"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_partner_submissions")),
        sa.UniqueConstraint("offer_id", name=op.f("uq_partner_submissions_offer_id")),
        sa.UniqueConstraint(
            "merchant_id", "ref", name="uq_partner_submissions_merchant_id_ref"
        ),
    )
    op.create_index(
        "ix_partner_submissions_merchant_id_created_at",
        "partner_submissions",
        ["merchant_id", "created_at"],
        unique=False,
    )
    for function in FUNCTIONS:
        op.execute(sa.text(function))
    for name, when, function in TRIGGERS:
        op.execute(
            sa.text(f"CREATE TRIGGER {name} {when} EXECUTE FUNCTION {function}()")
        )


def downgrade() -> None:
    for name, _when, _function in TRIGGERS:
        op.execute(sa.text(f"DROP TRIGGER {name} ON {TRIGGER_TABLES[name]}"))
    for function in (
        "freejoy_offer_insert_defaults",
        "freejoy_link_insert_defaults",
        "freejoy_audit_insert_guard",
        "freejoy_note_first_publish",
    ):
        op.execute(sa.text(f"DROP FUNCTION {function}()"))
    op.drop_index(
        "ix_partner_submissions_merchant_id_created_at",
        table_name="partner_submissions",
    )
    op.drop_table("partner_submissions")
