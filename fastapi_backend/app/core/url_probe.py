"""Check that an admin-written https URL still answers, without becoming an SSRF hole.

``UrlProbe`` is the seam: callers hand it a URL and get back a ``ProbeResult`` (a status and a
reason code, never an error message or the URL). ``HttpxUrlProbe`` is the real implementation.

Controls, in the order they apply to one URL:

1. ``validate_https_url`` again (https only, port 443, no user info, DNS names only, no control
   characters). A failure is ``refused / invalid_url`` and nothing is resolved or requested.
2. The name is resolved here, and the check is refused (``refused / blocked_address``) unless
   *every* answer is a globally routable unicast address (``app.core.address_policy``).
3. The request is sent to the validated IP address itself, with the real name in the ``Host``
   header and as the TLS server name (``sni_hostname``), so the certificate is still checked
   against the name. The address that was validated is therefore the address connected to: the
   client never resolves the name a second time, which closes the DNS-rebinding window.
4. Redirects are not followed. A 3xx answer means the destination is up, and that is all this
   reports (the redirect target is never requested, so it never needs re-validation).
5. Proxy settings from the environment are ignored (a proxy would resolve the name itself).
6. ``HEAD`` first; ``GET`` only after a 405 or 501, and the body is never read: leaving the
   ``stream`` block closes the connection.
7. Connect, read and whole-check deadlines, and a neutral ``User-Agent``.

Status mapping: 2xx and 3xx are reachable; 401, 403, 405 and 429 are inconclusive (the site is
up but will not say, usually a bot filter); other 4xx and every 5xx are unreachable; DNS,
connection, TLS and timeout failures are unreachable. Nothing here logs: the HTTP client
libraries' own request logging (which prints URLs) is held at WARNING.
"""

import asyncio
import enum
import logging
import socket
import ssl
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

import httpx

from app.core.address_policy import is_public_address
from app.core.catalogue_urls import HTTPS_PORT, InvalidUrl, validate_https_url

CONNECT_TIMEOUT = 5.0
READ_TIMEOUT = 10.0
TOTAL_TIMEOUT = 20.0
MAX_ADDRESS_ATTEMPTS = 3
MAX_CONNECTIONS = 20
HEAD_FALLBACK_STATUSES = frozenset({405, 501})
_QUIET_LOGGERS = ("httpx", "httpcore")

HTTP_OK_MIN, HTTP_REDIRECT_MIN, HTTP_CLIENT_ERROR_MIN = 200, 300, 400
HTTP_SERVER_ERROR_MIN, HTTP_SERVER_ERROR_MAX = 500, 599
AUTH_STATUSES = frozenset({401, 403})
METHOD_STATUSES = frozenset({405})
RATE_LIMIT_STATUS = 429


class ProbeStatus(enum.StrEnum):
    REACHABLE = "reachable"
    INCONCLUSIVE = "inconclusive"
    UNREACHABLE = "unreachable"
    REFUSED = "refused"  # never requested: the URL or its address failed a safety rule


class ProbeReason(enum.StrEnum):
    OK = "ok"
    REDIRECT = "redirect"
    AUTH_REQUIRED = "auth_required"
    METHOD_NOT_ALLOWED = "method_not_allowed"
    RATE_LIMITED = "rate_limited"
    DNS_FAILURE = "dns_failure"
    CONNECTION_ERROR = "connection_error"
    TIMEOUT = "timeout"
    TLS_ERROR = "tls_error"
    CLIENT_ERROR = "client_error"
    SERVER_ERROR = "server_error"
    INVALID_URL = "invalid_url"
    BLOCKED_ADDRESS = "blocked_address"
    PROBE_ERROR = "probe_error"  # a bug or an odd answer on our side, not a verdict


@dataclass(frozen=True)
class ProbeResult:
    status: ProbeStatus
    reason: ProbeReason


class UrlProbe(Protocol):
    async def check(self, url: str) -> ProbeResult:
        """Classify ``url``. Never raises (apart from cancellation) and never reveals the URL."""


class Resolver(Protocol):
    async def resolve(self, host: str) -> Sequence[str]:
        """Every address ``host`` resolves to; raises ``OSError`` when it does not resolve."""


class SystemResolver:
    """The operating system's resolver, off the event loop."""

    async def resolve(self, host: str) -> Sequence[str]:
        infos = await asyncio.get_running_loop().getaddrinfo(
            host, HTTPS_PORT, type=socket.SOCK_STREAM
        )
        return list(dict.fromkeys(str(info[4][0]) for info in infos))


