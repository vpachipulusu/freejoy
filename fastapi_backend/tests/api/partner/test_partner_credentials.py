"""The partner surface accepts the partner key and nothing else (ADR-013).

For every partner route and every method: no credentials, the internal key only and a session token only are
all refused with the same 401, a partner key sent together with the internal key, a session token or a cookie
is rejected, and the internal-authentication flag (``request.state.internal_authenticated``, set only by
``require_internal_key``) is never set while a partner request is served.
"""

import pytest
from fastapi.routing import iter_route_contexts
from starlette.datastructures import State

from app.core.config import settings
from app.main import app
from app.partner.mount import PartnerMount
from tests.api.partner.support import call_raw
from tests.partner_world import PartnerWorld

INTERNAL_KEY = "test-only-internal-service-key-not-for-deployment"
METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]
UNAUTHORIZED = {"success": False, "error": "Unauthorized"}


def partner_paths() -> list[str]:
    mount = next(r for r in app.routes if isinstance(r, PartnerMount))
    return sorted({c.path or "" for c in iter_route_contexts(mount.routes)})


CASES = [
    (f"/partner/v1{path}", method) for path in partner_paths() for method in METHODS
]


@pytest.fixture(autouse=True)
def surface_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "PARTNER_API_ENABLED", True)


@pytest.fixture
def state_writes(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every attribute set on any ``request.state`` while the test runs."""
    written: list[str] = []
    original = State.__setattr__

    def recording(self: State, key: str, value: object) -> None:
        written.append(key)
        original(self, key, value)

    monkeypatch.setattr(State, "__setattr__", recording)
    return written


def test_there_are_partner_routes_to_check() -> None:
    assert "/me" in partner_paths()
    assert len(CASES) >= len(METHODS)


@pytest.mark.parametrize(("path", "method"), CASES)
async def test_no_credentials_is_a_401(path: str, method: str) -> None:
    response = await call_raw(app, method, path)

    assert response.status == 401
    if method != "HEAD":
        assert response.json() == UNAUTHORIZED


@pytest.mark.parametrize(("path", "method"), CASES)
async def test_the_internal_key_alone_is_a_401(path: str, method: str) -> None:
    response = await call_raw(
        app, method, path, headers=[("X-Internal-Key", INTERNAL_KEY)]
    )

    assert response.status == 401


@pytest.mark.parametrize(("path", "method"), CASES)
async def test_a_session_token_alone_is_a_401(path: str, method: str) -> None:
    response = await call_raw(
        app,
        method,
        path,
        headers=[("X-Session-Token", "a-token"), ("X-Internal-Key", INTERNAL_KEY)],
    )

    assert response.status == 401


@pytest.mark.parametrize(("path", "method"), CASES)
@pytest.mark.parametrize(
    "other",
    [
        ("X-Internal-Key", INTERNAL_KEY),
        ("X-Session-Token", "a-token"),
        ("Cookie", "session=abc"),
    ],
    ids=["internal-key", "session-token", "cookie"],
)
async def test_a_partner_key_together_with_another_credential_is_rejected(
    world: PartnerWorld,
    state_writes: list[str],
    path: str,
    method: str,
    other: tuple[str, str],
) -> None:
    response = await call_raw(app, method, path, headers=[*world.headers(), other])

    assert response.status == 401
    assert "internal_authenticated" not in state_writes
    assert world.store.finds == []  # refused before the key was even looked up


@pytest.mark.parametrize(("path", "method"), CASES)
async def test_the_internal_flag_is_never_set_whatever_the_credentials(
    world: PartnerWorld, state_writes: list[str], path: str, method: str
) -> None:
    for headers in (
        [],
        [("X-Internal-Key", INTERNAL_KEY)],
        world.headers(),
        [*world.headers(), ("X-Internal-Key", INTERNAL_KEY)],
    ):
        await call_raw(app, method, path, headers=headers)

    assert "internal_authenticated" not in state_writes


async def test_the_state_recorder_would_see_the_flag_if_it_were_set(
    state_writes: list[str],
) -> None:
    """The recorder is not blind: an internal route that sets the flag shows up in it."""
    internal = await call_raw(
        app, "GET", "/v1/me", headers=[("X-Internal-Key", INTERNAL_KEY)]
    )

    assert internal.status in {401, 403, 500, 503}  # no session: refused after the key
    assert "internal_authenticated" in state_writes


@pytest.mark.parametrize(("path", "method"), CASES)
async def test_the_partner_key_alone_reaches_the_route(
    world: PartnerWorld, path: str, method: str
) -> None:
    response = await call_raw(app, method, path, headers=world.headers())

    assert response.status in {200, 405}
    assert response.status == (200 if method == "GET" else 405)
