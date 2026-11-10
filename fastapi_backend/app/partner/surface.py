"""Everything that wraps the partner sub-app from the outside (ADR-013).

``PartnerSurface`` is a pure ASGI wrapper placed *around* the FastAPI sub-app, not added to it as
middleware, because a FastAPI app's own error middleware sits outside its user middleware: a
500 produced there would otherwise skip the headers below. Being outermost it covers every
response the sub-app can produce, including 404s and 500s, and the ones it writes itself.

For every partner response it:

* answers 404 for any request while the surface is switched off (``PARTNER_API_ENABLED``), whatever
  the headers say, with the same body as an unknown path while it is on;
* owns the 413 for an over-long body: refused by the declared ``Content-Length``, and again by
  counting the bytes actually received (a missing or false length is never trusted, and the body is
  never buffered by the surface). Application code cannot turn it into something else, see
  ``_Exchange``;
* sets ``Cache-Control: no-store, private``, ``X-Content-Type-Options: nosniff`` and
  ``X-Freejoy-Surface: partner``, and removes ``Set-Cookie`` and every ``Access-Control-*`` header.

Middleware of the main app wraps this surface from outside and cannot be undone from in here, so
a test (``tests/api/partner/test_partner_surface.py``) pins the main app's middleware list.
"""

import asyncio
import logging
from collections.abc import Callable

from starlette.datastructures import Headers
from starlette.routing import BaseRoute
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from starlette.websockets import WebSocketClose

from app.core.errors import error_response

logger = logging.getLogger("yourapp.partner")

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
# Never swallowed, whatever the request did.
_PASS_THROUGH = (asyncio.CancelledError, KeyboardInterrupt, SystemExit, GeneratorExit)


class _BodyTooLarge(BaseException):
    """Raised from ``receive`` once the body crosses the cap.

    A ``BaseException`` on purpose: application code and the sub-app's error middleware catch
    ``Exception``, so neither can swallow it, log it or turn it into a 500 or a 200. The surface
    answers the 413 itself (``_Exchange``).
    """


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


class _Exchange:
    """One request through the surface: counts the body and decides who answers.

    Before any response has started, crossing the cap raises ``_BodyTooLarge`` from ``receive``.
    Whatever the application then does (let it escape, swallow it, answer 200, fail otherwise), the
    client gets the 413: the first message the application sends is replaced by it and the rest are
    dropped. Once a response has started it cannot be replaced, so ``receive`` reports a disconnect
    instead of raising (a streaming response that listens for one must not fail on a body it never read).
    """

    def __init__(self, scope: Scope, receive: Receive, send: Send, limit: int) -> None:
        self._scope = scope
        self._receive = receive
        self._send = send
        self._limit = limit
        self._received = 0
        self.overflowed = False
        self.replaced = False
        self.started = False
        self.status = 0

    async def receive(self) -> Message:
        if self.overflowed:
            return {"type": "http.disconnect"}
        message = await self._receive()
        if message["type"] == "http.request":
            self._received += len(message.get("body", b""))
            if self._received > self._limit:
                self.overflowed = True
                if self.started:
                    return {"type": "http.disconnect"}
                raise _BodyTooLarge
        return message

    async def send(self, message: Message) -> None:
        if self.replaced:
            return  # the rest of a response that was replaced by the 413
        if self.overflowed and not self.started:
            await self.refuse()  # replaces the application's first message
            return
        if message["type"] == "http.response.start":
            self.started = True
            self.status = message["status"]
        await self._send(message)

    async def refuse(self) -> None:
        self.started = True
        self.replaced = True
        response = error_response(413, PAYLOAD_TOO_LARGE_MESSAGE)
        await response(self._scope, self._receive, self._send)


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
        # The switch comes first: a switched-off surface answers every request the same way.
        if not self._enabled():
            await error_response(404, NOT_FOUND_MESSAGE)(scope, receive, send)
            return
        if _declares_too_much(scope, self._max_body_bytes):
            response = error_response(413, PAYLOAD_TOO_LARGE_MESSAGE)
            await response(scope, receive, send)
            return
        exchange = _Exchange(scope, receive, send, self._max_body_bytes)
        try:
            await self.app(scope, exchange.receive, exchange.send)
        except BaseException as exc:
            if isinstance(exc, _PASS_THROUGH):
                raise
            if exchange.overflowed and not exchange.started:
                await (
                    exchange.refuse()
                )  # whatever the app did with the exception, the answer is 413
                return
            if exchange.replaced:
                # The 413 is already sent. The private exception is just that refusal travelling up;
                # anything else is what the app did after it: kept visible, though not an error.
                if not isinstance(exc, _BodyTooLarge):
                    logger.warning(
                        "partner request failed after its body was refused: %s",
                        type(exc).__name__,
                    )
                return
            if isinstance(exc, Exception) and exchange.status == 500:
                # The sub-app already answered 500 and logged it; re-raising would log it again.
                return
            raise
        if exchange.overflowed and not exchange.started:
            await (
                exchange.refuse()
            )  # the application read too much and then sent nothing
