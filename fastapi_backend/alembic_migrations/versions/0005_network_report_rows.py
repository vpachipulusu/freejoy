"""Network report rows

Adds ``network_report_rows`` (model in ``app/models/platform/network_report.py``): one row per
transaction in an affiliate network's own report, keyed by ``(provider, external_id)`` (for Awin, the
transaction id), with the commission status (mapped onto ``pending``, ``approved``, ``declined`` or
``paid`` or ``deleted``, the raw value kept beside it), the commission and sale amounts in minor units, the currency,
the click reference the network reported, and a hash of the normalised row so a rerun can tell a
change at the network from nothing new. Revenue is attributed from these rows, never from clicks.
The optional links to the catalogue (offer, affiliate link, merchant) are ``ON DELETE SET NULL``:
deleting any of them keeps the reported revenue. Nothing else is touched.

The downgrade drops the table, which loses every imported report row. A later
``commands.import_awin_reports`` run over the same dates would bring the rows back from the network
(the network keeps its own history), but anything the network no longer reports would be gone for
good: run it only against a throwaway database or before any report has been imported.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PROVIDER_PATTERN = "provider ~ '^[a-z][a-z0-9_]{0,39}$'"
EXTERNAL_ID_LENGTH = "char_length(external_id) BETWEEN 1 AND 64"


def upgrade() -> None:
    op.create_table(
        "network_report_rows",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("offer_id", sa.Text(), nullable=True),
        sa.Column("affiliate_link_id", sa.Text(), nullable=True),
        sa.Column("merchant_external_id", sa.Text(), nullable=False),
        sa.Column("merchant_id", sa.Text(), nullable=True),
        sa.Column("transaction_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("commission_status", sa.Text(), nullable=False),
        sa.Column("commission_status_raw", sa.Text(), nullable=False),
        sa.Column("commission_amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("sale_amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.Text(), nullable=False),
        sa.Column("click_ref", sa.Text(), nullable=True),
        sa.Column("report_hash", sa.Text(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
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
            PROVIDER_PATTERN, name=op.f("ck_network_report_rows_provider_valid")
        ),
        sa.CheckConstraint(
            EXTERNAL_ID_LENGTH, name=op.f("ck_network_report_rows_external_id_valid")
        ),
        sa.CheckConstraint(
            "char_length(merchant_external_id) BETWEEN 1 AND 64",
            name=op.f("ck_network_report_rows_merchant_external_id_valid"),
        ),
        sa.CheckConstraint(
            "commission_status IN ('pending', 'approved', 'declined', 'paid', 'deleted')",
            name=op.f("ck_network_report_rows_commission_status_valid"),
        ),
        sa.CheckConstraint(
            "char_length(commission_status_raw) BETWEEN 1 AND 32",
            name=op.f("ck_network_report_rows_commission_status_raw_valid"),
        ),
        sa.CheckConstraint(
            "commission_amount_minor >= 0 AND sale_amount_minor >= 0",
            name=op.f("ck_network_report_rows_amounts_valid"),
        ),
        sa.CheckConstraint(
            "currency ~ '^[A-Z]{3}$'",
            name=op.f("ck_network_report_rows_currency_valid"),
        ),
        sa.CheckConstraint(
            "click_ref IS NULL OR char_length(click_ref) BETWEEN 1 AND 100",
            name=op.f("ck_network_report_rows_click_ref_valid"),
        ),
        sa.CheckConstraint(
            "report_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_network_report_rows_report_hash_valid"),
        ),
        sa.CheckConstraint(
            "last_seen_at >= first_seen_at",
            name=op.f("ck_network_report_rows_seen_order_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["offer_id"],
            ["offers.id"],
            name=op.f("fk_network_report_rows_offer_id_offers"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["affiliate_link_id"],
            ["affiliate_links.id"],
            name=op.f("fk_network_report_rows_affiliate_link_id_affiliate_links"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["merchant_id"],
            ["merchants.id"],
            name=op.f("fk_network_report_rows_merchant_id_merchants"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_network_report_rows")),
        sa.UniqueConstraint(
            "provider",
            "external_id",
            name="uq_network_report_rows_provider_external_id",
        ),
    )
    op.create_index(
        "ix_network_report_rows_provider_transaction_at",
        "network_report_rows",
        ["provider", "transaction_at"],
        unique=False,
    )
    op.create_index(
        "ix_network_report_rows_offer_id",
        "network_report_rows",
        ["offer_id"],
        unique=False,
    )
    op.create_index(
        "ix_network_report_rows_affiliate_link_id",
        "network_report_rows",
        ["affiliate_link_id"],
        unique=False,
    )
    op.create_index(
        "ix_network_report_rows_merchant_id",
        "network_report_rows",
        ["merchant_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table("network_report_rows")  # its indexes and constraints go with it
