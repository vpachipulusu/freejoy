"""The surface owns the 413 (ADR-013): application code cannot turn an over-long body into anything else.

Each test uses a throwaway sub-app and a small limit. The body is sent chunked (no ``Content-Length``),
so only the byte count on the stream can catch it. The private exception the surface raises from
``receive`` is a ``BaseException``, so the application's ``except Exception`` (and the sub-app's error
middleware) can neither swallow it nor log it.
"""

import asyncio
import logging

import pytest
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import StreamingResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.partner.app import create_partner_api
from app.partner.surface import PartnerSurface
from tests.api.partner.support import SURFACE_HEADERS, call_raw, error_logs

LIMIT = 10
TOO_LARGE = {"success": False, "error": "Payload too large"}
OVER = [b"abcd"] * 10  # 40 bytes in chunks, no Content-Length


def _assert_refused(response, records) -> None:  # type: ignore[no-untyped-def]
    assert response.error is None
    assert response.status == 413
    assert response.json() == TOO_LARGE
    for name, value in SURFACE_HEADERS.items():
        assert dict(response.headers)[name] == value
    assert records == []


class DrainBody:
    """A pure ASGI middleware that reads the whole body before the application runs."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        while (await receive()).get("more_body"):
            pass

        async def empty() -> Message:
            return {"type": "http.request", "body": b"", "more_body": False}

        await self.app(scope, empty, send)


def _surface(api) -> PartnerSurface:  # type: ignore[no-untyped-def]
    return PartnerSurface(api, enabled=lambda: True, max_body_bytes=LIMIT)


async def test_a_handler_that_swallows_every_exception_still_gets_a_413() -> None:
    api = create_partner_api()

    @api.post("/swallow")
    async def swallow(request: Request) -> dict[str, bool]:
        try:
            await request.body()
        except Exception:  # noqa: BLE001, S110 - the point of the test
            pass
        return {"ok": True}

    with error_logs() as records:
        response = await call_raw(_surface(api), "POST", "/swallow", chunks=OVER)

    _assert_refused(response, records)


async def test_a_pure_asgi_middleware_that_reads_the_body_gets_a_413_not_a_500() -> (
    None
):
    api = create_partner_api()
    api.add_middleware(DrainBody)

    @api.post("/echo")
    async def echo() -> dict[str, bool]:
        return {"ok": True}

    with error_logs() as records:
        response = await call_raw(_surface(api), "POST", "/echo", chunks=OVER)

    _assert_refused(response, records)


async def test_a_base_http_middleware_that_reads_the_body_gets_a_413_not_a_500() -> (
    None
):
    api = create_partner_api()

    class Reader(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
            await request.body()
            return await call_next(request)

    api.add_middleware(Reader)

    @api.post("/echo")
    async def echo() -> dict[str, bool]:
        return {"ok": True}

    with error_logs() as records:
        response = await call_raw(_surface(api), "POST", "/echo", chunks=OVER)

    _assert_refused(response, records)


async def test_a_streaming_response_that_never_reads_the_body_does_not_fail(
    caplog: pytest.LogCaptureFixture,
) -> None:
    api = create_partner_api()

    @api.post("/stream")
    async def stream() -> StreamingResponse:
        async def pieces():  # type: ignore[no-untyped-def]
            yield b"a"
            yield b"b"

        return StreamingResponse(pieces())

    with error_logs() as records:
        response = await call_raw(_surface(api), "POST", "/stream", chunks=OVER)

    assert (
        response.error is None
    )  # no RuntimeError out of the response's disconnect listener
    assert records == []
    assert (
        response.status == 413
    )  # the listener hit the cap before the response could start
    assert caplog.records == []
    for name, value in SURFACE_HEADERS.items():
        assert dict(response.headers)[name] == value


async def test_the_exception_is_not_an_exception() -> None:
    """The reason the swallowing handler above fails to swallow it."""
    from app.partner.surface import _BodyTooLarge

    assert not issubclass(_BodyTooLarge, Exception)


class ReadsThenSwallows:
    """Reads past the limit, catches whatever comes, and sends nothing at all."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            while (await receive()).get("more_body"):
                pass
        except BaseException:  # noqa: BLE001 - the point of the test
            return


