"""Everything that wraps the partner sub-app from the outside (ADR-013).

``PartnerSurface`` is a pure ASGI wrapper placed *around* the FastAPI sub-app, not added to it as
middleware, because a FastAPI app's own error middleware sits outside its user middleware: a
500 produced there would otherwise skip the headers below. Being outermost it covers every
response the sub-app can produce, including 404s and 500s, and the ones it writes itself.

For every partner response it:

* answers 404 for any path while the surface is switched off (``PARTNER_API_ENABLED``), with the
  same body as an unknown path while it is on;
* refuses an over-long body, by the declared ``Content-Length`` and again by counting the bytes
  actually received (a missing or false length is never trusted, and the body is never buffered);
* sets ``Cache-Control: no-store, private``, ``X-Content-Type-Options: nosniff`` and
  ``X-Freejoy-Surface: partner``, and removes ``Set-Cookie`` and every ``Access-Control-*`` header.

Middleware of the main app wraps this surface from outside and cannot be undone from in here, so
a test (``tests/api/partner/test_partner_surface.py``) pins the main app's middleware list.
"""

from collections.abc import Callable

from fastapi import HTTPException
from starlette.datastructures import Headers
from starlette.routing import BaseRoute
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from starlette.websockets import WebSocketClose

from app.core.errors import error_response

PARTNER_PREFIX = "/partner/v1"
SURFACE_HEADER = "X-Freejoy-Surface"
SURFACE_NAME = "partner"
CACHE_CONTROL = "no-store, private"
# Partner submissions are small JSON drafts; raise this deliberately when a real endpoint needs more.
MAX_BODY_BYTES = 262_144
NOT_FOUND_MESSAGE = (
    "Not Found"  # Starlette's own text for an unknown path, so both 404s match.
)
PAYLOAD_TOO_LARGE_MESSAGE = "Payload too large"

EnabledCheck = Callable[[], bool]


# Names (lower case, as bytes) the surface sets itself, so anything the inner app set is replaced.
SURFACE_RESPONSE_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"cache-control", CACHE_CONTROL.encode()),
    (b"x-content-type-options", b"nosniff"),
    (SURFACE_HEADER.lower().encode(), SURFACE_NAME.encode()),
)


def _is_dropped(name: bytes) -> bool:
    """A header the inner app must not send: cookies, CORS, or one the surface sets itself."""
    lowered = name.lower()
    return (
        lowered == b"set-cookie"
        or lowered.startswith(b"access-control-")
        or any(lowered == owned for owned, _ in SURFACE_RESPONSE_HEADERS)
    )


def _with_surface_headers(send: Send) -> Send:
    async def send_with_surface_headers(message: Message) -> None:
        if message["type"] == "http.response.start":
            # Compared in any letter case: ASGI says lower case, but nothing here relies on it.
            kept = [
                (name, value)
                for name, value in message.get("headers", [])
                if not _is_dropped(name)
            ]
            message["headers"] = [*kept, *SURFACE_RESPONSE_HEADERS]
        await send(message)

    return send_with_surface_headers


def _declares_too_much(scope: Scope, limit: int) -> bool:
    declared = Headers(scope=scope).getlist("content-length")
    # A digit string past 18 characters is over any cap, and int() refuses one past 4300.
    return any(
        v.isascii() and v.isdigit() and (len(v) > 18 or int(v) > limit)
        for v in declared
    )


def _counting(receive: Receive, limit: int) -> Receive:
    received = 0

    async def counting_receive() -> Message:
        nonlocal received
        message = await receive()
        if message["type"] == "http.request":
            received += len(message.get("body", b""))
            if received > limit:
                # Stops reading at once; the sub-app's handler turns this into the 413 envelope.
                raise HTTPException(413, PAYLOAD_TOO_LARGE_MESSAGE)
        return message

    return counting_receive


class PartnerSurface:
    def __init__(
        self,
        app: ASGIApp,
        *,
        enabled: EnabledCheck,
        max_body_bytes: int = MAX_BODY_BYTES,
    ) -> None:
        self.app = app
        self._enabled = enabled
        self._max_body_bytes = max_body_bytes

    @property
    def routes(self) -> list[BaseRoute]:
        """The sub-app's routes, so a mount of this wrapper can still be walked."""
        return getattr(self.app, "routes", [])

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await WebSocketClose()(scope, receive, send)  # the surface speaks HTTP only
            return
        send = _with_surface_headers(send)
        if _declares_too_much(scope, self._max_body_bytes):
            response = error_response(413, PAYLOAD_TOO_LARGE_MESSAGE)
            await response(scope, receive, send)
            return
        if not self._enabled():
            await error_response(404, NOT_FOUND_MESSAGE)(scope, receive, send)
            return
        await self.app(scope, _counting(receive, self._max_body_bytes), send)
