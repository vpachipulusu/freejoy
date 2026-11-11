"""Partner submissions

Adds ``partner_submissions`` (model in ``app/models/platform/partner_submission.py``): one row per offer a retailer
submits through the partner API (ADR-013, FREEJ-58), unique on ``(merchant_id, ref)``, with the sanitised content
hash, an optional pending (not yet applied) update, the key that submitted it and when the offer was first
published. Also eight database triggers, because the restricted partner role (created by
``commands/sql/partner_role.sql``) has column grants only, and a column grant has no row condition: the database
supplies what the role may not write and refuses what it may write but must not. Every guard tells the role from every
other writer by a privilege it lacks (``has_column_privilege(current_user, TG_RELID, <column>, ...)``), so the owner,
the admin API and the importers, which hold every privilege, are untouched. All functions run with the invoker's
rights and a pinned ``search_path``.

Defaults:

* ``offers`` BEFORE INSERT: ``commercial_basis`` is NOT NULL with no default, so every ordinary writer states it. A
  writer with no ``INSERT`` on that column gets ``SPONSORED`` as a placeholder (over-disclosure is the safe error; nobody
  has chosen it, and the editor must state the real basis in the publish request). Such a writer may only insert a
  sample or a trial. A writer that may write the column and omits it still fails on NOT NULL, as before.
* ``affiliate_links`` BEFORE INSERT: the same for ``provider``, which becomes ``direct``.

Guards on what the role may write (each needs the offer to belong to a partner submission):

* ``offers`` BEFORE UPDATE: a writer without ``UPDATE`` on ``status`` may change only an offer that has a submission of the
  same merchant, and, once the offer is not a DRAFT, only ``start_at``, ``end_at`` and ``updated_at``, only to end it
  (``end_at`` set and not after now, ``start_at`` only cleared).
* ``affiliate_links`` BEFORE INSERT OR UPDATE: a writer without ``UPDATE`` on ``provider`` may write only ``direct``
  links of a DRAFT that has a submission.
* ``offer_imports`` BEFORE INSERT OR UPDATE: a writer without ``UPDATE`` on ``provider`` may write only provider
  ``partner`` rows of an offer that has a submission.
* ``partner_submissions`` BEFORE INSERT: a writer without ``UPDATE`` on ``first_published_at`` may attach a submission
  only to a DRAFT of its own merchant that has no link and no provenance row yet (so a bare hand-made draft is not
  adoptable). The service therefore inserts offer, submission, link, import, in that order.
* ``audit_logs`` BEFORE INSERT: a writer that may not name a user may append only the three partner actions, about
  ``offers``, with no user and no organisation and a bounded id and metadata, so it cannot forge an ``admin.`` row or
  bury the trail.

Fact:

* ``offers`` AFTER UPDATE OF status: the first time an offer becomes PUBLISHED its submission's ``first_published_at``
  is set (the partner API needs it to tell ``declined`` from ``ended``).

What stays: nothing at the database separates one merchant from another inside the role (the session carries no merchant
identity): it can end partner-submitted live offers and edit partner drafts of any merchant, and read what its SELECT
grants name (ADR-013).

The upgrade takes brief locks on ``offers``, ``merchants``, ``affiliate_links``, ``offer_imports`` and ``audit_logs``
(foreign keys, triggers), so it sets ``lock_timeout``: if something holds a conflicting lock it fails and rolls back,
and the owner retries (pause the Awin import first). The downgrade drops the triggers, their functions and the table,
which loses every submission (the offers stay) and leaves the role's ``INSERT`` on ``audit_logs`` unguarded: never run it
in production (see ``docs/developer/platform-models.md``).

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
REF_PATTERN = "^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"
KEY_ID_PATTERN = "^[a-z0-9]{24}$"
# The largest sanitised proposal that may wait for an editor. The API's caps allow 6,900 characters of text and a 2,048
# character link; in 4-byte characters that is about 30 KB as ``jsonb::text``, so the limit leaves room above it.
MAX_PENDING_BYTES = 49152
LOCK_TIMEOUT = "5s"

# Every function: invoker rights (no SECURITY DEFINER), a pinned search_path, schema-qualified table names.
FUNCTIONS: dict[str, str] = {
    "freejoy_offer_insert_defaults": """
