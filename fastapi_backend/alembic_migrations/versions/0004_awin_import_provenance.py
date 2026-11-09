"""Awin import provenance

Adds ``offer_imports`` and ``merchant_imports`` (models in ``app/models/platform/catalogue_import.py``):
which provider promotion an offer was imported from and which provider advertiser a merchant stands
for, keyed by ``(provider, external_id)``, plus the hashes the importer uses to tell a change at the
source from an edit by a person. A separate pair of tables, not columns on ``offers``/``merchants``,
so the catalogue tables are untouched. Foreign keys cascade: deleting an offer or merchant deletes
only its provenance row.

The downgrade drops both tables, which loses the link between offers and the promotions they were
imported from (the offers and merchants themselves stay). A later import would then create the
promotions again as new drafts: run it only against a throwaway database or before any import.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PROVIDER_PATTERN = "provider ~ '^[a-z][a-z0-9_]{0,39}$'"
EXTERNAL_ID_LENGTH = "char_length(external_id) BETWEEN 1 AND 64"


def _timestamps() -> list[sa.Column[sa.DateTime]]:
    return [
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
    ]


def upgrade() -> None:
    op.create_table(
        "merchant_imports",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("merchant_id", sa.Text(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            PROVIDER_PATTERN, name=op.f("ck_merchant_imports_provider_valid")
        ),
        sa.CheckConstraint(
            EXTERNAL_ID_LENGTH, name=op.f("ck_merchant_imports_external_id_valid")
        ),
        sa.ForeignKeyConstraint(
            ["merchant_id"],
            ["merchants.id"],
            name=op.f("fk_merchant_imports_merchant_id_merchants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_merchant_imports")),
        sa.UniqueConstraint(
            "merchant_id", name=op.f("uq_merchant_imports_merchant_id")
        ),
        sa.UniqueConstraint(
            "provider",
            "external_id",
            name="uq_merchant_imports_provider_external_id",
        ),
    )
    op.create_table(
        "offer_imports",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("offer_id", sa.Text(), nullable=False),
        sa.Column("source_hash", sa.Text(), nullable=False),
        sa.Column("applied_source_hash", sa.Text(), nullable=False),
        sa.Column("applied_content_hash", sa.Text(), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_changed_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            PROVIDER_PATTERN, name=op.f("ck_offer_imports_provider_valid")
        ),
        sa.CheckConstraint(
            EXTERNAL_ID_LENGTH, name=op.f("ck_offer_imports_external_id_valid")
        ),
        sa.CheckConstraint(
            "char_length(source_hash) = 64"
            " AND char_length(applied_source_hash) = 64"
            " AND char_length(applied_content_hash) = 64",
            name=op.f("ck_offer_imports_hashes_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["offer_id"],
            ["offers.id"],
            name=op.f("fk_offer_imports_offer_id_offers"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_offer_imports")),
        sa.UniqueConstraint("offer_id", name=op.f("uq_offer_imports_offer_id")),
        sa.UniqueConstraint(
            "provider", "external_id", name="uq_offer_imports_provider_external_id"
        ),
    )


def downgrade() -> None:
    op.drop_table("offer_imports")
    op.drop_table("merchant_imports")
