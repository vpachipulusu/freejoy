"""``HttpxAwinClient`` over ``httpx.MockTransport``: no socket, no sleeping, a made-up token.

The token is a fake. The point of most tests here is where it may appear (the Authorization header
and nowhere else) and how every failure turns into a reason code that carries no response text.
"""

import asyncio
import json
import logging
import ssl
from collections.abc import Callable
from datetime import date
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from app.core.awin_client import (
    BASE_URL,
    MAX_ATTEMPTS,
    MAX_RESPONSE_BYTES,
    MAX_RETRY_AFTER_SECONDS,
    AwinError,
    AwinReason,
    HttpxAwinClient,
    PromotionFilters,
)
from tests.fakes_awin import FakeTime

TOKEN = "fake-awin-token-0123456789abcdef"
PUBLISHER_ID = 424242
ITEM = {"promotionId": 1}
Handler = Callable[[httpx.Request], httpx.Response]


class CountingLimiter:
    def __init__(self) -> None:
        self.acquired = 0

    async def acquire(self) -> None:
        self.acquired += 1


class Recorder:
    """A handler that answers from a script (the last answer repeats) and keeps the requests."""

    def __init__(self, *answers: httpx.Response | Exception) -> None:
        self.answers = list(answers)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer


def ok(payload: Any) -> httpx.Response:
    return httpx.Response(200, json=payload)


def build(handler: Handler) -> tuple[HttpxAwinClient, CountingLimiter, FakeTime]:
    fake = FakeTime()
    limiter = CountingLimiter()
    client = HttpxAwinClient(
        PUBLISHER_ID,
        SecretStr(TOKEN),
        transport=httpx.MockTransport(handler),
        limiter=limiter,
        sleep=fake.sleep,
    )
    return client, limiter, fake


async def fetch(client: HttpxAwinClient, **kwargs: Any) -> Any:
    return await client.fetch_promotions(
        kwargs.pop("filters", PromotionFilters()),
        page=kwargs.pop("page", 1),
        page_size=kwargs.pop("page_size", 200),
    )


async def failure(client: HttpxAwinClient) -> AwinError:
    with pytest.raises(AwinError) as caught:
        await fetch(client)
    return caught.value


# --- the request ---------------------------------------------------------------------------


async def test_the_request_is_one_post_to_the_fixed_host_with_the_documented_body() -> (
    None
):
    recorder = Recorder(ok([ITEM]))
    client, _, _ = build(recorder)

    await fetch(
        client,
        filters=PromotionFilters(updated_since=date(2026, 11, 1)),
        page=3,
        page_size=150,
    )

    request = recorder.requests[0]
    assert request.method == "POST"
    assert str(request.url) == f"{BASE_URL}/publisher/{PUBLISHER_ID}/promotions"
    assert json.loads(request.content) == {
        "filters": {
            "membership": "joined",
            "type": "all",
            "status": "active",
            "regionCodes": ["GB"],
            "updatedSince": "2026-11-01",
        },
        "pagination": {"page": 3, "pageSize": 150},
    }
    assert request.headers["content-type"] == "application/json"


async def test_updated_since_is_left_out_when_not_asked_for() -> None:
    recorder = Recorder(ok([]))
    client, _, _ = build(recorder)

    await fetch(client)

    assert "updatedSince" not in json.loads(recorder.requests[0].content)["filters"]


async def test_the_token_is_sent_in_the_authorization_header_only() -> None:
    recorder = Recorder(ok([ITEM]))
    client, _, _ = build(recorder)

    await fetch(client)

    request = recorder.requests[0]
    assert request.headers["authorization"] == f"Bearer {TOKEN}"
    assert request.url.query == b""
    assert TOKEN not in str(request.url)
    assert "accesstoken" not in str(request.url).lower()
    assert TOKEN.encode() not in request.content


@pytest.mark.parametrize(("page", "size"), [(0, 200), (-1, 200), (1, 9), (1, 201)])
async def test_a_page_or_size_outside_the_documented_range_is_refused_before_any_request(
    page: int, size: int
) -> None:
    recorder = Recorder(ok([]))
    client, limiter, _ = build(recorder)

    with pytest.raises(ValueError, match="page"):
        await fetch(client, page=page, page_size=size)

    assert recorder.requests == [] and limiter.acquired == 0


