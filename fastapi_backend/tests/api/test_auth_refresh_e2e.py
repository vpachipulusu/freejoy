"""End to end: ``POST /v1/auth/session/refresh`` and ``GET /v1/me`` on real Postgres.

Same contract as ``test_merchant_e2e.py``: runs only when ``TEST_DATABASE_URL`` points at
a throwaway local ``*_test`` database, rebuilt with the real Alembic migrations. Only the
Firebase adapter is replaced (a token table); the internal key, session dependency, auth
service, repositories, transaction handling and routes are the production code.
"""

import os
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.api.deps import get_rate_limiter, get_session_provider
from app.core.config import settings
from app.core.database import DefaultSessionProvider, build_engine
from app.core.firebase import AuthenticationFailed
from app.main import create_app
from tests.alembic_support import run_alembic
from tests.fakes import FakeRateLimiter, as_limiter

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
BACKEND_ROOT = Path(__file__).resolve().parents[2]
KEY = "test-only-internal-service-key-not-for-deployment"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="TEST_DATABASE_URL is not set"
)


class TokenTableIdentity:
    """Firebase stand-in: tokens map to claims; unknown tokens are rejected."""

    def __init__(self) -> None:
        self.sessions: dict[str, dict[str, Any]] = {}
        self.id_tokens: dict[str, dict[str, Any]] = {}

    async def verify_id_token(self, token: str) -> dict[str, Any]:
        if token not in self.id_tokens:
            raise AuthenticationFailed()
        return self.id_tokens[token]

    async def verify_session_cookie(self, token: str) -> dict[str, Any]:
        if token not in self.sessions:
            raise AuthenticationFailed()
        return self.sessions[token]

    async def create_session_cookie(self, token: str, expires_in: int) -> str:
        return f"new-cookie-for-{token}"

    async def revoke_refresh_tokens(self, uid: str) -> None:
        return None


def claims(uid: str, verified: bool, age: int = 0) -> dict[str, Any]:
    return {
        "uid": uid,
        "email": f"{uid}@example.com",
        "email_verified": verified,
        "auth_time": int(time.time()) - age,
    }


def _migrate(url: str, *args: str) -> None:
    run_alembic(url, *args)


@pytest.fixture(scope="module")
def database_url() -> Iterator[str]:
    assert TEST_DATABASE_URL is not None
    target = make_url(TEST_DATABASE_URL)
    assert (target.database or "").endswith("_test"), (
        "refusing to rebuild a database whose name does not end in _test"
    )
    assert target.host in LOCAL_HOSTS, "refusing to rebuild a non-local database"
    _migrate(TEST_DATABASE_URL, "downgrade", "base")
    _migrate(TEST_DATABASE_URL, "upgrade", "head")
    yield TEST_DATABASE_URL


@pytest.fixture
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    built = build_engine(database_url)
    async with built.begin() as connection:
        await connection.execute(text("TRUNCATE users, organizations CASCADE"))
    yield built
    await built.dispose()


@pytest.fixture
def identity(monkeypatch: pytest.MonkeyPatch) -> TokenTableIdentity:
    fake = TokenTableIdentity()
    monkeypatch.setattr("app.api.auth.FirebaseIdentityProvider", lambda: fake)
    monkeypatch.setattr(settings, "BACKEND_INTERNAL_KEY", SecretStr(KEY))
    return fake


@pytest.fixture
async def client(
    engine: AsyncEngine, identity: TokenTableIdentity
) -> AsyncIterator[AsyncClient]:
    app = create_app()
    provider = DefaultSessionProvider(
        async_sessionmaker(engine, expire_on_commit=False)
    )
    app.dependency_overrides[get_session_provider] = lambda: provider
    limiter = FakeRateLimiter()
    app.dependency_overrides[get_rate_limiter] = lambda: as_limiter(limiter)
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(
        transport=transport, base_url="http://test", headers={"X-Internal-Key": KEY}
    ) as http:
        yield http


