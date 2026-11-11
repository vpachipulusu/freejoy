"""Queries behind the admin partner keys API (ADR-013). No business rules live here.

Times that are compared with ``created_at`` (``revoked_at``) come from the database clock, as
``created_at`` does, so a skewed application clock cannot break the ``revoked_after_created`` check.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, select, update

from app.models.platform.partner_api_key import PartnerApiKey
from app.repositories.base import BaseRepository

MAX_LISTED_KEYS = 200


@dataclass(frozen=True)
class PartnerKeyRecord:
    """A key as the admin sees it: never the key itself, never its hash."""

    id: str
    merchant_id: str
    label: str
    created_by: str
    created_at: datetime
    revoked_at: datetime | None
    last_used_at: datetime | None


def _record(row: PartnerApiKey) -> PartnerKeyRecord:
    return PartnerKeyRecord(
        id=row.id,
        merchant_id=row.merchant_id,
        label=row.label,
        created_by=row.created_by,
        created_at=row.created_at,
        revoked_at=row.revoked_at,
        last_used_at=row.last_used_at,
    )


class AdminPartnerKeyRepository(BaseRepository):
    async def list_for_merchant(self, merchant_id: str) -> list[PartnerKeyRecord]:
        """Newest first, at most ``MAX_LISTED_KEYS``; the hash is never loaded."""
        rows = await self._session.execute(
            select(
                PartnerApiKey.id,
                PartnerApiKey.merchant_id,
                PartnerApiKey.label,
                PartnerApiKey.created_by,
                PartnerApiKey.created_at,
                PartnerApiKey.revoked_at,
                PartnerApiKey.last_used_at,
            )
            .where(PartnerApiKey.merchant_id == merchant_id)
            .order_by(PartnerApiKey.created_at.desc(), PartnerApiKey.id)
            .limit(MAX_LISTED_KEYS)
        )
        return [PartnerKeyRecord(*row) for row in rows]

    async def get(
        self, merchant_id: str, key_id: str, *, lock: bool = False
    ) -> PartnerKeyRecord | None:
        statement = select(PartnerApiKey).where(
            PartnerApiKey.id == key_id, PartnerApiKey.merchant_id == merchant_id
        )
        if lock:
            statement = statement.with_for_update()
        row = await self._session.scalar(statement)
        return _record(row) if row else None

    async def insert(self, values: dict[str, Any]) -> PartnerKeyRecord:
        row = PartnerApiKey(**values)
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return _record(row)

    async def revoke(self, merchant_id: str, key_id: str) -> PartnerKeyRecord:
        row = await self._session.scalar(
            update(PartnerApiKey)
            .where(
                PartnerApiKey.id == key_id,
                PartnerApiKey.merchant_id == merchant_id,
                PartnerApiKey.revoked_at.is_(None),
            )
            .values(revoked_at=func.now())
            .returning(PartnerApiKey)
            .execution_options(populate_existing=True)
        )
        if row is None:  # pragma: no cover - the caller holds the row lock
            raise LookupError(key_id)
        return _record(row)
