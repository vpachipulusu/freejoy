"""Partner API keys

Adds ``partner_api_keys`` (model in ``app/models/platform/partner_api_key.py``): one row per key an admin
issues to a merchant for the partner API (ADR-013). The row id is the key id written into the key
(``fj_<env>_<key_id>_<secret>``); only the SHA-256 of the whole key is stored. Ownership is per merchant
(``merchant_id``, ``ON DELETE CASCADE``: a deleted merchant's keys die with it). ``revoked_at`` and
``last_used_at`` are the only columns that change after creation.

The restricted database role for the partner surface is NOT created here: roles are cluster-wide and
belong to the owner. ``commands/sql/partner_role.sql`` is the script that creates it and grants it
``SELECT`` and ``UPDATE (last_used_at)`` on this table.

The downgrade drops the table, which loses every key (all of them stop working): run it only against a
throwaway database.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "partner_api_keys",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("merchant_id", sa.Text(), nullable=False),
        sa.Column("key_hash", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "id ~ '^[a-z0-9]{24}$'", name=op.f("ck_partner_api_keys_id_valid")
        ),
        sa.CheckConstraint(
            "key_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_partner_api_keys_key_hash_valid"),
        ),
        sa.CheckConstraint(
            "char_length(label) BETWEEN 1 AND 80",
            name=op.f("ck_partner_api_keys_label_valid"),
        ),
        sa.CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= created_at",
            name=op.f("ck_partner_api_keys_revoked_after_created"),
        ),
        sa.ForeignKeyConstraint(
            ["merchant_id"],
            ["merchants.id"],
            name=op.f("fk_partner_api_keys_merchant_id_merchants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_partner_api_keys")),
        sa.UniqueConstraint("key_hash", name=op.f("uq_partner_api_keys_key_hash")),
    )
    op.create_index(
        "ix_partner_api_keys_merchant_id_created_at",
        "partner_api_keys",
        ["merchant_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_partner_api_keys_merchant_id_created_at", table_name="partner_api_keys"
    )
    op.drop_table("partner_api_keys")
