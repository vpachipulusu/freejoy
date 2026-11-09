from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base
from app.models.platform.mixins import IdMixin, TimestampMixin

PROVIDER_PATTERN = "provider ~ '^[a-z][a-z0-9_]{0,39}$'"
EXTERNAL_ID_LENGTH = "char_length(external_id) BETWEEN 1 AND 64"
HASH_LENGTH = 64  # hex characters of a SHA-256 digest


class OfferImport(IdMixin, TimestampMixin, Base):
    """Where an imported offer came from, and what the import knows about it.

    One row per imported offer, keyed by ``(provider, external_id)`` (for Awin, the promotion id),
    so a rerun finds the same offer without matching on text. It lives beside ``offers`` rather than
    in nullable columns on it: the offers table stays what editors see, and the importer's
    bookkeeping can change without touching it.

    The three hashes are how the importer tells "the source changed" and "an editor changed the offer"
    apart (``docs/developer/backend-awin-import.md``):

    * ``source_hash``: the latest source content the importer has seen (normalised, so noise in the
      raw response does not count).
    * ``applied_source_hash``: the source content the offer currently reflects, as of ``applied_at``.
    * ``applied_content_hash``: a fingerprint of the offer and its links exactly as the importer left
      them. If the offer's fingerprint no longer matches, a person has changed it.

    ``source_changed_at`` is set when the source moved on and the importer did not apply it (the offer
    was edited, published, expired or archived), so a review queue can list those offers. It is cleared
    when the source returns to what the offer reflects or when the importer applies the new source.
    """

    __tablename__ = "offer_imports"
    __table_args__ = (
        UniqueConstraint(
            "provider", "external_id", name="uq_offer_imports_provider_external_id"
        ),
        CheckConstraint(PROVIDER_PATTERN, name="provider_valid"),
        CheckConstraint(EXTERNAL_ID_LENGTH, name="external_id_valid"),
        CheckConstraint(
            f"char_length(source_hash) = {HASH_LENGTH}"
            f" AND char_length(applied_source_hash) = {HASH_LENGTH}"
            f" AND char_length(applied_content_hash) = {HASH_LENGTH}",
            name="hashes_valid",
        ),
    )

    provider: Mapped[str]
    external_id: Mapped[str]
    # Cascade: deleting an offer removes its provenance; the next import would then create it afresh.
    offer_id: Mapped[str] = mapped_column(
        ForeignKey("offers.id", ondelete="CASCADE"), unique=True
    )
    source_hash: Mapped[str]
    applied_source_hash: Mapped[str]
    applied_content_hash: Mapped[str]
    applied_at: Mapped[datetime]
    source_changed_at: Mapped[datetime | None]


class MerchantImport(IdMixin, TimestampMixin, Base):
    """Which provider advertiser a merchant stands for (for Awin, the advertiser id).

    This is what maps an advertiser to a merchant. A merchant's name is never used to find one.
    """

    __tablename__ = "merchant_imports"
    __table_args__ = (
        UniqueConstraint(
            "provider", "external_id", name="uq_merchant_imports_provider_external_id"
        ),
        CheckConstraint(PROVIDER_PATTERN, name="provider_valid"),
        CheckConstraint(EXTERNAL_ID_LENGTH, name="external_id_valid"),
    )

    provider: Mapped[str]
    external_id: Mapped[str]
    merchant_id: Mapped[str] = mapped_column(
        ForeignKey("merchants.id", ondelete="CASCADE"), unique=True
    )
