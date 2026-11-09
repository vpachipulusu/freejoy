"""``app.core.url_probe``: reachability outcomes and the SSRF controls, without a socket.

The HTTP client is exercised through ``httpx.MockTransport`` and DNS through ``FakeResolver``;
nothing here opens a connection.
"""

import asyncio
import logging
import socket
import ssl
from collections.abc import AsyncIterator, Callable, Sequence
from typing import Any

import httpx
import pytest

from app.core import url_probe
from app.core.url_probe import (
    HttpxUrlProbe,
    ProbeReason,
    ProbeResult,
    ProbeStatus,
    SystemResolver,
    classify_status,
)
from tests.fakes_offer_health import FakeResolver

PUBLIC_V4 = "93.184.216.34"
PUBLIC_V4_B = "93.184.216.35"
PUBLIC_V6 = "2606:2800:220:1:248:1893:25c8:1946"
URL = "https://shop.example.org/offers/free-sample?ref=abc123#frag"
UA = "FreejoyLinkCheck/1.0"

Handler = Callable[[httpx.Request], httpx.Response | Any]


class ExplodingBody(httpx.AsyncByteStream):
    """A response body that fails the test if anything reads it."""

    async def __aiter__(self) -> AsyncIterator[bytes]:
        raise AssertionError("the response body was read")
        yield b""  # pragma: no cover


