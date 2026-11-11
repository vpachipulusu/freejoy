"""Test doubles for the partner surface: an in-memory key store and a session that only counts commits."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

from app.core.partner_keys import GeneratedKey, generate_key
from app.partner.keys import KeyProfile, StoredKey
from app.repositories.admin_partner_keys import PartnerKeyRecord

ENVIRONMENT = "test"
MERCHANT_ID = "merchant000000000000000a1"
KEY_ID = "keyid0000000000000000001"


class FakePartnerSession:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


@dataclass
class FakePartnerSessions:
    """A ``SessionProvider`` that hands out ``FakePartnerSession`` objects."""

    opened: int = 0
    sessions: list[FakePartnerSession] = field(default_factory=list)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[Any]:
        self.opened += 1
        session = FakePartnerSession()
        self.sessions.append(session)
        yield session


class FakeKeyStore:
    """Rows keyed by key id. ``touched`` lists the key ids ``touch`` was called for."""

    def __init__(self) -> None:
        self.rows: dict[str, StoredKey] = {}
        self.profiles: dict[str, KeyProfile] = {}
        self.touched: list[str] = []
        self.finds: list[str] = []
        self.fail_touch = False
        self.fail_find = False

    def add(
        self,
        *,
        key_id: str = KEY_ID,
        merchant_id: str = MERCHANT_ID,
        usable: bool = True,
        merchant_name: str = "Shop",
        label: str = "Feed",
        environment: str = ENVIRONMENT,
    ) -> GeneratedKey:
        key = generate_key(environment, key_id)
        self.rows[key_id] = StoredKey(key_id, merchant_id, key.digest, usable, True)
        self.profiles[key_id] = KeyProfile(merchant_name, label)
        return key

    def revoke(self, key_id: str) -> None:
        self.rows[key_id] = replace(self.rows[key_id], usable=False)

    async def find(self, key_id: str) -> StoredKey | None:
        self.finds.append(key_id)
        if self.fail_find:
            raise ConnectionError("database down")
        return self.rows.get(key_id)

    async def touch(self, key_id: str) -> None:
        if self.fail_touch:
            raise ConnectionError("database down")
        self.touched.append(key_id)
        self.rows[key_id] = replace(self.rows[key_id], touch_due=False)

    async def profile(self, key_id: str) -> KeyProfile | None:
        return self.profiles.get(key_id)


ADMIN_NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


class FakeAdminKeyStore:
    def __init__(self) -> None:
        self.rows: dict[str, PartnerKeyRecord] = {}
        self.hashes: dict[str, str] = {}
        self.locked_reads: list[str] = []

    async def list_for_merchant(self, merchant_id: str) -> list[PartnerKeyRecord]:
        return [r for r in self.rows.values() if r.merchant_id == merchant_id]

    async def get(
        self, merchant_id: str, key_id: str, *, lock: bool = False
    ) -> PartnerKeyRecord | None:
        if lock:
            self.locked_reads.append(key_id)
        found = self.rows.get(key_id)
        return found if found and found.merchant_id == merchant_id else None

    async def insert(self, values: dict[str, Any]) -> PartnerKeyRecord:
        record = PartnerKeyRecord(
            id=values["id"],
            merchant_id=values["merchant_id"],
            label=values["label"],
            created_by=values["created_by"],
            created_at=ADMIN_NOW,
            revoked_at=None,
            last_used_at=None,
        )
        self.rows[record.id] = record
        self.hashes[record.id] = values["key_hash"]
        return record

    async def revoke(self, merchant_id: str, key_id: str) -> PartnerKeyRecord:
        self.rows[key_id] = replace(self.rows[key_id], revoked_at=ADMIN_NOW)
        return self.rows[key_id]
