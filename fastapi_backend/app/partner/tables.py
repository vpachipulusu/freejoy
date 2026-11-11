"""The tables the partner submission code reads and writes, as SQLAlchemy Core definitions (ADR-013, FREEJ-58).

``app/partner`` may import only ``app.core`` and itself, so it cannot use the ORM models. It names the tables and
columns it touches here (``merchants`` is in ``keys.py``) and reaches them through the restricted
``freejoy_partner`` role, whose column grants in ``commands/sql/partner_role.sql`` cover the columns used here
in the way they are used (the ``SELECT`` grants may be wider, for the fingerprint and the click report). ``tests/api/partner/test_partner_table_definitions.py`` checks every table, column and
type against the ORM metadata, and ``tests/commands/test_partner_role_script.py`` ties the script's grants to these
definitions, so the two cannot drift.

Columns that this code only writes (``updated_at``, every audit column) are listed because a Core statement must
name them; the role has no ``SELECT`` on them.
"""

from sqlalchemy import Boolean, DateTime, Enum, Integer, Text, column, table
from sqlalchemy.dialects.postgresql import JSONB

from app.core.offer_vocabulary import (
    OFFER_FRESHNESS,
    OFFER_STATUSES,
    OFFER_TYPES,
    POSTAGE_KINDS,
)


def _enum(name: str, *members: str) -> Enum:
    """An enum stored as short text with no database type, as the ORM models store theirs (``enum_column``)."""
    return Enum(
        *members, name=name, native_enum=False, create_constraint=False, length=16
    )


offers = table(
    "offers",
    column("id", Text),
    column("title", Text),
    column("slug", Text),
    column("summary", Text),
    column("description", JSONB),
    column("category_id", Text),
    column("merchant_id", Text),
    column("offer_type", _enum("offertype", *OFFER_TYPES)),
    column("status", _enum("offerstatus", *OFFER_STATUSES)),
    column("featured", Boolean),
    column("image_tint", Text),
    column("image_label", Text),
    column("image_alt", Text),
    column("source_url", Text),
    column("postage_kind", _enum("postagekind", *POSTAGE_KINDS)),
    column("postage_amount_minor", Integer),
    column("postage_currency", Text),
    column("requires_card", Boolean),
    column("subscription_terms", Text),
    column("eligibility", Text),
    column("availability", Text),
    column("steps", JSONB),
    column("start_at", DateTime(timezone=True)),
    column("end_at", DateTime(timezone=True)),
    column("last_verified_at", DateTime(timezone=True)),
    column("freshness", _enum("offerfreshness", *OFFER_FRESHNESS)),
    column("publisher_notes", Text),
    column("updated_at", DateTime(timezone=True)),
)
affiliate_links = table(
    "affiliate_links",
    column("id", Text),
    column("offer_id", Text),
    column("provider", Text),
    column("destination_url", Text),
    column("tracking_id", Text),
    column("active", Boolean),
    column("updated_at", DateTime(timezone=True)),
)
offer_imports = table(
    "offer_imports",
    column("id", Text),
    column("provider", Text),
    column("external_id", Text),
    column("offer_id", Text),
    column("source_hash", Text),
    column("applied_source_hash", Text),
    column("applied_content_hash", Text),
    column("applied_at", DateTime(timezone=True)),
    column("source_changed_at", DateTime(timezone=True)),
    column("updated_at", DateTime(timezone=True)),
)
categories = table(
    "categories",
    column("id", Text),
    column("slug", Text),
    column("enabled", Boolean),
)
partner_submissions = table(
    "partner_submissions",
    column("id", Text),
    column("merchant_id", Text),
    column("ref", Text),
    column("offer_id", Text),
    column("content_hash", Text),
    column("pending_payload", JSONB(none_as_null=True)),
    column("pending_hash", Text),
    column("pending_at", DateTime(timezone=True)),
    column("submitted_at", DateTime(timezone=True)),
    column("key_id", Text),
    column("first_published_at", DateTime(timezone=True)),
    column("created_at", DateTime(timezone=True)),
    column("updated_at", DateTime(timezone=True)),
)
# Append only: the role may insert these five columns and nothing else (a database trigger fixes the rest).
audit_logs = table(
    "audit_logs",
    column("id", Text),
    column("action", Text),
    column("resource", Text),
    column("resource_id", Text),
    column("metadata", JSONB),
)