CREATE FUNCTION freejoy_offer_insert_defaults() RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, public AS $fn$
BEGIN
  IF NEW.commercial_basis IS NULL
     AND NOT has_column_privilege(current_user, TG_RELID, 'commercial_basis', 'INSERT') THEN
    NEW.commercial_basis := 'SPONSORED';
    IF NEW.offer_type NOT IN ('FREE_SAMPLE', 'FREE_TRIAL') THEN
      RAISE EXCEPTION 'this writer may only insert a sample or a trial' USING ERRCODE = '42501';
    END IF;
  END IF;
  RETURN NEW;
END
$fn$
""",
    "freejoy_link_insert_defaults": """
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
""",
    "freejoy_offer_update_guard": """
CREATE FUNCTION freejoy_offer_update_guard() RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, public AS $fn$
BEGIN
  IF has_column_privilege(current_user, TG_RELID, 'status', 'UPDATE') THEN
    RETURN NEW;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM public.partner_submissions s
                  WHERE s.offer_id = OLD.id AND s.merchant_id = OLD.merchant_id) THEN
    RAISE EXCEPTION 'this writer may only change an offer it submitted' USING ERRCODE = '42501';
  END IF;
  IF NEW.offer_type NOT IN ('FREE_SAMPLE', 'FREE_TRIAL') THEN
    RAISE EXCEPTION 'this writer may only write a sample or a trial' USING ERRCODE = '42501';
  END IF;
  IF OLD.status <> 'DRAFT' AND (
       (to_jsonb(NEW) - '{start_at,end_at,updated_at}'::text[])
         IS DISTINCT FROM (to_jsonb(OLD) - '{start_at,end_at,updated_at}'::text[])
       OR NEW.end_at IS NULL OR NEW.end_at > now()
       OR (NEW.start_at IS DISTINCT FROM OLD.start_at AND NEW.start_at IS NOT NULL)) THEN
    RAISE EXCEPTION 'this writer may only end an offer that is not a draft' USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END
$fn$
""",
    "freejoy_link_write_guard": """
CREATE FUNCTION freejoy_link_write_guard() RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, public AS $fn$
BEGIN
  IF has_column_privilege(current_user, TG_RELID, 'provider', 'UPDATE') THEN
    RETURN NEW;
  END IF;
  IF NEW.provider IS DISTINCT FROM 'direct' OR (TG_OP = 'UPDATE' AND OLD.provider <> 'direct') THEN
    RAISE EXCEPTION 'this writer may only write direct links' USING ERRCODE = '42501';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM public.offers o
                   JOIN public.partner_submissions s ON s.offer_id = o.id
                  WHERE o.id = NEW.offer_id AND o.status = 'DRAFT') THEN
    RAISE EXCEPTION 'this writer may only write links of a submitted draft' USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END
$fn$
""",
    "freejoy_import_write_guard": """
CREATE FUNCTION freejoy_import_write_guard() RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, public AS $fn$
BEGIN
  IF has_column_privilege(current_user, TG_RELID, 'provider', 'UPDATE') THEN
    RETURN NEW;
  END IF;
  IF NEW.provider IS DISTINCT FROM 'partner' OR (TG_OP = 'UPDATE' AND OLD.provider <> 'partner') THEN
    RAISE EXCEPTION 'this writer may only write partner provenance' USING ERRCODE = '42501';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM public.partner_submissions s WHERE s.offer_id = NEW.offer_id) THEN
    RAISE EXCEPTION 'this writer may only write provenance of a submitted offer' USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END
$fn$
""",
    "freejoy_submission_insert_guard": """
CREATE FUNCTION freejoy_submission_insert_guard() RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, public AS $fn$
BEGIN
  IF has_column_privilege(current_user, TG_RELID, 'first_published_at', 'UPDATE') THEN
    RETURN NEW;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM public.offers o
                  WHERE o.id = NEW.offer_id AND o.merchant_id = NEW.merchant_id AND o.status = 'DRAFT')
     OR EXISTS (SELECT 1 FROM public.affiliate_links l WHERE l.offer_id = NEW.offer_id)
     OR EXISTS (SELECT 1 FROM public.offer_imports i WHERE i.offer_id = NEW.offer_id) THEN
    RAISE EXCEPTION 'this writer may only submit a new draft of its own merchant' USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END
$fn$
""",
    "freejoy_audit_insert_guard": """
