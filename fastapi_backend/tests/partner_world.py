"""A partner world with no database: a fake key store behind the real authenticator, a counting limiter.

The ``world`` fixture is made available to ``tests/api`` by ``tests/api/conftest.py``."""

from dataclasses import dataclass

import pytest

from app.core import rate_limiter_provider
from app.core.partner_keys import GeneratedKey
from app.partner import deps
from tests.fakes import FakeRateLimiter
from tests.fakes_partner import FakeKeyStore, FakePartnerSessions


@dataclass
class PartnerWorld:
    store: FakeKeyStore
    sessions: FakePartnerSessions
    limiter: FakeRateLimiter
    key: GeneratedKey

    def headers(self, key: GeneratedKey | None = None) -> list[tuple[str, str]]:
        return [("authorization", f"Bearer {(key or self.key).value}")]


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> PartnerWorld:
    store = FakeKeyStore()
    sessions = FakePartnerSessions()
    limiter = FakeRateLimiter()
    key = store.add()
    monkeypatch.setattr(deps, "get_partner_session_provider", lambda: sessions)
    monkeypatch.setattr(deps, "build_key_store", lambda session: store)
    monkeypatch.setattr(rate_limiter_provider, "_rate_limiter", limiter)
    return PartnerWorld(store, sessions, limiter, key)