async def seed_user(
    engine: AsyncEngine, uid: str, verified: bool, tfa: bool = False
) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO users (id, firebase_uid, email, email_verified, "
                "two_factor_enabled) VALUES (:id, :uid, :email, :v, :tfa)"
            ),
            {
                "id": f"id-{uid}",
                "uid": uid,
                "email": f"{uid}@example.com",
                "v": verified,
                "tfa": tfa,
            },
        )


async def scalar(engine: AsyncEngine, sql: str, **params: Any) -> Any:
    async with engine.connect() as connection:
        return (await connection.execute(text(sql), params)).scalar_one()


def refresh(client: AsyncClient, id_token: str, session: str | None = "sess-ada"):
    headers = {"X-Session-Token": session} if session else {}
    return client.post(
        "/v1/auth/session/refresh", json={"id_token": id_token}, headers=headers
    )


LAST_LOGIN = "SELECT last_login_at FROM users WHERE firebase_uid = :uid"
VERIFIED = "SELECT email_verified FROM users WHERE firebase_uid = :uid"


async def test_refresh_accepts_stale_token_syncs_email_verified_and_writes_no_history(
    client: AsyncClient, engine: AsyncEngine, identity: TokenTableIdentity
) -> None:
    await seed_user(engine, "ada", verified=False)
    identity.sessions["sess-ada"] = claims("ada", verified=False)
    identity.id_tokens["id-ada"] = claims("ada", verified=True, age=7 * 86400)

    response = await refresh(client, "id-ada")

    assert response.status_code == 200
    assert response.json()["data"]["session_cookie"] == "new-cookie-for-id-ada"
    assert response.headers["cache-control"] == "no-store"
    assert await scalar(engine, VERIFIED, uid="ada") is True
    assert await scalar(engine, "SELECT count(*) FROM login_history") == 0
    assert await scalar(engine, LAST_LOGIN, uid="ada") is None


async def test_refresh_uid_mismatch_changes_nothing(
    client: AsyncClient, engine: AsyncEngine, identity: TokenTableIdentity
) -> None:
    await seed_user(engine, "ada", verified=False)
    await seed_user(engine, "bob", verified=False)
    identity.sessions["sess-ada"] = claims("ada", verified=False)
    identity.id_tokens["id-bob"] = claims("bob", verified=True)

    response = await refresh(client, "id-bob")

    assert response.status_code == 401
    assert "new-cookie" not in response.text
    assert await scalar(engine, "SELECT count(*) FROM users WHERE email_verified") == 0
    assert await scalar(engine, "SELECT count(*) FROM login_history") == 0


@pytest.mark.parametrize("session", [None, "", "unknown-session"])
async def test_refresh_without_valid_session_never_mints(
    client: AsyncClient,
    engine: AsyncEngine,
    identity: TokenTableIdentity,
    session: str | None,
) -> None:
    identity.id_tokens["id-ada"] = claims("ada", verified=True)

    response = await refresh(client, "id-ada", session=session)

    assert response.status_code == 401
    assert "new-cookie" not in response.text
    assert await scalar(engine, "SELECT count(*) FROM users") == 0


async def test_refresh_rejects_invalid_id_token(
    client: AsyncClient, engine: AsyncEngine, identity: TokenTableIdentity
) -> None:
    await seed_user(engine, "ada", verified=False)
    identity.sessions["sess-ada"] = claims("ada", verified=False)

    response = await refresh(client, "bogus")

    assert response.status_code == 401
    assert await scalar(engine, VERIFIED, uid="ada") is False


async def test_me_returns_two_factor_enabled(
    client: AsyncClient, engine: AsyncEngine, identity: TokenTableIdentity
) -> None:
    await seed_user(engine, "ada", verified=True, tfa=True)
    await seed_user(engine, "bob", verified=True, tfa=False)
    identity.sessions["sess-ada"] = claims("ada", verified=True)
    identity.sessions["sess-bob"] = claims("bob", verified=True)

    ada = await client.get("/v1/me", headers={"X-Session-Token": "sess-ada"})
    bob = await client.get("/v1/me", headers={"X-Session-Token": "sess-bob"})

    assert ada.json()["data"]["two_factor_enabled"] is True
    assert bob.json()["data"]["two_factor_enabled"] is False
