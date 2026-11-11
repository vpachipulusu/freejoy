from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base
from app.models.platform.mixins import IdMixin, TimestampMixin

REF_PATTERN = "^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"
KEY_ID_PATTERN = "^[a-z0-9]{24}$"
HASH_PATTERN = "^[0-9a-f]{64}$"
# The largest sanitised proposal that may wait for an editor. The API's caps allow 6,900 characters of text and a
# 2,048 character link; in 4-byte characters that is about 30 KB as ``jsonb::text``, so the limit leaves room above it
# (``tests/api/partner/test_partner_content.py`` pins the worst case).
MAX_PENDING_BYTES = 49_152


class PartnerSubmission(IdMixin, TimestampMixin, Base):
    """One offer a retailer submitted through the partner API (ADR-013), keyed by the retailer's own ``ref``.

    Tenancy is a column: every partner query carries ``merchant_id`` and ``(merchant_id, ref)`` is unique, so
    a ref of another merchant is simply not found. ``offer_id`` is unique (one submission per offer) and
    cascades, so deleting the offer deletes the submission. The offer itself is always created as a ``DRAFT``;
    nothing in this table or in the partner role can publish it.

    ``content_hash`` is the SHA-256 of the latest *sanitised* content the retailer submitted (what a
    create would write), so sending the same content twice changes nothing. ``pending_*`` hold a proposed
    update that an editor has not applied yet (the offer is not an untouched draft): ``pending_payload`` is the
    sanitised, validated content (never raw request text) and is capped. The three ``pending_*`` columns are all
    set or all null. ``key_id`` is the key that made the latest submission (plain text, no foreign key, like
    ``partner_api_keys.created_by``). ``first_published_at`` is set by a database trigger the first time the
    offer becomes ``PUBLISHED``, which is how the partner sees ``declined`` (archived, never published) apart
    from ``ended``.
    """

    __tablename__ = "partner_submissions"
    __table_args__ = (
        UniqueConstraint(
            "merchant_id", "ref", name="uq_partner_submissions_merchant_id_ref"
        ),
        Index(
            "ix_partner_submissions_merchant_id_created_at", "merchant_id", "created_at"
        ),
        CheckConstraint(f"ref ~ '{REF_PATTERN}'", name="ref_valid"),
        CheckConstraint(f"key_id ~ '{KEY_ID_PATTERN}'", name="key_id_valid"),
        CheckConstraint(f"content_hash ~ '{HASH_PATTERN}'", name="content_hash_valid"),
        CheckConstraint(
            "(pending_payload IS NULL AND pending_hash IS NULL AND pending_at IS NULL)"
            " OR (pending_payload IS NOT NULL AND pending_hash IS NOT NULL AND pending_at IS NOT NULL"
            " AND jsonb_typeof(pending_payload) = 'object'"
            f" AND pending_hash ~ '{HASH_PATTERN}'"
            f" AND octet_length(pending_payload::text) <= {MAX_PENDING_BYTES})",
            name="pending_complete",
        ),
    )

    merchant_id: Mapped[str] = mapped_column(
        ForeignKey("merchants.id", ondelete="CASCADE")
    )
    ref: Mapped[str]
    offer_id: Mapped[str] = mapped_column(
        ForeignKey("offers.id", ondelete="CASCADE"), unique=True
    )
    content_hash: Mapped[str]
    pending_payload: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(none_as_null=True)
    )
    pending_hash: Mapped[str | None]
    pending_at: Mapped[datetime | None]
    submitted_at: Mapped[datetime] = mapped_column(server_default=func.now())
    key_id: Mapped[str]
    first_published_at: Mapped[datetime | None]