async def test_an_app_that_swallows_the_overflow_and_sends_nothing_still_gets_a_413() -> (
    None
):
    with error_logs() as records:
        response = await call_raw(
            PartnerSurface(
                ReadsThenSwallows(), enabled=lambda: True, max_body_bytes=LIMIT
            ),
            "POST",
            "/x",
            chunks=OVER,
        )

    _assert_refused(response, records)


class StartsThenReads:
    """Starts a 200 response, then keeps reading the body: the response cannot be replaced."""

    def __init__(self) -> None:
        self.seen: list[str] = []

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        while True:
            message = await receive()
            self.seen.append(message["type"])
            if message["type"] == "http.disconnect":
                break
        await send({"type": "http.response.body", "body": b"done"})


async def test_after_the_response_has_started_receive_reports_a_disconnect() -> None:
    inner = StartsThenReads()

    response = await call_raw(
        PartnerSurface(inner, enabled=lambda: True, max_body_bytes=LIMIT),
        "POST",
        "/x",
        chunks=OVER,
    )

    assert response.error is None
    assert response.status == 200  # the started response was left alone
    assert response.body == b"done"
    assert inner.seen[-1] == "http.disconnect"
    assert "http.request" in inner.seen


class RaisesAfterReplacement:
    """Swallows the overflow, answers 200 (replaced by the 413), then fails."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            while (await receive()).get("more_body"):
                pass
        except BaseException:  # noqa: BLE001, S110 - the point of the test
            pass
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})
        raise ValueError("after the 413")


async def test_an_error_after_the_413_was_sent_is_a_warning_not_a_second_answer(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="yourapp.partner"):
        response = await call_raw(
            PartnerSurface(
                RaisesAfterReplacement(), enabled=lambda: True, max_body_bytes=LIMIT
            ),
            "POST",
            "/x",
            chunks=OVER,
        )

    assert response.status == 413
    assert response.json() == TOO_LARGE
    assert response.error is None
    assert [r.getMessage() for r in caplog.records if r.name == "yourapp.partner"] == [
        "partner request failed after its body was refused: ValueError"
    ]


async def test_cancellation_is_never_swallowed() -> None:
    class Cancelled:
        async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
            raise asyncio.CancelledError

    surface = PartnerSurface(Cancelled(), enabled=lambda: True)

    with pytest.raises(asyncio.CancelledError):
        await surface(
            {"type": "http", "path": "/x", "headers": []},
            _never,
            _discard,
        )


async def test_an_error_after_a_started_response_is_not_hidden() -> None:
    class StartsThenFails:
        async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
            await send({"type": "http.response.start", "status": 200, "headers": []})
            raise ValueError("mid-stream")

    response = await call_raw(
        PartnerSurface(StartsThenFails(), enabled=lambda: True), "GET", "/x"
    )

    assert isinstance(response.error, ValueError)


async def _never() -> Message:
    return {"type": "http.disconnect"}


async def _discard(message: Message) -> None:
    return None


async def test_reading_again_after_the_overflow_reports_a_disconnect() -> None:
    seen: list[str] = []

    class ReadsAgain:
        async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
            try:
                while (await receive()).get("more_body"):
                    pass
            except BaseException:  # noqa: BLE001 - the point of the test
                seen.append((await receive())["type"])

    response = await call_raw(
        PartnerSurface(ReadsAgain(), enabled=lambda: True, max_body_bytes=LIMIT),
        "POST",
        "/x",
        chunks=OVER,
    )

    assert seen == ["http.disconnect"]
    assert response.status == 413
