import enum
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base
from app.models.platform.catalogue_import import (
    EXTERNAL_ID_LENGTH,
    HASH_LENGTH,
    PROVIDER_PATTERN,
)
from app.models.platform.mixins import IdMixin, TimestampMixin

MAX_STATUS_RAW_LENGTH = 32
MAX_CLICK_REF_LENGTH = 100


class CommissionStatus(enum.StrEnum):
    """The states a network's commission status is mapped onto, one to one (lower-case, stored as text)."""

    PENDING = "pending"
    APPROVED = "approved"
    DECLINED = "declined"
    PAID = "paid"
    DELETED = "deleted"


_STATUSES = ", ".join(f"'{status.value}'" for status in CommissionStatus)


class NetworkReportRow(IdMixin, TimestampMixin, Base):
    """One transaction as the affiliate network's own report states it.

    Revenue attribution comes from here and never from ``click_events`` (which are counts only).
    ``(provider, external_id)`` is the network's transaction id, so a rerun finds the same row. The
    links to the catalogue (``offer_id``, ``affiliate_link_id``, ``merchant_id``) are optional and
    ``SET NULL``: an unmatched row is kept, and deleting an offer, link or merchant never deletes
    reported revenue. ``report_hash`` fingerprints the normalised row so a rerun can tell "the
    network changed this transaction" (a status moved, an amount was amended) from "nothing new".
    Money is an integer in minor units plus a 3-letter currency, never a float.
    ``docs/developer/backend-network-reports.md`` has the matching rules and their limits.
    """

    __tablename__ = "network_report_rows"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "external_id",
            name="uq_network_report_rows_provider_external_id",
        ),
        CheckConstraint(PROVIDER_PATTERN, name="provider_valid"),
        CheckConstraint(EXTERNAL_ID_LENGTH, name="external_id_valid"),
        CheckConstraint(
            "char_length(merchant_external_id) BETWEEN 1 AND 64",
            name="merchant_external_id_valid",
        ),
        CheckConstraint(
            f"commission_status IN ({_STATUSES})", name="commission_status_valid"
        ),
        CheckConstraint(
            f"char_length(commission_status_raw) BETWEEN 1 AND {MAX_STATUS_RAW_LENGTH}",
            name="commission_status_raw_valid",
        ),
        CheckConstraint(
            "commission_amount_minor >= 0 AND sale_amount_minor >= 0",
            name="amounts_valid",
        ),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency_valid"),
        CheckConstraint(
            f"click_ref IS NULL OR char_length(click_ref) BETWEEN 1 AND {MAX_CLICK_REF_LENGTH}",
            name="click_ref_valid",
        ),
        CheckConstraint(
            f"report_hash ~ '^[0-9a-f]{{{HASH_LENGTH}}}$'", name="report_hash_valid"
        ),
        CheckConstraint("last_seen_at >= first_seen_at", name="seen_order_valid"),
        Index(
            "ix_network_report_rows_provider_transaction_at",
            "provider",
            "transaction_at",
        ),
        Index("ix_network_report_rows_offer_id", "offer_id"),
        # Deleting a link or a merchant sets these to NULL: without an index that scans the table.
        Index("ix_network_report_rows_affiliate_link_id", "affiliate_link_id"),
        Index("ix_network_report_rows_merchant_id", "merchant_id"),
    )

    provider: Mapped[str]
    external_id: Mapped[str]
    offer_id: Mapped[str | None] = mapped_column(
        ForeignKey("offers.id", ondelete="SET NULL")
    )
    affiliate_link_id: Mapped[str | None] = mapped_column(
        ForeignKey("affiliate_links.id", ondelete="SET NULL")
    )
    # The advertiser id as the report gives it; ``merchant_id`` is what it resolved to (if anything).
    merchant_external_id: Mapped[str]
    merchant_id: Mapped[str | None] = mapped_column(
        ForeignKey("merchants.id", ondelete="SET NULL")
    )
    transaction_at: Mapped[datetime]
    commission_status: Mapped[str]
    commission_status_raw: Mapped[str]
    commission_amount_minor: Mapped[int] = mapped_column(BigInteger)
    sale_amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str]
    click_ref: Mapped[str | None]
    report_hash: Mapped[str]
    first_seen_at: Mapped[datetime]
    last_seen_at: Mapped[datetime]
