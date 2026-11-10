"""How the partner surface reads partner keys (ADR-013).

``app/partner`` may import only ``app.core`` and itself, so it cannot use the ORM models or the admin
repository. It describes the two tables it reads with SQLAlchemy Core (names and columns only) and talks to
them through the restricted ``freejoy_partner`` role, which has just the grants this file needs
(``commands/sql/partner_role.sql``). ``tests/api/partner/test_partner_table_definitions.py`` checks every table and
column named here exists in the ORM metadata, so the two cannot drift.
"""

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import column, func, or_, select, table, update
from sqlalchemy.ext.asyncio import AsyncSession

partner_api_keys = table(
    "partner_api_keys",
    column("id"),
    column("merchant_id"),
    column("key_hash"),
    column("label"),
    column("revoked_at"),
    column("last_used_at"),
)
merchants = table("merchants", column("id"), column("name"), column("active"))

# ``last_used_at`` moves at most this often per key: it is a "last seen" hint for the admin, not a log, and a
# write per request would be a write on the hot path of every partner call.
LAST_USED_GRANULARITY_SECONDS = 60


@dataclass(frozen=True)
class StoredKey:
    """What authentication needs about a key id. ``usable`` is false for a revoked key or an inactive merchant."""

    key_id: str
    merchant_id: str
    key_hash: str
    usable: bool

    def __repr__(self) -> str:
        return f"StoredKey(key_id={self.key_id!r})"


@dataclass(frozen=True)
class KeyProfile:
    merchant_name: str
    key_label: str


class KeyStore(Protocol):
    async def find(self, key_id: str) -> StoredKey | None: ...
    async def touch(self, key_id: str) -> None: ...
    async def profile(self, key_id: str) -> KeyProfile | None: ...


class PartnerKeyStore:
    """Key lookups on the partner role's session. The caller owns the session and the commit."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find(self, key_id: str) -> StoredKey | None:
        row = (
            await self._session.execute(
                select(
                    partner_api_keys.c.id,
                    partner_api_keys.c.merchant_id,
                    partner_api_keys.c.key_hash,
                    partner_api_keys.c.revoked_at.is_(None).label("not_revoked"),
                    merchants.c.active,
                )
                .join(merchants, merchants.c.id == partner_api_keys.c.merchant_id)
                .where(partner_api_keys.c.id == key_id)
            )
        ).one_or_none()
        if row is None:
            return None
        return StoredKey(
            key_id=row.id,
            merchant_id=row.merchant_id,
            key_hash=row.key_hash,
            usable=bool(row.not_revoked and row.active),
        )

    async def touch(self, key_id: str) -> None:
        """Move ``last_used_at`` to the database clock, unless it already moved a moment ago."""
        stale = func.now() - func.make_interval(
            0, 0, 0, 0, 0, 0, LAST_USED_GRANULARITY_SECONDS
        )
        await self._session.execute(
            update(partner_api_keys)
            .where(
                partner_api_keys.c.id == key_id,
                or_(
                    partner_api_keys.c.last_used_at.is_(None),
                    partner_api_keys.c.last_used_at < stale,
                ),
            )
            .values(last_used_at=func.now())
        )

    async def profile(self, key_id: str) -> KeyProfile | None:
        row = (
            await self._session.execute(
                select(merchants.c.name, partner_api_keys.c.label)
                .join(merchants, merchants.c.id == partner_api_keys.c.merchant_id)
                .where(partner_api_keys.c.id == key_id)
            )
        ).one_or_none()
        return None if row is None else KeyProfile(row.name, row.label)
