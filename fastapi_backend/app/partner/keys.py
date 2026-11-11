"""How the partner surface reads partner keys (ADR-013).

``app/partner`` may import only ``app.core`` and itself, so it cannot use the ORM models or the admin
repository. It describes the two tables it reads with SQLAlchemy Core (names, columns and types) and talks to
them through the restricted ``freejoy_partner`` role, which has just the grants this file needs
(``commands/sql/partner_role.sql``). ``tests/api/partner/test_partner_table_definitions.py`` checks every table and
column named here exists in the ORM metadata, so the two cannot drift.
"""

from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy import Boolean, DateTime, Text, column, func, or_, select, table, update
from sqlalchemy.ext.asyncio import AsyncSession

partner_api_keys = table(
    "partner_api_keys",
    column("id", Text),
    column("merchant_id", Text),
    column("key_hash", Text),
    column("label", Text),
    column("revoked_at", DateTime(timezone=True)),
    column("last_used_at", DateTime(timezone=True)),
)
merchants = table(
    "merchants", column("id", Text), column("name", Text), column("active", Boolean)
)

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
    # ``last_used_at`` is empty or older than ``LAST_USED_GRANULARITY_SECONDS`` on the database clock.
    touch_due: bool = False

    def __repr__(self) -> str:
        return f"StoredKey(key_id={self.key_id!r})"


@dataclass(frozen=True)
class KeyProfile:
    merchant_name: str
    key_label: str


class KeyLookup(Protocol):
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
                    self._touch_due().label("touch_due"),
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
            touch_due=bool(row.touch_due),
        )

    @staticmethod
    def _touch_due() -> Any:
        """``last_used_at`` is empty or older than the granularity, on the database clock."""
        stale = func.now() - func.make_interval(
            0, 0, 0, 0, 0, 0, LAST_USED_GRANULARITY_SECONDS
        )
        return or_(
            partner_api_keys.c.last_used_at.is_(None),
            partner_api_keys.c.last_used_at < stale,
        )

    async def touch(self, key_id: str) -> None:
        """Move ``last_used_at`` to the database clock, unless it already moved a moment ago.

        The caller asks only when ``StoredKey.touch_due``; the condition is repeated here so two racing
        requests still write once.
        """
        await self._session.execute(
            update(partner_api_keys)
            .where(partner_api_keys.c.id == key_id, self._touch_due())
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