def classify_status(code: int) -> ProbeResult:
    """The category for an HTTP status code."""
    if HTTP_OK_MIN <= code < HTTP_REDIRECT_MIN:
        return ProbeResult(ProbeStatus.REACHABLE, ProbeReason.OK)
    if HTTP_REDIRECT_MIN <= code < HTTP_CLIENT_ERROR_MIN:
        return ProbeResult(ProbeStatus.REACHABLE, ProbeReason.REDIRECT)
    if code in AUTH_STATUSES:
        return ProbeResult(ProbeStatus.INCONCLUSIVE, ProbeReason.AUTH_REQUIRED)
    if code in METHOD_STATUSES:
        return ProbeResult(ProbeStatus.INCONCLUSIVE, ProbeReason.METHOD_NOT_ALLOWED)
    if code == RATE_LIMIT_STATUS:
        return ProbeResult(ProbeStatus.INCONCLUSIVE, ProbeReason.RATE_LIMITED)
    if HTTP_CLIENT_ERROR_MIN <= code < HTTP_SERVER_ERROR_MIN:
        return ProbeResult(ProbeStatus.UNREACHABLE, ProbeReason.CLIENT_ERROR)
    if HTTP_SERVER_ERROR_MIN <= code <= HTTP_SERVER_ERROR_MAX:
        return ProbeResult(ProbeStatus.UNREACHABLE, ProbeReason.SERVER_ERROR)
    return ProbeResult(ProbeStatus.INCONCLUSIVE, ProbeReason.PROBE_ERROR)


def _has_tls_cause(error: BaseException) -> bool:
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        if isinstance(current, ssl.SSLError):
            return True
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return False


def _describe_failure(error: httpx.HTTPError) -> ProbeResult:
    if isinstance(error, httpx.TimeoutException):
        return ProbeResult(ProbeStatus.UNREACHABLE, ProbeReason.TIMEOUT)
    if _has_tls_cause(error):
        return ProbeResult(ProbeStatus.UNREACHABLE, ProbeReason.TLS_ERROR)
    return ProbeResult(ProbeStatus.UNREACHABLE, ProbeReason.CONNECTION_ERROR)


def _netloc(address: str) -> str:
    return f"[{address}]" if ":" in address else address


def _quiet_http_loggers() -> None:
    """httpx and httpcore log each request URL at INFO and DEBUG; keep them at WARNING."""
    for name in _QUIET_LOGGERS:
        logger = logging.getLogger(name)
        logger.setLevel(max(logger.level, logging.WARNING))


class HttpxUrlProbe:
    """``UrlProbe`` over httpx, pinned to the address that passed the safety check."""

    def __init__(
        self,
        *,
        user_agent: str,
        resolver: Resolver | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        total_timeout: float = TOTAL_TIMEOUT,
    ) -> None:
        _quiet_http_loggers()
        self._resolver: Resolver = resolver or SystemResolver()
        self._total_timeout = total_timeout
        self._client = httpx.AsyncClient(
            transport=transport,
            headers={"User-Agent": user_agent, "Accept": "*/*"},
            timeout=httpx.Timeout(
                CONNECT_TIMEOUT,
                read=READ_TIMEOUT,
                write=READ_TIMEOUT,
                pool=READ_TIMEOUT,
            ),
            limits=httpx.Limits(
                max_connections=MAX_CONNECTIONS, max_keepalive_connections=0
            ),
            follow_redirects=False,
            trust_env=False,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def check(self, url: str) -> ProbeResult:
        try:
            async with asyncio.timeout(self._total_timeout):
                return await self._check(url)
        except TimeoutError:
            return ProbeResult(ProbeStatus.UNREACHABLE, ProbeReason.TIMEOUT)
        except Exception:  # noqa: BLE001  (a probe never raises; the type is not reported)
            return ProbeResult(ProbeStatus.INCONCLUSIVE, ProbeReason.PROBE_ERROR)

    async def _check(self, url: str) -> ProbeResult:
        try:
            parts = urlsplit(validate_https_url(url))
        except InvalidUrl:
            return ProbeResult(ProbeStatus.REFUSED, ProbeReason.INVALID_URL)
        host = parts.hostname or ""
        try:
            addresses = await self._resolver.resolve(host)
        except OSError:
            return ProbeResult(ProbeStatus.UNREACHABLE, ProbeReason.DNS_FAILURE)
        if not addresses:
            return ProbeResult(ProbeStatus.UNREACHABLE, ProbeReason.DNS_FAILURE)
        if not all(is_public_address(address) for address in addresses):
            return ProbeResult(ProbeStatus.REFUSED, ProbeReason.BLOCKED_ADDRESS)
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        return await self._try_addresses(host, path, addresses[:MAX_ADDRESS_ATTEMPTS])

    async def _try_addresses(
        self, host: str, path: str, addresses: Sequence[str]
    ) -> ProbeResult:
        """First address that connects decides; only a failure to connect moves to the next."""
        last = ProbeResult(ProbeStatus.INCONCLUSIVE, ProbeReason.PROBE_ERROR)
        for address in addresses:
            try:
                return await self._attempt(host, path, address)
            except (httpx.ConnectError, httpx.ConnectTimeout) as error:
                last = _describe_failure(error)
            except httpx.HTTPError as error:
                return _describe_failure(error)
        return last

    async def _attempt(self, host: str, path: str, address: str) -> ProbeResult:
        status = await self._status("HEAD", host, path, address)
        if status in HEAD_FALLBACK_STATUSES:
            status = await self._status("GET", host, path, address)
        return classify_status(status)

    async def _status(self, method: str, host: str, path: str, address: str) -> int:
        async with self._client.stream(
            method,
            f"https://{_netloc(address)}{path}",
            headers={"Host": host},
            extensions={"sni_hostname": host},
        ) as response:
            return response.status_code
