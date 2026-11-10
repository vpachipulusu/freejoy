"""A small client for the Awin Publisher API's promotions endpoint (offers importer, FREEJ-46).

``AwinClient`` is the seam: the importer asks it for one page of promotions and gets a
``PromotionsPage`` or an ``AwinError`` that carries a reason code and nothing else. ``HttpxAwinClient``
is the real implementation. Everything it does is chosen to keep three things true:

* **The token only ever travels in an ``Authorization`` header.** Awin also accepts an
  ``accessToken`` query parameter; it is never used, so the token is in no URL, no log line, no
  exception text, no ``repr`` and (because errors are built outside the ``except`` block) no
  ``__cause__`` or ``__context__``. ``SecretStr`` is unwrapped at the one call site that builds the
  header.
* **The host is fixed.** ``BASE_URL`` is a constant, not a setting. TLS verification stays on, proxy
  variables from the environment are ignored (``trust_env=False``) and redirects are never followed
  (a redirect would carry the header to another host; it is reported as ``redirected``).
* **Everything is bounded.** Connect, read, write and pool timeouts, an overall deadline per attempt,
  a response size cap (the body is streamed and abandoned at the cap), a client-side limit of 20 calls
  per 60 s (Awin's own limit, per user) that every attempt passes through, and a few retries with
  backoff for 429, 5xx, timeouts and dropped connections only. A 401 or 403 stops at once.

What Awin's documentation does not say (the response envelope, how the last page is signalled, error
bodies) is handled defensively: ``parse_promotions`` accepts a bare list or an object with the list
under ``data`` and anything else fails closed as ``unrecognised_response``. Error bodies are never
read. See ``docs/developer/backend-awin-import.md`` for the list of unverified assumptions.

Do not use it in a process where Sentry is initialised: the SDK's httpx integration would record each
request as a breadcrumb. The import command starts Sentry only after the last request.
"""

import asyncio
import enum
import json
import logging
import re
import ssl
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal, Protocol

import httpx
from pydantic import SecretStr

from app.core.call_limiter import SlidingWindowLimiter

BASE_URL = "https://api.awin.com"
USER_AGENT = "Freejoy-AwinImport/1.0"
REGION_CODES = ("GB",)

MIN_PAGE_SIZE = 10
MAX_PAGE_SIZE = 200
CALLS_PER_WINDOW = 20  # Awin: 20 API calls per minute per user
WINDOW_SECONDS = 60.0

CONNECT_TIMEOUT_SECONDS = 5.0
READ_TIMEOUT_SECONDS = 20.0
WRITE_TIMEOUT_SECONDS = 10.0
POOL_TIMEOUT_SECONDS = 5.0
ATTEMPT_DEADLINE_SECONDS = 30.0
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_ATTEMPTS = 4
BACKOFF_BASE_SECONDS = 1.0
BACKOFF_CAP_SECONDS = 30.0
MAX_RETRY_AFTER_SECONDS = 60.0
MAX_CONNECTIONS = 2

AUTH_STATUSES = frozenset({401, 403})
THROTTLE_STATUS = 429
SERVER_ERROR_MIN, SERVER_ERROR_MAX = 500, 599
REDIRECT_MIN, REDIRECT_MAX = 300, 399
CLIENT_ERROR_MIN, CLIENT_ERROR_MAX = 400, 499
_RETRY_AFTER = re.compile(r"\d{1,6}")
_QUIET_LOGGERS = ("httpx", "httpcore")


class AwinReason(enum.StrEnum):
    """Why a call failed. These strings are all that leaves this module about a failure."""

    AUTH_FAILED = "auth_failed"
    RATE_LIMITED = "rate_limited"
    SERVER_ERROR = "server_error"
    TIMEOUT = "timeout"
    CONNECTION_ERROR = "connection_error"
    TLS_ERROR = "tls_error"
    CLIENT_ERROR = "client_error"
    REDIRECTED = "redirected"
    UNEXPECTED_STATUS = "unexpected_status"
    RESPONSE_TOO_LARGE = "response_too_large"
    UNSUPPORTED_ENCODING = "unsupported_encoding"
    INVALID_JSON = "invalid_json"
    UNRECOGNISED_RESPONSE = "unrecognised_response"


_RETRYABLE = frozenset(
    {
        AwinReason.RATE_LIMITED,
        AwinReason.SERVER_ERROR,
        AwinReason.TIMEOUT,
        AwinReason.CONNECTION_ERROR,
    }
)