CREATE FUNCTION freejoy_audit_insert_guard() RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, public AS $fn$
BEGIN
  IF NOT has_column_privilege(current_user, TG_RELID, 'user_id', 'INSERT') THEN
    IF NEW.user_id IS NOT NULL OR NEW.organization_id IS NOT NULL
       OR NEW.action NOT IN ('partner.offers.submit', 'partner.offers.update', 'partner.offers.end')
       OR NEW.resource IS DISTINCT FROM 'offers'
       OR char_length(coalesce(NEW.resource_id, '')) > 64
       OR octet_length(coalesce(NEW.metadata, '{}'::jsonb)::text) > 2048 THEN
      RAISE EXCEPTION 'this writer may only append partner audit rows' USING ERRCODE = '42501';
    END IF;
  END IF;
  RETURN NEW;
END
$fn$
""",
    "freejoy_note_first_publish": """
CREATE FUNCTION freejoy_note_first_publish() RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, public AS $fn$
BEGIN
  UPDATE public.partner_submissions SET first_published_at = now()
   WHERE offer_id = NEW.id AND first_published_at IS NULL;
  RETURN NULL;
END
$fn$
""",
}

# (trigger, table, event, function). BEFORE triggers on one table fire in name order.
TRIGGERS: tuple[tuple[str, str, str, str], ...] = (
    (
        "trg_offers_insert_defaults",
        "offers",
        "BEFORE INSERT",
        "freejoy_offer_insert_defaults",
    ),
    (
        "trg_offers_update_guard",
        "offers",
        "BEFORE UPDATE",
        "freejoy_offer_update_guard",
    ),
    (
        "trg_offers_note_first_publish",
        "offers",
        "AFTER UPDATE OF status",
        "freejoy_note_first_publish",
    ),
    (
        "trg_affiliate_links_insert_defaults",
        "affiliate_links",
        "BEFORE INSERT",
        "freejoy_link_insert_defaults",
    ),
    (
        "trg_affiliate_links_write_guard",
        "affiliate_links",
        "BEFORE INSERT OR UPDATE",
        "freejoy_link_write_guard",
    ),
    (
        "trg_offer_imports_write_guard",
        "offer_imports",
        "BEFORE INSERT OR UPDATE",
        "freejoy_import_write_guard",
    ),
    (
        "trg_partner_submissions_insert_guard",
        "partner_submissions",
        "BEFORE INSERT",
        "freejoy_submission_insert_guard",
    ),
    (
        "trg_audit_logs_insert_guard",
        "audit_logs",
        "BEFORE INSERT",
        "freejoy_audit_insert_guard",
    ),
)
FIRST_PUBLISH_WHEN = (
    " WHEN (NEW.status = 'PUBLISHED' AND OLD.status IS DISTINCT FROM 'PUBLISHED')"
)


def _create_trigger(name: str, table: str, event: str, function: str) -> str:
    when = FIRST_PUBLISH_WHEN if function == "freejoy_note_first_publish" else ""
    return f"CREATE TRIGGER {name} {event} ON {table} FOR EACH ROW{when} EXECUTE FUNCTION {function}()"


def upgrade() -> None:
    op.execute(sa.text(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'"))
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
            f"ref ~ '{REF_PATTERN}'", name=op.f("ck_partner_submissions_ref_valid")
        ),
        sa.CheckConstraint(
            f"key_id ~ '{KEY_ID_PATTERN}'",
            name=op.f("ck_partner_submissions_key_id_valid"),
        ),
        sa.CheckConstraint(
            f"content_hash ~ '{HASH_PATTERN}'",
            name=op.f("ck_partner_submissions_content_hash_valid"),
        ),
        sa.CheckConstraint(
            "(pending_payload IS NULL AND pending_hash IS NULL AND pending_at IS NULL)"
            " OR (pending_payload IS NOT NULL AND pending_hash IS NOT NULL AND pending_at IS NOT NULL"
            " AND jsonb_typeof(pending_payload) = 'object'"
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
    for statement in FUNCTIONS.values():
        op.execute(sa.text(statement))
    for name, table, event, function in TRIGGERS:
        op.execute(sa.text(_create_trigger(name, table, event, function)))


def downgrade() -> None:
    op.execute(sa.text(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'"))
    for name, table, _event, _function in TRIGGERS:
        op.execute(sa.text(f"DROP TRIGGER {name} ON {table}"))
    for function in FUNCTIONS:
        op.execute(sa.text(f"DROP FUNCTION {function}()"))
    op.drop_index(
        "ix_partner_submissions_merchant_id_created_at",
        table_name="partner_submissions",
    )
    op.drop_table("partner_submissions")