@pytest.mark.parametrize("publisher_id", [0, -3])
def test_a_publisher_id_that_is_not_positive_is_refused(publisher_id: int) -> None:
    with pytest.raises(ValueError, match="publisher"):
        HttpxAwinClient(publisher_id, SecretStr(TOKEN))


def test_the_connection_settings_are_the_safe_ones() -> None:
    client = HttpxAwinClient(PUBLISHER_ID, SecretStr(TOKEN))

    http = client.http_client
    assert http.follow_redirects is False
    assert http.trust_env is False
    assert http.timeout.connect and http.timeout.read and http.timeout.write
    assert http.timeout.pool
    assert BASE_URL == "https://api.awin.com"


# --- success shapes ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("payload", "items", "pages"),
    [
        ([ITEM, ITEM], 2, None),
        ([], 0, None),
        ({"data": [ITEM]}, 1, None),
        ({"data": [], "pagination": {"totalPages": 0}}, 0, 0),
        ({"data": [ITEM], "pagination": {"page": 1, "totalPages": 4}}, 1, 4),
        ({"data": [ITEM], "pagination": {"totalPages": "four"}}, 1, None),
        ({"data": [ITEM], "pagination": {"totalPages": True}}, 1, None),
        ({"data": [ITEM], "pagination": {"totalPages": -2}}, 1, None),
        ({"data": [ITEM], "pagination": "nope"}, 1, None),
        ({"data": [ITEM], "extra": {"anything": 1}}, 1, None),
    ],
)
async def test_each_plausible_envelope_is_accepted(
    payload: Any, items: int, pages: int | None
) -> None:
    client, _, _ = build(Recorder(ok(payload)))

    page = await fetch(client)

    assert len(page.items) == items
    assert page.total_pages == pages


@pytest.mark.parametrize(
    "payload",
    [{}, {"promotions": [ITEM]}, {"data": "x"}, {"data": {"a": 1}}, "text", 7, "null"],
)
async def test_an_envelope_it_does_not_recognise_fails_closed(payload: Any) -> None:
    client, _, _ = build(Recorder(ok(payload)))

    error = await failure(client)

    assert error.reason is AwinReason.UNRECOGNISED_RESPONSE


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(b"not json", id="text"),
        pytest.param(b"", id="empty"),
        pytest.param(b"\xff\xfe\x00", id="not-utf8"),
        pytest.param(b"[" * 100_000, id="too-deep"),
        pytest.param(b'{"data": [1,', id="truncated"),
    ],
)
async def test_a_body_that_is_not_json_fails_closed(body: bytes) -> None:
    client, _, _ = build(Recorder(httpx.Response(200, content=body)))

    error = await failure(client)

    assert error.reason is AwinReason.INVALID_JSON


async def test_a_body_over_the_cap_is_refused() -> None:
    big = b"[" + b" " * (MAX_RESPONSE_BYTES + 1) + b"]"
    client, _, _ = build(Recorder(httpx.Response(200, content=big)))

    error = await failure(client)

    assert error.reason is AwinReason.RESPONSE_TOO_LARGE


async def test_a_declared_length_over_the_cap_is_refused_up_front() -> None:
    response = httpx.Response(
        200, content=b"[]", headers={"content-length": str(MAX_RESPONSE_BYTES + 1)}
    )
    client, _, _ = build(Recorder(response))

    error = await failure(client)

    assert error.reason is AwinReason.RESPONSE_TOO_LARGE


# --- status codes, retries and the limiter -------------------------------------------------