class AwinError(Exception):
    """A call failed. ``str(error)`` is the reason code; no response text, URL or header is kept."""

    def __init__(self, reason: AwinReason) -> None:
        super().__init__(reason.value)
        self.reason = reason


# The values Awin documents for the membership filter (help.awin.com/apidocs/promotions).
Membership = Literal["joined", "notJoined", "all"]


@dataclass(frozen=True)
class PromotionFilters:
    """The filters the importer sends: joined programmes, vouchers and promotions, active, UK only.

    ``membership`` is ``joined`` unless a dry-run probe asks for more; a real run refuses any other value.
    """

    updated_since: date | None = None
    membership: Membership = "joined"

    def to_json(self) -> dict[str, Any]:
        filters: dict[str, Any] = {
            "membership": self.membership,
            "type": "all",
            "status": "active",
            "regionCodes": list(REGION_CODES),
        }
        if self.updated_since is not None:
            filters["updatedSince"] = self.updated_since.isoformat()
        return filters


@dataclass(frozen=True)
class PromotionsPage:
    """One page of raw items (untrusted) and, when the response said so, how many pages exist."""

    items: tuple[Any, ...]
    total_pages: int | None = None
    envelope: Literal["list", "object"] = "list"  # a bare list, or {"data": [...]}
    total_pages_present: bool = False  # pagination.totalPages was there (usable or not)


class AwinClient(Protocol):
    async def fetch_promotions(
        self, filters: PromotionFilters, *, page: int, page_size: int
    ) -> PromotionsPage:
        """One page of promotions; raises ``AwinError`` (reason code only) on any failure."""

    async def aclose(self) -> None: ...


class CallLimiter(Protocol):
    async def acquire(self) -> None: ...


def parse_promotions(payload: object) -> PromotionsPage | None:
    """A page from a decoded body, or ``None`` when the envelope is not one of the known shapes.

    Known: a bare list, or an object whose ``data`` is a list (with an optional ``pagination`` object
    whose ``totalPages`` is a non-negative integer). The total is only a hint to stop early.
    """
    if isinstance(payload, list):
        return PromotionsPage(tuple(payload))
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        return None
    pagination = payload.get("pagination")
    total = pagination.get("totalPages") if isinstance(pagination, dict) else None
    valid = isinstance(total, int) and not isinstance(total, bool) and total >= 0
    present = isinstance(pagination, dict) and "totalPages" in pagination
    return PromotionsPage(
        tuple(payload["data"]), total if valid else None, "object", present
    )


@dataclass(frozen=True)
class _Outcome:
    page: PromotionsPage | None = None
    failure: AwinReason | None = None
    retry_after: float | None = None


def _retry_after(value: str | None) -> float | None:
    if value is None or not _RETRY_AFTER.fullmatch(value.strip()):
        return None
    return min(float(value), MAX_RETRY_AFTER_SECONDS)


def _is_tls_failure(error: BaseException) -> bool:
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        if isinstance(current, ssl.SSLError):
            return True
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return False


def _status_failure(status: int) -> AwinReason | None:
    """The failure for a status code, or ``None`` for 200."""
    if status in AUTH_STATUSES:
        return AwinReason.AUTH_FAILED
    if status == THROTTLE_STATUS:
        return AwinReason.RATE_LIMITED
    if SERVER_ERROR_MIN <= status <= SERVER_ERROR_MAX:
        return AwinReason.SERVER_ERROR
    if REDIRECT_MIN <= status <= REDIRECT_MAX:
        return AwinReason.REDIRECTED
    if CLIENT_ERROR_MIN <= status <= CLIENT_ERROR_MAX:
        return AwinReason.CLIENT_ERROR
    return None if status == httpx.codes.OK else AwinReason.UNEXPECTED_STATUS


def _quiet_http_loggers() -> None:
    """httpx and httpcore log each request URL at INFO and DEBUG; keep them at WARNING."""
    for name in _QUIET_LOGGERS:
        logger = logging.getLogger(name)
        logger.setLevel(max(logger.level, logging.WARNING))