def make_probe(
    handler: Handler,
    *,
    table: dict[str, Sequence[str]] | None = None,
    total_timeout: float = 5.0,
) -> tuple[HttpxUrlProbe, FakeResolver, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    async def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        outcome = handler(request)
        if asyncio.iscoroutine(outcome):
            outcome = await outcome
        return outcome  # type: ignore[no-any-return]

    resolver = FakeResolver(
        table if table is not None else {"shop.example.org": [PUBLIC_V4]}
    )
    probe = HttpxUrlProbe(
        user_agent=UA,
        resolver=resolver,
        transport=httpx.MockTransport(record),
        total_timeout=total_timeout,
    )
    return probe, resolver, seen


def respond(code: int, **kwargs: Any) -> Handler:
    return lambda _request: httpx.Response(code, **kwargs)


@pytest.mark.parametrize(
    ("code", "status", "reason"),
    [
        (200, ProbeStatus.REACHABLE, ProbeReason.OK),
        (204, ProbeStatus.REACHABLE, ProbeReason.OK),
        (301, ProbeStatus.REACHABLE, ProbeReason.REDIRECT),
        (308, ProbeStatus.REACHABLE, ProbeReason.REDIRECT),
        (401, ProbeStatus.INCONCLUSIVE, ProbeReason.AUTH_REQUIRED),
        (403, ProbeStatus.INCONCLUSIVE, ProbeReason.AUTH_REQUIRED),
        (405, ProbeStatus.INCONCLUSIVE, ProbeReason.METHOD_NOT_ALLOWED),
        (429, ProbeStatus.INCONCLUSIVE, ProbeReason.RATE_LIMITED),
        (400, ProbeStatus.UNREACHABLE, ProbeReason.CLIENT_ERROR),
        (404, ProbeStatus.UNREACHABLE, ProbeReason.CLIENT_ERROR),
        (410, ProbeStatus.UNREACHABLE, ProbeReason.CLIENT_ERROR),
        (500, ProbeStatus.UNREACHABLE, ProbeReason.SERVER_ERROR),
        (501, ProbeStatus.UNREACHABLE, ProbeReason.SERVER_ERROR),
        (503, ProbeStatus.UNREACHABLE, ProbeReason.SERVER_ERROR),
        (100, ProbeStatus.INCONCLUSIVE, ProbeReason.PROBE_ERROR),
        (600, ProbeStatus.INCONCLUSIVE, ProbeReason.PROBE_ERROR),
    ],
)
def test_status_codes_map_to_one_category(
    code: int, status: ProbeStatus, reason: ProbeReason
) -> None:
    assert classify_status(code) == ProbeResult(status, reason)


async def test_a_2xx_head_is_reachable_and_the_body_is_never_read() -> None:
    probe, _, seen = make_probe(respond(200, stream=ExplodingBody()))

    result = await probe.check(URL)

    assert result == ProbeResult(ProbeStatus.REACHABLE, ProbeReason.OK)
    assert [request.method for request in seen] == ["HEAD"]


async def test_the_request_goes_to_the_validated_address_with_the_name_in_host_and_sni() -> (
    None
):
    probe, resolver, seen = make_probe(respond(200))

    await probe.check(URL)

    request = seen[0]
    assert resolver.lookups == ["shop.example.org"]
    assert (
        request.url.host == PUBLIC_V4
    )  # the address that was checked is the one connected to
    assert request.url.scheme == "https"
    assert request.headers["host"] == "shop.example.org"
    assert request.extensions["sni_hostname"] == "shop.example.org"
    assert request.url.raw_path == b"/offers/free-sample?ref=abc123"  # fragment dropped


async def test_the_user_agent_names_the_site() -> None:
    probe, _, seen = make_probe(respond(200))

    await probe.check(URL)

    assert seen[0].headers["user-agent"] == UA


async def test_an_ipv6_address_is_pinned_in_brackets() -> None:
    probe, _, seen = make_probe(respond(200), table={"shop.example.org": [PUBLIC_V6]})

    result = await probe.check(URL)

    assert result.status is ProbeStatus.REACHABLE
    assert seen[0].url.host == PUBLIC_V6
    assert seen[0].headers["host"] == "shop.example.org"


async def test_a_redirect_is_reachable_and_is_not_followed() -> None:
    probe, _, seen = make_probe(
        respond(302, headers={"location": "https://elsewhere.example.net/x"})
    )

    result = await probe.check(URL)

    assert result == ProbeResult(ProbeStatus.REACHABLE, ProbeReason.REDIRECT)
    assert len(seen) == 1


@pytest.mark.parametrize("head_code", [405, 501])
async def test_head_405_or_501_falls_back_to_a_get_that_is_not_read(
    head_code: int,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD":
            return httpx.Response(head_code)
        return httpx.Response(200, stream=ExplodingBody())

    probe, _, seen = make_probe(handler)

    result = await probe.check(URL)

    assert result == ProbeResult(ProbeStatus.REACHABLE, ProbeReason.OK)
    assert [request.method for request in seen] == ["HEAD", "GET"]
    assert {request.url.host for request in seen} == {PUBLIC_V4}


async def test_head_and_get_both_405_is_inconclusive() -> None:
    probe, _, seen = make_probe(respond(405))

    result = await probe.check(URL)

    assert result == ProbeResult(
        ProbeStatus.INCONCLUSIVE, ProbeReason.METHOD_NOT_ALLOWED
    )
    assert len(seen) == 2


async def test_a_get_fallback_can_find_the_page_is_gone() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(405 if request.method == "HEAD" else 404)

    probe, _, _ = make_probe(handler)

    result = await probe.check(URL)

    assert result == ProbeResult(ProbeStatus.UNREACHABLE, ProbeReason.CLIENT_ERROR)


async def test_other_head_statuses_do_not_trigger_a_get() -> None:
    probe, _, seen = make_probe(respond(404))

    await probe.check(URL)

    assert [request.method for request in seen] == ["HEAD"]


async def test_dns_failure_is_unreachable_and_nothing_is_requested() -> None:
    probe, _, seen = make_probe(respond(200), table={})

    result = await probe.check(URL)

    assert result == ProbeResult(ProbeStatus.UNREACHABLE, ProbeReason.DNS_FAILURE)
    assert seen == []


async def test_a_name_with_no_addresses_is_a_dns_failure() -> None:
    probe, _, seen = make_probe(respond(200), table={"shop.example.org": []})

    result = await probe.check(URL)

    assert result == ProbeResult(ProbeStatus.UNREACHABLE, ProbeReason.DNS_FAILURE)
    assert seen == []


@pytest.mark.parametrize(
    "addresses",
    [
        ["10.0.0.5"],
        ["127.0.0.1"],
        ["169.254.169.254"],
        ["100.64.0.9"],
        ["::1"],
        ["fe80::1"],
        ["::ffff:127.0.0.1"],
        ["224.0.0.1"],
        [PUBLIC_V4, "10.0.0.5"],  # one bad answer among good ones: refuse them all
        ["10.0.0.5", PUBLIC_V4],
        ["not-an-address"],
    ],
)
async def test_a_name_resolving_to_a_non_public_address_is_refused_before_any_request(
    addresses: list[str],
) -> None:
    probe, _, seen = make_probe(respond(200), table={"shop.example.org": addresses})

    result = await probe.check(URL)

    assert result == ProbeResult(ProbeStatus.REFUSED, ProbeReason.BLOCKED_ADDRESS)
    assert seen == []


@pytest.mark.parametrize(
    "url",
    [
        "http://shop.example.org/x",  # not https
        "https://127.0.0.1/x",  # IP literal
        "https://[::1]/x",
        "https://localhost/x",  # single label
        "https://user@shop.example.org/x",  # user info
        "https://shop.example.org:8443/x",  # non-default port
        "https://shop.example.org/x\r\nHost: evil",  # control characters
        "//shop.example.org/x",
        "javascript:alert(1)",
        "",
        "https://",
    ],
)
async def test_an_invalid_url_is_refused_without_resolving_or_requesting(
    url: str,
) -> None:
    probe, resolver, seen = make_probe(respond(200))

    result = await probe.check(url)

    assert result == ProbeResult(ProbeStatus.REFUSED, ProbeReason.INVALID_URL)
    assert resolver.lookups == [] and seen == []


@pytest.mark.parametrize(
    "error",
    [
        httpx.ConnectTimeout("t"),
        httpx.ReadTimeout("t"),
        httpx.WriteTimeout("t"),
        httpx.PoolTimeout("t"),
    ],
)
async def test_timeouts_are_unreachable_with_reason_timeout(error: Exception) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise error

    probe, _, _ = make_probe(handler)

    assert await probe.check(URL) == ProbeResult(
        ProbeStatus.UNREACHABLE, ProbeReason.TIMEOUT
    )


async def test_the_whole_check_has_a_deadline() -> None:
    async def slow(_request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(200)

    probe, _, _ = make_probe(slow, total_timeout=0.05)

    assert await probe.check(URL) == ProbeResult(
        ProbeStatus.UNREACHABLE, ProbeReason.TIMEOUT
    )


async def test_a_certificate_failure_is_a_tls_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        error = httpx.ConnectError("handshake")
        error.__cause__ = ssl.SSLCertVerificationError("certificate verify failed")
        raise error

    probe, _, _ = make_probe(handler)

    assert await probe.check(URL) == ProbeResult(
        ProbeStatus.UNREACHABLE, ProbeReason.TLS_ERROR
    )


@pytest.mark.parametrize(
    "error",
    [
        httpx.ConnectError("refused"),
        httpx.ReadError("reset"),
        httpx.RemoteProtocolError("bad"),
        httpx.TooManyRedirects("loop"),
    ],
)
async def test_connection_level_failures_are_connection_errors(
    error: Exception,
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise error

    probe, _, _ = make_probe(handler)

    assert await probe.check(URL) == ProbeResult(
        ProbeStatus.UNREACHABLE, ProbeReason.CONNECTION_ERROR
    )


async def test_the_next_address_is_tried_when_a_connection_cannot_be_made() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == PUBLIC_V6:
            raise httpx.ConnectError("no route to host")
        return httpx.Response(200)

    probe, _, seen = make_probe(
        handler, table={"shop.example.org": [PUBLIC_V6, PUBLIC_V4]}
    )

    result = await probe.check(URL)

    assert result == ProbeResult(ProbeStatus.REACHABLE, ProbeReason.OK)
    assert [request.url.host for request in seen] == [PUBLIC_V6, PUBLIC_V4]


async def test_at_most_three_addresses_are_tried() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    addresses = [f"93.184.216.{n}" for n in range(10, 16)]
    probe, _, seen = make_probe(handler, table={"shop.example.org": addresses})

    result = await probe.check(URL)

    assert result == ProbeResult(ProbeStatus.UNREACHABLE, ProbeReason.CONNECTION_ERROR)
    assert len(seen) == 3


async def test_a_failure_after_connecting_does_not_try_another_address() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    probe, _, seen = make_probe(
        handler, table={"shop.example.org": [PUBLIC_V4, PUBLIC_V4_B]}
    )

    await probe.check(URL)

    assert len(seen) == 1


async def test_an_unexpected_error_is_inconclusive_and_never_raised() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise RuntimeError("bug in the transport")

    probe, _, _ = make_probe(handler)

    assert await probe.check(URL) == ProbeResult(
        ProbeStatus.INCONCLUSIVE, ProbeReason.PROBE_ERROR
    )


async def test_nothing_about_the_url_reaches_logs_or_output(
    caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    caplog.set_level(logging.DEBUG)
    probe, _, _ = make_probe(respond(200))

    await probe.check(URL)
    await probe.check("https://127.0.0.1/secret?token=abc")

    captured = capsys.readouterr()
    everything = caplog.text + captured.out + captured.err
    for fragment in ("shop.example.org", "free-sample", "abc123", "secret", "token"):
        assert fragment not in everything


async def test_the_http_client_loggers_are_quietened_so_request_urls_never_reach_a_log() -> (
    None
):
    HttpxUrlProbe(user_agent=UA, resolver=FakeResolver())

    assert logging.getLogger("httpx").level >= logging.WARNING
    assert logging.getLogger("httpcore").level >= logging.WARNING


async def test_close_releases_the_client() -> None:
    probe, _, _ = make_probe(respond(200))

    await probe.aclose()
    await probe.aclose()  # closing twice is harmless


async def test_the_system_resolver_returns_unique_addresses_in_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_getaddrinfo(host: str, port: int, *_args: Any) -> list[Any]:
        assert (host, port) == ("shop.example.org", 443)
        return [
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", (PUBLIC_V6, 443, 0, 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_V4, 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_V4, 443)),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    addresses = await SystemResolver().resolve("shop.example.org")

    assert list(addresses) == [PUBLIC_V6, PUBLIC_V4]


def test_connections_are_never_kept_alive_and_http2_is_off() -> None:
    # Together these bind the certificate check to the Host header: see the comment in url_probe.
    assert url_probe.MAX_KEEPALIVE_CONNECTIONS == 0
    assert url_probe.USE_HTTP2 is False
