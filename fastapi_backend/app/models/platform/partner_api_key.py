from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base
from app.core.partner_keys import KEY_ID_LENGTH
from app.models.platform.mixins import CreatedAtMixin, IdMixin

MAX_LABEL_LENGTH = 80


class PartnerApiKey(IdMixin, CreatedAtMixin, Base):
    """A key a merchant's developer sends as ``Authorization: Bearer ...`` to the partner API (ADR-013).

    Ownership is per merchant, never per organisation. ``id`` IS the key id written into the key
    (``fj_<env>_<id>_<secret>``), so a lookup by id is a primary-key read. Only ``key_hash`` (the SHA-256
    of the whole key, see ``app.core.partner_keys``) is stored: the key itself is shown once at creation
    and is not recoverable. A revoked key keeps its row (``revoked_at``). Deleting the merchant deletes its keys with it (``ON DELETE CASCADE``: a deleted merchant's keys must stop); what survives is the audit trail, whose create and revoke rows hold the key id, merchant id and label (``audit_logs`` has no foreign keys).
    ``created_by`` is the admin's user id as plain text with no foreign key, like the audit trail: the
    row outlives the admin. ``last_used_at`` is moved by the partner role, which may update only that
    column (``commands/sql/partner_role.sql``).

    ``app/partner/keys.py`` reads this table with SQLAlchemy Core (the partner package may not import
    models); a test keeps its table and column names in step with this model.
    """

    __tablename__ = "partner_api_keys"
    __table_args__ = (
        CheckConstraint(f"id ~ '^[a-z0-9]{{{KEY_ID_LENGTH}}}$'", name="id_valid"),
        CheckConstraint("key_hash ~ '^[0-9a-f]{64}$'", name="key_hash_valid"),
        CheckConstraint(
            f"char_length(label) BETWEEN 1 AND {MAX_LABEL_LENGTH}", name="label_valid"
        ),
        CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= created_at",
            name="revoked_after_created",
        ),
        Index(
            "ix_partner_api_keys_merchant_id_created_at", "merchant_id", "created_at"
        ),
    )

    merchant_id: Mapped[str] = mapped_column(
        ForeignKey("merchants.id", ondelete="CASCADE")
    )
    key_hash: Mapped[str] = mapped_column(unique=True)
    label: Mapped[str]
    created_by: Mapped[str]
    revoked_at: Mapped[datetime | None]
    last_used_at: Mapped[datetime | None]