class HttpxAwinClient:
    """``AwinClient`` over httpx. Reads no error body, follows no redirect, keeps no token in view."""

    def __init__(
        self,
        publisher_id: int,
        token: SecretStr,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        limiter: CallLimiter | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if type(publisher_id) is not int or publisher_id < 1:
            raise ValueError("publisher id must be a positive integer")
        secret = token.get_secret_value()
        if not secret or not all("!" <= char <= "~" for char in secret):
            raise ValueError("the token must be printable ASCII without whitespace")
        _quiet_http_loggers()
        self._url = f"{BASE_URL}/publisher/{publisher_id}/promotions"
        self._publisher_id = publisher_id
        self._token = token
        self._limiter: CallLimiter = limiter or SlidingWindowLimiter(
            CALLS_PER_WINDOW, WINDOW_SECONDS
        )
        self._sleep = sleep
        self._http = httpx.AsyncClient(
            transport=transport,
            timeout=httpx.Timeout(
                CONNECT_TIMEOUT_SECONDS,
                read=READ_TIMEOUT_SECONDS,
                write=WRITE_TIMEOUT_SECONDS,
                pool=POOL_TIMEOUT_SECONDS,
            ),
            limits=httpx.Limits(max_connections=MAX_CONNECTIONS),
            follow_redirects=False,
            trust_env=False,
            verify=True,
        )

    def __repr__(self) -> str:
        return f"HttpxAwinClient(publisher_id={self._publisher_id})"

    @property
    def http_client(self) -> httpx.AsyncClient:
        return self._http

    async def aclose(self) -> None:
        await self._http.aclose()

    async def fetch_promotions(
        self, filters: PromotionFilters, *, page: int, page_size: int
    ) -> PromotionsPage:
        if page < 1 or not MIN_PAGE_SIZE <= page_size <= MAX_PAGE_SIZE:
            raise ValueError("page must be at least 1 and page_size between 10 and 200")
        body = {
            "filters": filters.to_json(),
            "pagination": {"page": page, "pageSize": page_size},
        }
        outcome = _Outcome(failure=AwinReason.CONNECTION_ERROR)
        for attempt in range(1, MAX_ATTEMPTS + 1):
            await self._limiter.acquire()
            outcome = await self._attempt(body)
            if outcome.page is not None:
                return outcome.page
            if outcome.failure not in _RETRYABLE or attempt == MAX_ATTEMPTS:
                break
            await self._sleep(self._delay(attempt, outcome.retry_after))
        raise AwinError(outcome.failure or AwinReason.CONNECTION_ERROR)

    @staticmethod
    def _delay(attempt: int, retry_after: float | None) -> float:
        backoff = min(
            BACKOFF_BASE_SECONDS * (2.0 ** (attempt - 1)), BACKOFF_CAP_SECONDS
        )
        return max(backoff, retry_after or 0.0)

    async def _attempt(self, body: dict[str, Any]) -> _Outcome:
        try:
            async with asyncio.timeout(ATTEMPT_DEADLINE_SECONDS):
                return await self._exchange(body)
        except (TimeoutError, httpx.TimeoutException):
            return _Outcome(failure=AwinReason.TIMEOUT)
        except httpx.HTTPError as error:
            tls = _is_tls_failure(error)
            return _Outcome(
                failure=AwinReason.TLS_ERROR if tls else AwinReason.CONNECTION_ERROR
            )

    async def _exchange(self, body: dict[str, Any]) -> _Outcome:
        headers = {
            "Authorization": f"Bearer {self._token.get_secret_value()}",
            "Accept": "application/json",
            "Accept-Encoding": "identity",  # so the size cap counts the bytes on the wire
            "User-Agent": USER_AGENT,
        }
        async with self._http.stream(
            "POST", self._url, json=body, headers=headers
        ) as response:
            failure = _status_failure(response.status_code)
            if failure is not None:  # the error body is never read
                retry = _retry_after(response.headers.get("retry-after"))
                return _Outcome(failure=failure, retry_after=retry)
            encoding = response.headers.get("content-encoding", "identity")
            if encoding.strip().lower() not in ("", "identity"):
                return _Outcome(failure=AwinReason.UNSUPPORTED_ENCODING)
            return await self._decode(response)

    @staticmethod
    async def _decode(response: httpx.Response) -> _Outcome:
        declared = response.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > MAX_RESPONSE_BYTES:
            return _Outcome(failure=AwinReason.RESPONSE_TOO_LARGE)
        chunks: list[bytes] = []
        size = 0
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > MAX_RESPONSE_BYTES:
                return _Outcome(failure=AwinReason.RESPONSE_TOO_LARGE)
            chunks.append(chunk)
        try:
            payload = json.loads(b"".join(chunks))
        except (ValueError, RecursionError):
            return _Outcome(failure=AwinReason.INVALID_JSON)
        page = parse_promotions(payload)
        if page is None:
            return _Outcome(failure=AwinReason.UNRECOGNISED_RESPONSE)
        return _Outcome(page=page)