@pytest.mark.parametrize("status", [401, 403])
async def test_an_authentication_failure_stops_at_once(status: int) -> None:
    recorder = Recorder(httpx.Response(status, text=f"bad token {TOKEN}"))
    client, _, fake = build(recorder)

    error = await failure(client)

    assert error.reason is AwinReason.AUTH_FAILED
    assert len(recorder.requests) == 1 and fake.sleeps == []
    assert TOKEN not in repr(error) and TOKEN not in str(error)


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (400, AwinReason.CLIENT_ERROR),
        (404, AwinReason.CLIENT_ERROR),
        (422, AwinReason.CLIENT_ERROR),
        (302, AwinReason.REDIRECTED),
        (301, AwinReason.REDIRECTED),
        (204, AwinReason.UNEXPECTED_STATUS),
    ],
)
async def test_other_statuses_are_one_request_and_a_reason_code(
    status: int, reason: AwinReason
) -> None:
    headers = {"location": "https://evil.example/steal"} if status in (301, 302) else {}
    recorder = Recorder(httpx.Response(status, headers=headers))
    client, _, fake = build(recorder)

    error = await failure(client)

    assert error.reason is reason
    assert len(recorder.requests) == 1 and fake.sleeps == []


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (429, AwinReason.RATE_LIMITED),
        (500, AwinReason.SERVER_ERROR),
        (503, AwinReason.SERVER_ERROR),
    ],
)
async def test_a_throttle_or_server_error_is_retried_a_bounded_number_of_times(
    status: int, reason: AwinReason
) -> None:
    recorder = Recorder(httpx.Response(status))
    client, limiter, fake = build(recorder)

    error = await failure(client)

    assert error.reason is reason
    assert len(recorder.requests) == MAX_ATTEMPTS
    assert (
        limiter.acquired == MAX_ATTEMPTS
    )  # every attempt counts toward the 20 a minute
    assert len(fake.sleeps) == MAX_ATTEMPTS - 1
    assert fake.sleeps == sorted(fake.sleeps)  # backs off, never faster


async def test_a_retry_that_succeeds_returns_the_page_and_every_attempt_carried_the_header() -> (
    None
):
    recorder = Recorder(httpx.Response(503), httpx.Response(429), ok([ITEM]))
    client, _, _ = build(recorder)

    page = await fetch(client)

    assert len(page.items) == 1 and len(recorder.requests) == 3
    for request in recorder.requests:
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        assert request.url.query == b""


async def test_retry_after_is_honoured_and_capped() -> None:
    recorder = Recorder(
        httpx.Response(429, headers={"retry-after": "7"}),
        httpx.Response(429, headers={"retry-after": "86400"}),
        httpx.Response(429, headers={"retry-after": "soon"}),
        ok([ITEM]),
    )
    client, _, fake = build(recorder)

    await fetch(client)

    assert fake.sleeps[0] >= 7
    assert fake.sleeps[1] == MAX_RETRY_AFTER_SECONDS
    assert fake.sleeps[2] < MAX_RETRY_AFTER_SECONDS  # an unreadable value is ignored


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (httpx.ReadTimeout("slow"), AwinReason.TIMEOUT),
        (httpx.ConnectTimeout("slow"), AwinReason.TIMEOUT),
        (httpx.ConnectError("refused"), AwinReason.CONNECTION_ERROR),
        (httpx.RemoteProtocolError("closed"), AwinReason.CONNECTION_ERROR),
    ],
)
async def test_timeouts_and_connection_failures_are_retried_then_reported(
    error: Exception, reason: AwinReason
) -> None:
    recorder = Recorder(error)
    client, _, _ = build(recorder)

    result = await failure(client)

    assert result.reason is reason
    assert len(recorder.requests) == MAX_ATTEMPTS


async def test_a_tls_failure_is_not_retried() -> None:
    error = httpx.ConnectError("tls")
    error.__cause__ = ssl.SSLCertVerificationError("bad certificate")
    recorder = Recorder(error)
    client, _, _ = build(recorder)

    result = await failure(client)

    assert result.reason is AwinReason.TLS_ERROR
    assert len(recorder.requests) == 1


