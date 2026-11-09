"""Sentry error reporting (ADR-003).

Initialised only when SENTRY_DSN is set. Events never carry credentials or request
bodies, and each one is tagged with the request ID from the request-ID middleware.
Unhandled exceptions are reported by the SDK's ASGI middleware when they pass
through the global 500 handler (``app.core.errors``), which does not report them
itself, so there is exactly one event per failure.
"""

import re
from collections.abc import Mapping
from typing import Any

import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration
from sentry_sdk.transport import Transport

from app.core.config import Settings
from app.core.logging import request_id_var

REQUEST_ID_TAG = "request_id"

# Case-insensitive denylist applied to header names, query parameters and the keys
# of contexts/extra. Covers ADR-003's X-Session-Token and X-Internal-Key by pattern.
SENSITIVE_NAME = re.compile(
    r"authorization|cookie|token|key|secret|session|auth|password", re.IGNORECASE
)
FILTERED = "[Filtered]"
SERVER_NAME = "yourapp-api"

# A path segment, query value or header word holding an email address, plain or
# percent-encoded (also twice). The Brevo contact calls put the address in the path
# (``/contacts/<email>``) and the SDK's httpx integration records that URL as a breadcrumb.
# Split into tokens first and test each for the mark, so a long run with no address
# costs one pass, not one per start position.
URL_TOKEN = re.compile(r"[^\s/?#&=;]+")
ADDRESS_MARK = re.compile(r"@|%(?:25)*40", re.IGNORECASE)


def _redact_addresses(value: Any) -> Any:
    """Return ``value`` with any email address replaced; non-strings pass through."""
    if not isinstance(value, str):
        return value
    return URL_TOKEN.sub(
        lambda token: FILTERED if ADDRESS_MARK.search(token.group()) else token.group(),
        value,
    )


MAX_REDACT_DEPTH = 20
_CONTAINERS = (dict, list, tuple, set, frozenset)


def _redact_keys(
    value: Any,
    _depth: int = 0,
    _path: frozenset[int] = frozenset(),
    _done: dict[int, Any] | None = None,
) -> Any:
    """Return a copy of ``value`` with sensitive keys filtered and addresses redacted.

    Every string leaf loses an email address (``_redact_addresses``); a value under
    a sensitive key is filtered whole. Lists, tuples and sets come back as lists. A
    cycle, or anything nested deeper than ``MAX_REDACT_DEPTH``, becomes the filtered
    marker, so a hostile or self-referencing event cannot raise ``RecursionError``
    (the SDK sends the raw event when a hook raises). A container reached again by
    another route is redacted once (``_done``), so shared children cost nothing.
    """
    if not isinstance(value, _CONTAINERS):
        return _redact_addresses(value)
    if _depth >= MAX_REDACT_DEPTH or id(value) in _path:
        return FILTERED
    done = {} if _done is None else _done
    if id(value) in done:
        return done[id(value)]
    path = _path | {id(value)}
    result: Any
    if isinstance(value, dict):
        result = {
            key: FILTERED
            if SENSITIVE_NAME.search(str(key))
            else _redact_keys(item, _depth + 1, path, done)
            for key, item in value.items()
        }
    else:
        result = [_redact_keys(item, _depth + 1, path, done) for item in value]
    done[id(value)] = result
    return result


def _strip_query(url: Any) -> Any:
    if not isinstance(url, str):
        return url
    return _redact_addresses(url.split("?", 1)[0].split("#", 1)[0])


def scrub_event(event: Any, _hint: Any = None) -> Any:
    """``before_send``: drop credentials, bodies and queries; tag the request ID."""
    request = event.get("request")
    if isinstance(request, dict):
        request.pop("data", None)
        request.pop("cookies", None)
        request.pop("query_string", None)
        if "url" in request:
            request["url"] = _strip_query(request["url"])
        headers = request.get("headers")
        if isinstance(headers, dict):
            request["headers"] = _redact_keys(headers)
    for section in ("contexts", "extra"):
        if section in event:
            event[section] = _redact_keys(event[section])
    request_id = request_id_var.get()
    if request_id:
        event.setdefault("tags", {}).setdefault(REQUEST_ID_TAG, request_id)
    return event


def scrub_breadcrumb(crumb: Any, _hint: Any = None) -> Any:
    """``before_breadcrumb``: drop log messages, scrub the rest."""
    if crumb.get("category") == "logging":
        return None
    data = crumb.get("data")
    if isinstance(data, dict):
        crumb["data"] = _redact_keys(
            {k: _strip_query(v) if k == "url" else v for k, v in data.items()}
        )
    return crumb


def tag_request_id(request_id: str) -> None:
    """Tag the current request's Sentry scope. A no-op when Sentry is off."""
    if sentry_sdk.is_initialized():
        sentry_sdk.get_isolation_scope().set_tag(REQUEST_ID_TAG, request_id)


def report_exception(exc: BaseException) -> None:
    """Report a handled-but-notable exception. A no-op when Sentry is off."""
    if sentry_sdk.is_initialized():
        sentry_sdk.capture_exception(exc)


def report_message(
    message: str,
    *,
    context_name: str,
    context: Mapping[str, Any],
    level: str = "warning",
) -> bool:
    """Send one event for something that is not an exception (a command's run summary).

    ``context`` becomes ``contexts[context_name]`` and goes through ``scrub_event`` like every other
    event. Keep the message constant so repeated runs group into one issue. Returns whether an event
    was queued; a no-op (False) when Sentry is off.
    """
    if not sentry_sdk.is_initialized():
        return False
    with sentry_sdk.new_scope() as scope:
        scope.set_context(context_name, dict(context))
        sentry_sdk.capture_message(message, level=level, scope=scope)  # type: ignore[arg-type]
    return True


def flush_events(timeout: float = 5.0) -> None:
    """Wait for queued events to be sent. A short-lived command calls this before it exits."""
    if sentry_sdk.is_initialized():
        sentry_sdk.flush(timeout=timeout)


def init_sentry(
    settings: Settings, transport: type[Transport] | Transport | None = None
) -> bool:
    """Start Sentry when a DSN is configured. Returns whether it was started."""
    if settings.SENTRY_DSN is None:
        return False
    dsn = settings.SENTRY_DSN.get_secret_value()
    if not dsn:
        return False
    sentry_sdk.init(
        dsn=dsn,
        environment=settings.VERCEL_ENV or settings.ENVIRONMENT,
        release=settings.VERCEL_GIT_COMMIT_SHA,
        send_default_pii=False,
        max_request_body_size="never",
        # Frame locals include the raw ASGI scope (all headers), so they stay off.
        include_local_variables=False,
        server_name=SERVER_NAME,
        # traces_sample_rate is intentionally unset: tracing and performance
        # monitoring are off, only errors are reported.
        before_send=scrub_event,
        before_breadcrumb=scrub_breadcrumb,
        integrations=[StarletteIntegration(), FastApiIntegration()],
        transport=transport,
    )
    return True
