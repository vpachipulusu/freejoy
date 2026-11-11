"""Every error a partner caller can get has a fixed message and echoes nothing the caller sent (ADR-013).

The shared error handler passes a string ``detail`` through, so this is a convention that has to be checked:
each error body of the partner surface is one of a closed set of messages, has only the two envelope keys, and
contains neither the path, the header values, the key nor the body the caller sent. 413 and 500 come from a
throwaway sub-app that has routes to provoke them.
"""

import json

import pytest
from fastapi import Request
from pydantic import BaseModel

from app.core.config import settings
from app.core.partner_keys import key_digest
from app.core.rate_limit import PARTNER_AUTH_FAILURE_POLICY
from app.main import app
from app.partner.app import create_partner_api, create_partner_authenticator
from app.partner.surface import PartnerSurface
from tests.api.partner.support import RawResponse, call_raw
from tests.fakes import RaisingRateLimiter
from tests.partner_world import PartnerWorld

FIXED_MESSAGES = frozenset(
    {
        "Unauthorized",
        "Not Found",
        "Method Not Allowed",
        "Payload too large",
        "Invalid request",
        "Too many requests",
        "Internal server error",
        "Service temporarily unavailable",
        "Conflict",
    }
)
MARKER = "zz-request-marker-9f3"


class Item(BaseModel):
    count: int


def _surface(max_body_bytes: int = 64) -> PartnerSurface:
    api = create_partner_api()

    @api.post("/typed")
    async def typed(item: Item) -> None:
        return None

    @api.get("/boom")
    async def boom() -> None:
        raise RuntimeError(f"detail {MARKER}")

    @api.post("/echo")
    async def echo(request: Request) -> dict[str, int]:
        return {"size": len(await request.body())}

    return PartnerSurface(
        api,
        enabled=lambda: True,
        authenticator=create_partner_authenticator(),
        max_body_bytes=max_body_bytes,
    )


@pytest.fixture(autouse=True)
def surface_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "PARTNER_API_ENABLED", True)


def assert_fixed(response: RawResponse, forbidden: list[str]) -> None:
    body = response.json()
    assert set(body) == {"success", "error"}
    assert body["success"] is False
    assert body["error"] in FIXED_MESSAGES
    text = response.body.decode() + repr(response.headers)
    for item in forbidden:
        assert item not in text


async def test_every_partner_error_has_a_fixed_message_and_echoes_nothing(
    world: PartnerWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    surface = _surface()
    key = world.key.value
    secret_header = f"secret-header-{MARKER}"
    keyed = [*world.headers(), ("x-probe", secret_header)]
    forbidden = [MARKER, secret_header, key, key_digest(key), "203.0.113.9"]
    responses: dict[int, RawResponse] = {}

    # 401: no key, a wrong key, and a key with another credential
    responses[401] = await call_raw(
        surface, "GET", f"/{MARKER}", headers=[("x-probe", secret_header)]
    )
    assert_fixed(
        await call_raw(
            surface, "GET", "/x", headers=[("authorization", f"Bearer {MARKER}")]
        ),
        forbidden,
    )
    assert_fixed(
        await call_raw(surface, "GET", "/x", headers=[*keyed, ("cookie", "a=1")]),
        forbidden,
    )
    # 404 and 405 from the sub-app
    responses[404] = await call_raw(surface, "GET", f"/{MARKER}", headers=keyed)
    responses[405] = await call_raw(surface, "DELETE", "/me", headers=keyed)
    # 422: a body that fails validation
    responses[422] = await call_raw(
        surface,
        "POST",
        "/typed",
        headers=[*keyed, ("content-type", "application/json")],
        chunks=[json.dumps({"count": MARKER}).encode()],
    )
    # 413: too large, declared and counted
    responses[413] = await call_raw(
        surface, "POST", "/echo", headers=[*keyed, ("content-length", "9999")]
    )
    assert_fixed(
        await call_raw(
            surface, "POST", "/echo", headers=keyed, chunks=[b"x" * 40, b"y" * 40]
        ),
        forbidden,
    )
    # 500: a handler that fails with request data in its message
    responses[500] = await call_raw(surface, "GET", "/boom", headers=keyed)
    # 429 and 503 from the failure limit and the limiter
    wrong = [("authorization", f"Bearer {key[:-1]}{'0' if key[-1] != '0' else '1'}")]
    for _ in range(PARTNER_AUTH_FAILURE_POLICY.limit + 1):
        last = await call_raw(
            surface, "GET", "/x", headers=[*wrong, ("x-probe", secret_header)]
        )
    responses[429] = last
    from app.core import rate_limiter_provider

    monkeypatch.setattr(rate_limiter_provider, "_rate_limiter", RaisingRateLimiter())
    responses[503] = await call_raw(surface, "GET", "/x", headers=wrong)

    for status, response in responses.items():
        assert response.status == status, status
        assert_fixed(response, forbidden)


async def test_the_main_surface_unknown_path_error_does_not_echo_the_path(
    world: PartnerWorld,
) -> None:
    response = await call_raw(
        app, "GET", f"/partner/v1/{MARKER}/b", headers=world.headers()
    )

    assert response.status == 404
    assert_fixed(response, [MARKER, world.key.value])


async def test_a_405_names_only_the_allowed_methods(world: PartnerWorld) -> None:
    response = await call_raw(app, "POST", "/partner/v1/me", headers=world.headers())

    assert response.status == 405
    assert response.header("allow") == "GET"
    assert_fixed(response, [world.key.value])
