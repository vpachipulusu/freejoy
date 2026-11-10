"""Raw ASGI calls for the partner surface tests.

An HTTP client (httpx) normalises ``..`` and ``//`` in a URL before it sends anything, so it
cannot send the adversarial paths these tests are about. ``call_raw`` builds the ASGI scope by
hand, the way a server such as uvicorn does (``path`` percent-decoded, ``raw_path`` as it came
off the wire), and calls the application directly, so the path reaches the application exactly
as written. ``Recorder`` proves it: it sits in front of the application and notes the ``path``
that arrived.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

SURFACE_HEADERS = {
    "cache-control": "no-store, private",
    "x-content-type-options": "nosniff",
    "x-freejoy-surface": "partner",
}


@dataclass(frozen=True)
class RawResponse:
    status: int
    headers: list[tuple[str, str]]
    body: bytes
    error: Exception | None = None

    def header_names(self) -> set[str]:
        return {name for name, _ in self.headers}

    def header(self, name: str) -> str | None:
        return next((value for key, value in self.headers if key == name), None)

    def json(self) -> Any:
        import json

        return json.loads(self.body)


@dataclass
class Recorder:
    """An ASGI wrapper that notes the ``path`` and ``raw_path`` that reach the application."""

    app: ASGIApp
    paths: list[str] = field(default_factory=list)
    raw_paths: list[bytes] = field(default_factory=list)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        self.paths.append(scope["path"])
        self.raw_paths.append(scope["raw_path"])
        await self.app(scope, receive, send)


def build_scope(
    method: str,
    path: str,
    raw_path: str | None = None,
    headers: Iterable[tuple[str, str]] = (),
) -> Scope:
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": (raw_path if raw_path is not None else path).encode(),
        "query_string": b"",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers],
        "server": ("testserver", 80),
        "client": ("203.0.113.9", 4242),
    }


async def call_raw(
    application: ASGIApp,
    method: str,
    path: str,
    *,
    raw_path: str | None = None,
    headers: Iterable[tuple[str, str]] = (),
    chunks: Iterable[bytes] = (b"",),
    on_receive: Callable[[], None] | None = None,
) -> RawResponse:
    """Call ``application`` with a hand-built scope; ``chunks`` are the body, sent in order."""
    pending = list(chunks)
    started: list[Message] = []
    body = bytearray()

    async def receive() -> Message:
        if on_receive is not None:
            on_receive()
        if not pending:
            return {"type": "http.disconnect"}
        chunk = pending.pop(0)
        return {"type": "http.request", "body": chunk, "more_body": bool(pending)}

    async def send(message: Message) -> None:
        if message["type"] == "http.response.start":
            started.append(message)
        elif message["type"] == "http.response.body":
            body.extend(message.get("body", b""))

    error: Exception | None = None
    try:
        await application(build_scope(method, path, raw_path, headers), receive, send)
    except Exception as exc:  # noqa: BLE001 - a 500 is re-raised after it is sent; keep the response
        error = exc
    start = started[0]
    pairs = [(k.decode().lower(), v.decode()) for k, v in start["headers"]]
    return RawResponse(start["status"], pairs, bytes(body), error)
