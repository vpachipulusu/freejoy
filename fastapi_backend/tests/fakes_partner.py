"""Test doubles for the partner surface: an in-memory key store and a session that only counts commits."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from typing import Any

from app.core.partner_keys import GeneratedKey, generate_key
from app.partner.keys import KeyProfile, StoredKey

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
        self.rows[key_id] = StoredKey(key_id, merchant_id, key.digest, usable)
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

    async def profile(self, key_id: str) -> KeyProfile | None:
        return self.profiles.get(key_id)
