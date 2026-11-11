"""The partner surface (ADR-013): the same answer for every path, on every response the same headers.

The real application is called through raw ASGI scopes (``support.call_raw``): an HTTP client
would normalise ``..`` and ``//`` away before sending, and then these tests would prove nothing.
``Recorder`` shows the path that reached the application is the one written here, and each
adversarial path must also end up in the partner sub-app (surface header present): had a path been
normalised to ``/v1/me`` it would have reached an internal route and answered 401 without it.
"""

import pytest
from fastapi import Request
from fastapi.routing import iter_route_contexts
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel
from starlette.middleware.cors import CORSMiddleware
from starlette.types import Message, Receive, Scope, Send

from app.core.config import Settings, settings
from app.core.request_id import RequestIdMiddleware
from app.main import app
from app.partner.app import create_partner_api
from app.partner.surface import MAX_BODY_BYTES, PartnerSurface
from tests.api.partner.support import (
    SURFACE_HEADERS,
    Recorder,
    allow_all,
    call_raw,
    error_logs,
)
from tests.partner_world import PartnerWorld

NOT_FOUND = {"success": False, "error": "Not Found"}
# (path as the server decodes it, raw_path as it came off the wire)
ADVERSARIAL = [
    ("/partner/v1/x", "/partner/v1/x"),
    ("/partner/v1", "/partner/v1"),
    ("/partner/v1/", "/partner/v1/"),
    ("/partner/v1/../v1/me", "/partner/v1/../v1/me"),
    ("/partner/v1/../v1/me", "/partner/v1/%2e%2e/v1/me"),
    ("/partner/v1/%2e%2e/v1/me", "/partner/v1/%252e%252e/v1/me"),
    ("/partner/v1/./x", "/partner/v1/./x"),
    ("/partner/v1//x", "/partner/v1//x"),
    ("/partner/v1/docs", "/partner/v1/docs"),
    ("/partner/v1/redoc", "/partner/v1/redoc"),
    ("/partner/v1/openapi.json", "/partner/v1/openapi.json"),
    ("/partner/v1/docs/oauth2-redirect", "/partner/v1/docs/oauth2-redirect"),
    ("/partner/v1/x/", "/partner/v1/x/"),
    # A newline: Starlette's own mount regex does not match across one (see PartnerMount).
    ("/partner/v1/a\nb", "/partner/v1/a%0Ab"),
    ("/partner/v1/a\nb/", "/partner/v1/a%0Ab/"),
    ("/partner/v1/\n/../../v1/me", "/partner/v1/%0A/../../v1/me"),
]


