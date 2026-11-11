"""A backend for the admin Playwright journeys when no Firebase test project is available. TEST ONLY.

    ENVIRONMENT=test \\
    DATABASE_URL=postgresql+asyncpg://postgres:<pw>@localhost:<port>/<your_test_db> \\
    BACKEND_INTERNAL_KEY=<32+ random chars> \\
    E2E_ADMIN_SESSION_COOKIE=<24+ random chars> E2E_PLAIN_SESSION_COOKIE=<24+ random chars> \\
    uv run python -m tests.e2e_identity_server --port 8171

It serves the real application, with one change: the identity provider accepts exactly the two session values
named above (no Firebase call, no network) and the database gets two matching users, an admin with every
permission and a signed-in user with none. The browser then sends the same value as its ``session`` cookie.

Guards, all fail closed (exit code 1, nothing written):

* ``ENVIRONMENT`` must be exactly ``local`` or ``test``;
* the database name must end in ``_test``;
* the database host must be loopback (the rule ``commands.seed_catalogue`` uses);
* both session values must be at least 24 characters and different from each other;
* the listener binds to 127.0.0.1 only.

It lives under ``tests/`` so it is not part of the application image, and it is never imported by the app.
"""

import argparse
import asyncio
import os
import secrets
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import uvicorn
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app.core import firebase
from app.core.config import settings
from commands.seed_catalogue import is_local_database, is_seed_allowed

MIN_SESSION_LENGTH = 24
LOOPBACK = "127.0.0.1"
FAKE_AUTH_TIME = 1_700_000_000


@dataclass(frozen=True)
class FakeAccount:
    user_id: str
    uid: str
    email: str
    name: str
    admin: bool


ADMIN = FakeAccount(
    "usr_e2e_admin", "e2e-admin-uid", "e2e-admin@example.test", "E2E Admin", True
)
PLAIN = FakeAccount(
    "usr_e2e_plain", "e2e-plain-uid", "e2e-plain@example.test", "E2E Plain User", False
)


def fail(message: str) -> None:
    print(f"e2e-identity-server: {message}", file=sys.stderr)
    raise SystemExit(1)


def is_test_database(url: str) -> bool:
    """True when the database name ends in ``_test`` (the repo's rule for databases tests may write)."""
    return (make_url(url).database or "").endswith("_test")


def read_sessions(environ: Mapping[str, str]) -> dict[str, FakeAccount]:
    admin = environ.get("E2E_ADMIN_SESSION_COOKIE", "")
    plain = environ.get("E2E_PLAIN_SESSION_COOKIE", "")
    if len(admin) < MIN_SESSION_LENGTH or len(plain) < MIN_SESSION_LENGTH:
        fail(
            "E2E_ADMIN_SESSION_COOKIE and E2E_PLAIN_SESSION_COOKIE must be "
            f"at least {MIN_SESSION_LENGTH} characters"
        )
    if secrets.compare_digest(admin.encode(), plain.encode()):
        fail("the two session values must differ")
    return {admin: ADMIN, plain: PLAIN}


def install_identity(sessions: dict[str, FakeAccount]) -> None:
    """Replace session-cookie verification; every other value is refused like a bad Firebase cookie."""

    async def verify_session_cookie(self: object, token: str) -> Mapping[str, Any]:
        for value, account in sessions.items():
            if secrets.compare_digest(token.encode(), value.encode()):
                return {
                    "uid": account.uid,
                    "email": account.email,
                    "email_verified": True,
                    "auth_time": FAKE_AUTH_TIME,
                    "name": account.name,
                }
        raise firebase.AuthenticationFailed()

    firebase.FirebaseIdentityProvider.verify_session_cookie = verify_session_cookie  # type: ignore[method-assign]


async def seed_users() -> None:
    engine = create_async_engine(settings.DATABASE_URL, connect_args={"timeout": 10})
    try:
        async with engine.begin() as connection:
            for account in (ADMIN, PLAIN):
                await connection.execute(
                    text(
                        "insert into users (id, firebase_uid, email, email_verified, display_name) "
                        "values (:id, :uid, :email, true, :name) on conflict (firebase_uid) do nothing"
                    ),
                    {
                        "id": account.user_id,
                        "uid": account.uid,
                        "email": account.email,
                        "name": account.name,
                    },
                )
            await connection.execute(
                text(
                    "insert into admin_users (id, user_id, role, permissions) "
                    "values ('adm_e2e_admin', :user_id, 'super_admin', '[\"*\"]'::jsonb) "
                    "on conflict (user_id) do nothing"
                ),
                {"user_id": ADMIN.user_id},
            )
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()

    if not is_seed_allowed(settings.ENVIRONMENT):
        fail("ENVIRONMENT must be exactly 'local' or 'test'")
    if not is_local_database(str(settings.DATABASE_URL)):
        fail("DATABASE_URL must point at a loopback host")
    if not is_test_database(str(settings.DATABASE_URL)):
        fail("the database name must end in '_test'")
    sessions = read_sessions(os.environ)

    install_identity(sessions)
    asyncio.run(seed_users())
    from app.main import app

    uvicorn.run(app, host=LOOPBACK, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