async def test_a_call_that_outlives_its_deadline_is_a_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.core.awin_client as module

    monkeypatch.setattr(module, "ATTEMPT_DEADLINE_SECONDS", 0.01)

    class Hang(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            await asyncio.sleep(5)
            return httpx.Response(200, json=[])

    fake = FakeTime()
    client = HttpxAwinClient(
        PUBLISHER_ID,
        SecretStr(TOKEN),
        transport=Hang(),
        limiter=CountingLimiter(),
        sleep=fake.sleep,
    )

    error = await failure(client)

    assert error.reason is AwinReason.TIMEOUT


# --- the token stays out of everything -----------------------------------------------------


SCENARIOS: list[Callable[[], httpx.Response | Exception]] = [
    lambda: httpx.Response(401, text=f"Bearer {TOKEN}"),
    lambda: httpx.Response(500, text=f"echo {TOKEN}"),
    lambda: httpx.Response(200, content=TOKEN.encode()),
    lambda: httpx.Response(200, json={"echo": TOKEN}),
    lambda: httpx.ConnectError(f"failed with {TOKEN}"),
    lambda: httpx.ReadTimeout(f"slow {TOKEN}"),
]


@pytest.mark.parametrize("scenario", SCENARIOS)
async def test_the_token_never_reaches_a_log_line_an_exception_or_a_repr(
    scenario: Callable[[], httpx.Response | Exception],
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    caplog.set_level(logging.DEBUG)
    client, _, _ = build(Recorder(scenario()))

    error = await failure(client)

    streams = capsys.readouterr()
    assert TOKEN not in caplog.text
    assert TOKEN not in streams.out + streams.err
    assert TOKEN not in repr(error) and TOKEN not in str(error)
    assert error.__cause__ is None and error.__context__ is None
    assert TOKEN not in repr(client) and TOKEN not in str(client)
    assert TOKEN not in repr(vars(client))


def test_the_http_libraries_are_held_at_warning() -> None:
    HttpxAwinClient(PUBLISHER_ID, SecretStr(TOKEN))

    for name in ("httpx", "httpcore"):
        assert logging.getLogger(name).getEffectiveLevel() >= logging.WARNING


async def test_aclose_closes_the_http_client() -> None:
    client, _, _ = build(Recorder(ok([])))

    await client.aclose()

    assert client.http_client.is_closed


async def test_the_request_asks_for_an_unencoded_body_so_the_size_cap_counts_real_bytes() -> (
    None
):
    recorder = Recorder(ok([ITEM]))
    client, _, _ = build(recorder)

    await fetch(client)

    assert recorder.requests[0].headers["accept-encoding"] == "identity"


@pytest.mark.parametrize("encoding", ["gzip", "br", "deflate", "zstd", "gzip, br"])
async def test_a_response_that_carries_a_content_encoding_is_refused(
    encoding: str,
) -> None:
    import gzip
    import zlib

    body = zlib.compress(b"[]") if encoding == "deflate" else gzip.compress(b"[]")
    recorder = Recorder(
        httpx.Response(200, content=body, headers={"content-encoding": encoding})
    )
    client, _, _ = build(recorder)

    error = await failure(client)

    assert error.reason is AwinReason.UNSUPPORTED_ENCODING
    assert len(recorder.requests) == 1  # not retried


@pytest.mark.parametrize("encoding", ["identity", "Identity", ""])
async def test_an_identity_encoding_is_fine(encoding: str) -> None:
    client, _, _ = build(
        Recorder(
            httpx.Response(200, json=[ITEM], headers={"content-encoding": encoding})
        )
    )

    page = await fetch(client)

    assert len(page.items) == 1


@pytest.mark.parametrize(
    "token",
    [
        "with space",
        "line\nbreak",
        "carriage\rreturn",
        "tab\tinside",
        "caf\u00e9",
        "",
        "trail ",
    ],
)
def test_a_token_that_cannot_be_a_header_value_is_refused_without_echoing_it(
    token: str,
) -> None:
    with pytest.raises(ValueError) as caught:
        HttpxAwinClient(PUBLISHER_ID, SecretStr(token))

    assert token not in str(caught.value) or token == ""
    assert "token" in str(caught.value)


def test_a_boolean_is_not_a_publisher_id() -> None:
    with pytest.raises(ValueError, match="publisher"):
        HttpxAwinClient(True, SecretStr(TOKEN))  # type: ignore[arg-type]