@pytest.fixture(params=[False, True], ids=["disabled", "enabled"])
def switch(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> bool:
    monkeypatch.setattr(settings, "PARTNER_API_ENABLED", request.param)
    return bool(request.param)


def _assert_partner_headers(headers: list[tuple[str, str]]) -> None:
    names = {name for name, _ in headers}
    for name, value in SURFACE_HEADERS.items():
        assert dict(headers)[name] == value
    assert "set-cookie" not in names
    assert not {name for name in names if name.startswith("access-control-")}
    assert "location" not in names


def test_the_switch_is_off_by_default() -> None:
    assert Settings.model_fields["PARTNER_API_ENABLED"].default is False


UNAUTHORIZED = {"success": False, "error": "Unauthorized"}


@pytest.mark.parametrize(("path", "raw_path"), ADVERSARIAL)
async def test_every_path_under_the_prefix_gets_one_answer_without_a_key(
    switch: bool, path: str, raw_path: str
) -> None:
    """Off: the 404 of an unknown path. On: the 401 every caller without a valid key gets, whatever the path
    (authentication comes before routing, so which paths exist is not learned)."""
    recorder = Recorder(app)
    baseline = await call_raw(app, "GET", "/partner/v1/unknown")

    response = await call_raw(recorder, "GET", path, raw_path=raw_path)

    assert recorder.paths == [path]  # the path was not normalised on the way in
    assert recorder.raw_paths == [raw_path.encode()]
    assert response.status == (401 if switch else 404)
    assert response.json() == (UNAUTHORIZED if switch else NOT_FOUND)
    assert response.body == baseline.body
    _assert_partner_headers(response.headers)


@pytest.mark.parametrize(("path", "raw_path"), ADVERSARIAL)
async def test_with_a_valid_key_every_adversarial_path_stays_in_the_sub_app_and_gets_its_404(
    world: PartnerWorld,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    raw_path: str,
) -> None:
    monkeypatch.setattr(settings, "PARTNER_API_ENABLED", True)
    recorder = Recorder(app)

    response = await call_raw(
        recorder, "GET", path, raw_path=raw_path, headers=world.headers()
    )

    assert recorder.paths == [path]
    assert response.status == 404
    assert response.json() == NOT_FOUND
    _assert_partner_headers(response.headers)


async def test_the_disabled_404_and_the_enabled_404_for_a_valid_key_are_byte_for_byte_the_same(
    world: PartnerWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "PARTNER_API_ENABLED", False)
    disabled = await call_raw(app, "GET", "/partner/v1/x", headers=world.headers())
    monkeypatch.setattr(settings, "PARTNER_API_ENABLED", True)
    enabled = await call_raw(app, "GET", "/partner/v1/x", headers=world.headers())

    assert disabled.body == enabled.body
    assert disabled.status == enabled.status == 404
    assert {k for k, _ in disabled.headers} == {k for k, _ in enabled.headers}


async def test_the_switch_comes_before_authentication(
    world: PartnerWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "PARTNER_API_ENABLED", False)

    response = await call_raw(app, "GET", "/partner/v1/me", headers=world.headers())

    assert response.status == 404
    assert world.store.finds == []  # no lookup happened


async def test_a_normal_http_client_gets_the_same_envelope(switch: bool) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as http:
        response = await http.get("/partner/v1/x")

    assert response.status_code == (401 if switch else 404)
    assert response.json() == (UNAUTHORIZED if switch else NOT_FOUND)
    assert response.headers["x-freejoy-surface"] == "partner"


@pytest.mark.parametrize("path", ["/partner/v1/x", "/partner/v1", "/partner/v1/"])
@pytest.mark.parametrize("with_origin", [False, True])
async def test_options_gets_no_cors_headers(
    switch: bool, path: str, with_origin: bool
) -> None:
    headers = (
        [
            ("Origin", "https://retailer.example"),
            ("Access-Control-Request-Method", "POST"),
            ("Access-Control-Request-Headers", "authorization"),
        ]
        if with_origin
        else []
    )

    response = await call_raw(app, "OPTIONS", path, headers=headers)

    assert response.status == (401 if switch else 404)
    _assert_partner_headers(response.headers)


async def test_the_main_app_has_no_middleware_that_could_wrap_the_partner_surface() -> (
    None
):
    """Middleware of the main app sits outside the surface and could add CORS headers or cookies to
    partner responses, where the surface cannot remove them. So the list is pinned: adding to it
    must be a decision that also deals with ``/partner/v1``."""
    assert [m.cls for m in app.user_middleware] == [RequestIdMiddleware]


async def test_an_outer_cors_middleware_would_reach_the_partner_responses() -> None:
    """Why the pin above exists: nothing inside the surface can undo what wraps it from outside."""
    wrapped = CORSMiddleware(app, allow_origins=["*"])

    response = await call_raw(
        wrapped,
        "GET",
        "/partner/v1/x",
        headers=[("Origin", "https://retailer.example")],
    )

    assert "access-control-allow-origin" in response.header_names()


async def test_the_real_body_limit_applies_only_while_the_surface_is_on(
    switch: bool, world: PartnerWorld
) -> None:
    keyed = world.headers()
    over = await call_raw(
        app,
        "POST",
        "/partner/v1/x",
        headers=[*keyed, ("Content-Length", str(MAX_BODY_BYTES + 1))],
    )
    within = await call_raw(
        app,
        "POST",
        "/partner/v1/x",
        headers=[*keyed, ("Content-Length", str(MAX_BODY_BYTES))],
    )

    # Switched off, every request gets the same 404, a large declared length included.
    assert over.status == (413 if switch else 404)
    assert over.json() == (
        {"success": False, "error": "Payload too large"} if switch else NOT_FOUND
    )
    assert within.status == 404
    _assert_partner_headers(over.headers)


async def test_an_oversize_declared_length_without_a_key_is_a_401_not_a_413(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "PARTNER_API_ENABLED", True)

    response = await call_raw(
        app,
        "POST",
        "/partner/v1/x",
        headers=[("Content-Length", str(MAX_BODY_BYTES + 1))],
    )

    assert response.status == 401


PARTNER_HEADER_NAMES = {
    "content-length",
    "content-type",
    "cache-control",
    "x-content-type-options",
    "x-freejoy-surface",
    "x-request-id",
}


@pytest.mark.parametrize("with_origin", [False, True])
async def test_a_partner_404_has_exactly_these_headers(
    world: PartnerWorld, monkeypatch: pytest.MonkeyPatch, with_origin: bool
) -> None:
    """A new header on a partner response (from outer middleware, say) must be a decision."""
    headers = [("Origin", "https://retailer.example")] if with_origin else []
    for enabled in (False, True):
        monkeypatch.setattr(settings, "PARTNER_API_ENABLED", enabled)

        response = await call_raw(
            app, "GET", "/partner/v1/x", headers=[*headers, *world.headers()]
        )

        assert response.status == 404
        assert response.header_names() == PARTNER_HEADER_NAMES


@pytest.mark.parametrize("with_origin", [False, True])
async def test_a_partner_401_has_exactly_these_headers(
    monkeypatch: pytest.MonkeyPatch, with_origin: bool
) -> None:
    monkeypatch.setattr(settings, "PARTNER_API_ENABLED", True)
    headers = [("Origin", "https://retailer.example")] if with_origin else []

    response = await call_raw(app, "GET", "/partner/v1/x", headers=headers)

    assert response.status == 401
    assert response.header_names() == PARTNER_HEADER_NAMES | {"www-authenticate"}
    assert response.header("www-authenticate") == "Bearer"


# ---------------------------------------------------------------- the surface on its own (throwaway sub-app)


class Typed(BaseModel):
    count: int


class Probe:
    """Records what a throwaway partner sub-app saw."""

    def __init__(self) -> None:
        self.handler_calls = 0
        self.receive_calls = 0


def _throwaway_surface(
    *, enabled: bool = True, limit: int = 10
) -> tuple[PartnerSurface, Probe]:
    api = create_partner_api()
    probe = Probe()

    @api.post("/echo")
    async def echo(request: Request) -> dict[str, int]:
        size = len(await request.body())
        probe.handler_calls += (
            1  # counted only once the whole body was read and accepted
        )
        return {"size": size}

    @api.get("/boom")
    async def boom() -> None:
        raise RuntimeError("secret internal detail")

    @api.post("/typed")
    async def typed(item: Typed) -> None:
        return None

    return PartnerSurface(
        api, enabled=lambda: enabled, authenticator=allow_all, max_body_bytes=limit
    ), probe


async def test_an_over_long_declared_length_is_refused_without_reading_the_body() -> (
    None
):
    surface, probe = _throwaway_surface()
    reads = Probe()

    response = await call_raw(
        surface,
        "POST",
        "/echo",
        headers=[("content-length", "11")],
        chunks=[b"x" * 11],
        on_receive=lambda: setattr(reads, "receive_calls", reads.receive_calls + 1),
    )

    assert response.status == 413
    assert response.json() == {"success": False, "error": "Payload too large"}
    _assert_partner_headers(response.headers)
    assert probe.handler_calls == 0
    assert reads.receive_calls == 0


async def test_a_missing_length_is_counted_and_reading_stops_at_the_limit() -> None:
    surface, probe = _throwaway_surface(limit=10)
    reads = Probe()
    chunks = [
        b"abcd"
    ] * 50  # chunked upload: no Content-Length at all, 200 bytes in all

    response = await call_raw(
        surface,
        "POST",
        "/echo",
        chunks=chunks,
        on_receive=lambda: setattr(reads, "receive_calls", reads.receive_calls + 1),
    )

    assert response.status == 413
    assert response.json() == {"success": False, "error": "Payload too large"}
    _assert_partner_headers(response.headers)
    assert probe.handler_calls == 0
    assert reads.receive_calls == 3  # 4 + 4 + 4 bytes crosses 10; nothing more is read


async def test_a_false_length_does_not_get_past_the_cap() -> None:
    surface, probe = _throwaway_surface(limit=10)

    response = await call_raw(
        surface,
        "POST",
        "/echo",
        headers=[("content-length", "5")],  # claims 5 bytes, sends 20
        chunks=[b"x" * 10, b"y" * 10],
    )

    assert response.status == 413
    assert probe.handler_calls == 0


@pytest.mark.parametrize("declared", ["abc", "²", "-1", "1.5", ""])
async def test_a_malformed_length_is_ignored_and_the_stream_is_counted(
    declared: str,
) -> None:
    surface, _ = _throwaway_surface(limit=10)

    response = await call_raw(
        surface,
        "POST",
        "/echo",
        headers=[("content-length", declared)],
        chunks=[b"x" * 50],
    )

    assert response.status == 413


async def test_an_absurdly_long_declared_length_is_refused_not_a_crash() -> None:
    surface, probe = _throwaway_surface(limit=10)

    response = await call_raw(
        surface, "POST", "/echo", headers=[("content-length", "9" * 5000)]
    )

    assert response.status == 413
    assert response.error is None
    assert probe.handler_calls == 0


async def test_a_body_exactly_at_the_limit_is_served() -> None:
    surface, probe = _throwaway_surface(limit=10)

    response = await call_raw(
        surface,
        "POST",
        "/echo",
        headers=[("content-length", "10")],
        chunks=[b"x" * 5, b"y" * 5],
    )

    assert response.status == 200
    assert response.json() == {"size": 10}
    assert probe.handler_calls == 1
    _assert_partner_headers(response.headers)


async def test_the_switch_decides_between_the_handler_and_the_404() -> None:
    off, off_probe = _throwaway_surface(enabled=False)
    on, on_probe = _throwaway_surface(enabled=True)

    disabled = await call_raw(off, "POST", "/echo", chunks=[b"hi"])
    enabled = await call_raw(on, "POST", "/echo", chunks=[b"hi"])
    unknown = await call_raw(on, "POST", "/unknown", chunks=[b"hi"])

    assert (disabled.status, enabled.status) == (404, 200)
    assert (off_probe.handler_calls, on_probe.handler_calls) == (0, 1)
    assert disabled.body == unknown.body
    _assert_partner_headers(disabled.headers)


async def test_a_500_carries_the_headers_and_leaks_nothing() -> None:
    surface, _ = _throwaway_surface()

    response = await call_raw(surface, "GET", "/boom")

    assert response.status == 500
    assert response.json() == {"success": False, "error": "Internal server error"}
    assert b"secret internal detail" not in response.body
    _assert_partner_headers(response.headers)


async def test_a_failing_request_is_logged_once() -> None:
    surface, _ = _throwaway_surface()

    with error_logs() as records:
        response = await call_raw(surface, "GET", "/boom")

    assert response.status == 500
    assert response.error is None  # not re-raised after the sub-app answered
    assert [r.getMessage() for r in records] == ["unhandled exception"]


async def test_validation_errors_do_not_echo_the_input() -> None:
    surface, _ = _throwaway_surface(limit=1000)

    response = await call_raw(
        surface,
        "POST",
        "/typed",
        headers=[("content-type", "application/json")],
        chunks=[b'{"count": "do-not-echo-me"}'],
    )

    assert response.status == 422
    assert response.json() == {"success": False, "error": "Invalid request"}
    _assert_partner_headers(response.headers)


class LeakyApp:
    """An inner app that tries to set a cookie, CORS headers and its own caching."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"set-cookie", b"a=1"),
                    (b"Set-Cookie", b"b=2"),
                    (b"access-control-allow-origin", b"*"),
                    (b"Access-Control-Allow-Credentials", b"true"),
                    (b"cache-control", b"public, max-age=600"),
                    (b"x-freejoy-surface", b"other"),
                    (b"x-keep-me", b"yes"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": b"ok"})


async def test_the_surface_strips_cookies_and_cors_headers_an_inner_app_sets() -> None:
    surface = PartnerSurface(LeakyApp(), enabled=lambda: True, authenticator=allow_all)

    response = await call_raw(surface, "GET", "/anything")

    _assert_partner_headers(response.headers)
    assert response.header("x-keep-me") == "yes"
    assert [v for k, v in response.headers if k == "cache-control"] == [
        "no-store, private"
    ]
    assert [v for k, v in response.headers if k == "x-freejoy-surface"] == ["partner"]


class HeaderlessApp:
    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await send({"type": "http.response.start", "status": 204})
        await send({"type": "http.response.body", "body": b""})


async def test_a_response_started_without_a_headers_list_still_gets_the_headers() -> (
    None
):
    surface = PartnerSurface(
        HeaderlessApp(), enabled=lambda: True, authenticator=allow_all
    )

    response = await call_raw(surface, "GET", "/x")

    assert response.status == 204
    _assert_partner_headers(response.headers)


async def test_a_websocket_is_refused() -> None:
    surface = PartnerSurface(LeakyApp(), enabled=lambda: True, authenticator=allow_all)
    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "websocket.connect"}

    async def send(message: Message) -> None:
        sent.append(message)

    await surface(
        {"type": "websocket", "path": "/x", "headers": [], "subprotocols": []},
        receive,
        send,
    )

    assert [m["type"] for m in sent] == ["websocket.close"]


def test_the_surface_exposes_its_routes_for_the_confinement_walk() -> None:
    surface, _ = _throwaway_surface()

    assert {c.path for c in iter_route_contexts(surface.routes)} == {
        "/me",
        "/echo",
        "/boom",
        "/typed",
    }
    assert (
        PartnerSurface(LeakyApp(), enabled=lambda: True, authenticator=allow_all).routes
        == []
    )
