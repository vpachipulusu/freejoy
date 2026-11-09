import json
from collections.abc import Iterator
from typing import Any

import pytest
import sentry_sdk
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sentry_sdk.transport import Transport

from app.core.config import Settings
from app.core.logging import request_id_var
from app.core.sentry import (
    FILTERED,
    flush_events,
    init_sentry,
    report_exception,
    report_message,
    scrub_breadcrumb,
    scrub_event,
    tag_request_id,
)
from app.main import create_app

DSN = "https://publickey@o0.ingest.sentry.io/1"


class RecordingTransport(Transport):
    """In-memory transport: nothing leaves the process."""

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        super().__init__(options)
        self.events: list[dict[str, Any]] = []

    def capture_envelope(self, envelope: Any) -> None:
        for item in envelope.items:
            if item.type == "event":
                self.events.append(item.payload.json)


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "DATABASE_URL": "postgresql+asyncpg://test:test@localhost:5432/test",
        "ENVIRONMENT": "test",
    }
    return Settings(**{**values, **overrides})  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def reset_sentry() -> Iterator[None]:
    yield
    sentry_sdk.get_global_scope().client = None  # type: ignore[assignment]
    sentry_sdk.init()  # disabled client: no DSN


def test_does_not_start_without_a_dsn() -> None:
    started = init_sentry(make_settings(SENTRY_DSN=None))

    assert started is False
    assert sentry_sdk.is_initialized() is False


def test_does_not_start_with_an_empty_dsn() -> None:
    assert init_sentry(make_settings(SENTRY_DSN=SecretStr(""))) is False


def test_starts_with_a_dsn_and_sets_environment_release_and_pii() -> None:
    transport = RecordingTransport()
    settings = make_settings(
        SENTRY_DSN=DSN, VERCEL_ENV="preview", VERCEL_GIT_COMMIT_SHA="abc123"
    )

    started = init_sentry(settings, transport=transport)

    options = sentry_sdk.get_client().options
    assert started is True
    assert options["environment"] == "preview"
    assert options["release"] == "abc123"
    assert options["send_default_pii"] is False


def test_environment_falls_back_to_environment_setting() -> None:
    init_sentry(make_settings(SENTRY_DSN=DSN), transport=RecordingTransport())

    assert sentry_sdk.get_client().options["environment"] == "test"


def test_dsn_is_not_exposed_by_settings_repr() -> None:
    assert DSN not in repr(make_settings(SENTRY_DSN=DSN))


def test_scrub_removes_sensitive_headers_cookies_and_body() -> None:
    event = {
        "request": {
            "headers": {
                "Authorization": "Bearer secret",
                "Cookie": "session=secret",
                "X-Session-Token": "secret",
                "X-Internal-Key": "secret",
                "User-Agent": "pytest",
            },
            "cookies": {"session": "secret"},
            "data": {"password": "secret"},
            "url": "http://test/x",
        }
    }

    scrubbed = scrub_event(event)

    headers = scrubbed["request"]["headers"]
    assert headers["Authorization"] == FILTERED
    assert headers["Cookie"] == FILTERED
    assert headers["X-Session-Token"] == FILTERED
    assert headers["X-Internal-Key"] == FILTERED
    assert headers["User-Agent"] == "pytest"
    assert "data" not in scrubbed["request"]
    assert "cookies" not in scrubbed["request"]
    assert "secret" not in str(scrubbed)


def test_scrub_filters_any_header_matching_the_denylist() -> None:
    event = {
        "request": {
            "headers": {
                "X-Api-Key": "v",
                "X-Auth-Foo": "v",
                "Proxy-Authorization": "v",
                "X-My-Secret": "v",
                "Accept": "json",
            }
        }
    }

    headers = scrub_event(event)["request"]["headers"]

    assert [k for k, v in headers.items() if v == FILTERED] == [
        "X-Api-Key",
        "X-Auth-Foo",
        "Proxy-Authorization",
        "X-My-Secret",
    ]
    assert headers["Accept"] == "json"


def test_scrub_drops_query_string_from_request_and_url() -> None:
    event = {
        "request": {
            "url": "http://test/x?token=abc&a=1#frag",
            "query_string": "token=abc&a=1",
        }
    }

    request = scrub_event(event)["request"]

    assert request["url"] == "http://test/x"
    assert "query_string" not in request


def test_scrub_sanitizes_contexts_and_extra() -> None:
    event = {
        "contexts": {"app": {"api_key": "v", "name": "n"}},
        "extra": {"password": "v", "items": [{"session_id": "v", "ok": 1}]},
    }

    scrubbed = scrub_event(event)

    assert scrubbed["contexts"] == {"app": {"api_key": FILTERED, "name": "n"}}
    assert scrubbed["extra"] == {
        "password": FILTERED,
        "items": [{"session_id": FILTERED, "ok": 1}],
    }


def test_breadcrumbs_drop_log_messages_and_scrub_data() -> None:
    assert scrub_breadcrumb({"category": "logging", "message": "email a@b.c"}) is None

    crumb = scrub_breadcrumb(
        {"category": "httplib", "data": {"url": "http://x/y?token=1", "auth": "v"}}
    )

    assert crumb["data"] == {"url": "http://x/y", "auth": FILTERED}


def test_server_name_is_fixed_and_tracing_is_off() -> None:
    init_sentry(make_settings(SENTRY_DSN=DSN), transport=RecordingTransport())

    options = sentry_sdk.get_client().options
    assert options["server_name"] == "yourapp-api"
    assert options["traces_sample_rate"] is None


def test_scrub_tolerates_events_without_a_request() -> None:
    assert scrub_event({"message": "x"}) == {"message": "x"}


def test_scrub_adds_the_context_request_id_tag() -> None:
    token = request_id_var.set("req-1")
    try:
        scrubbed = scrub_event({})
    finally:
        request_id_var.reset(token)

    assert scrubbed["tags"] == {"request_id": "req-1"}


def test_helpers_are_noops_when_sentry_is_off() -> None:
    tag_request_id("req-1")
    report_exception(RuntimeError("x"))  # must not raise


def test_report_message_and_flush_do_nothing_when_sentry_is_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sentry_sdk, "is_initialized", lambda: False)

    assert report_message("m", context_name="c", context={"a": 1}) is False
    flush_events()  # must not raise


def test_report_message_sends_one_scrubbed_event_with_the_context() -> None:
    transport = RecordingTransport()
    init_sentry(make_settings(SENTRY_DSN=DSN), transport=transport)

    sent = report_message(
        "Offer health check found problems",
        context_name="offer_health",
        context={
            "unreachable": [{"offer_id": "o1", "reason": "dns_failure"}],
            "api_token": "must-be-filtered",
        },
    )
    flush_events()

    assert sent is True
    assert len(transport.events) == 1
    event = transport.events[0]
    assert event["message"] == "Offer health check found problems"
    assert event["level"] == "warning"
    context = event["contexts"]["offer_health"]
    assert context["unreachable"] == [{"offer_id": "o1", "reason": "dns_failure"}]
    assert context["api_token"] == FILTERED


async def test_unhandled_exception_is_captured_once_with_context() -> None:
    transport = RecordingTransport()
    init_sentry(make_settings(SENTRY_DSN=DSN), transport=transport)
    app = create_app()

    @app.post("/_test/boom", tags=["t"])
    async def boom() -> None:
        raise RuntimeError("kaboom")

    client = AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    )
    async with client:
        response = await client.post(
            "/_test/boom",
            json={"password": "hunter2"},
            headers={"Authorization": "Bearer secret", "X-Session-Token": "tok"},
        )
    sentry_sdk.flush()

    assert response.status_code == 500
    assert response.json() == {"success": False, "error": "Internal server error"}
    assert len(transport.events) == 1
    event = transport.events[0]
    assert event["tags"]["request_id"] == response.headers["X-Request-ID"]
    assert event["request"]["url"].endswith("/_test/boom")
    assert event["exception"]["values"][0]["type"] == "RuntimeError"
    assert "secret" not in str(event)
    assert "hunter2" not in str(event)
    assert "tok" not in event["request"]["headers"].values()
    assert event["server_name"] == "yourapp-api"


ADDRESS = "victim@example.com"


@pytest.mark.parametrize(
    "url",
    [
        "https://api.brevo.com/v3/contacts/victim%40example.com",
        "https://api.brevo.com/v3/contacts/victim@example.com",
        "https://api.brevo.com/v3/contacts/victim%2540example.com",
        "https://api.brevo.com/v3/contacts/victim%40example.com?identifierType=email_id",
    ],
)
def test_breadcrumbs_drop_an_email_address_held_in_a_url_path(url: str) -> None:
    crumb = scrub_breadcrumb(
        {
            "category": "httplib",
            "data": {"url": url, "http.query": f"email={ADDRESS}", "reason": "OK"},
        }
    )

    text = json.dumps(crumb)
    assert "victim" not in text
    assert "example.com" not in text
    assert "%40" not in text
    assert crumb["data"]["url"].startswith("https://api.brevo.com/v3/contacts/")
    assert crumb["data"]["reason"] == "OK"


def test_breadcrumbs_redact_addresses_in_nested_data_at_any_depth() -> None:
    crumb = scrub_breadcrumb(
        {
            "category": "httplib",
            "data": {
                "extra": {"url": "https://api.brevo.com/v3/contacts/a%40b.com"},
                "items": [{"to": "victim@example.com"}, "x"],
            },
        }
    )

    text = json.dumps(crumb)
    assert "a%40b.com" not in text
    assert "victim" not in text
    assert (
        crumb["data"]["extra"]["url"] == f"https://api.brevo.com/v3/contacts/{FILTERED}"
    )
    assert crumb["data"]["items"] == [{"to": FILTERED}, "x"]


def test_scrub_redacts_addresses_in_contexts_extra_and_headers_at_any_depth() -> None:
    event = {
        "contexts": {"app": {"deep": {"url": "https://x/contacts/a%40b.com"}}},
        "extra": {"rows": [{"email": "victim@example.com"}], "n": 3},
        "request": {"headers": {"referer": "https://x/c/a%40b.com"}},
    }

    text = json.dumps(scrub_event(event))

    assert "a%40b.com" not in text
    assert "victim" not in text


def test_redaction_survives_a_self_referencing_dict() -> None:
    data: dict[str, Any] = {"to": "victim@example.com"}
    data["self"] = data
    data["list"] = [data, {"again": data}]

    crumb = scrub_breadcrumb({"category": "x", "data": data})

    text = json.dumps(crumb)
    assert "victim" not in text
    assert FILTERED in text


def test_redaction_survives_a_very_deep_nest() -> None:
    nested: Any = {"url": "https://x/contacts/a%40b.com"}
    for _ in range(5000):
        nested = {"next": nested}

    scrubbed = scrub_event({"extra": nested})

    assert "a%40b.com" not in json.dumps(scrubbed)


def test_redaction_is_quick_when_a_child_is_shared_many_times() -> None:
    node: Any = {"url": "https://x/contacts/a%40b.com"}
    for _ in range(18):
        node = {"left": node, "right": node}

    assert "a%40b.com" not in json.dumps(scrub_event({"extra": node}))


def test_redaction_covers_tuples_and_sets() -> None:
    event = {
        "extra": {
            "pair": ("victim@example.com", "ok"),
            "tags": {"victim@example.com"},
            "frozen": frozenset({"a%40b.com"}),
        }
    }

    extra = scrub_event(event)["extra"]

    assert "victim" not in json.dumps(extra)
    assert "a%40b.com" not in json.dumps(extra)
    assert extra["pair"] == [FILTERED, "ok"]


def test_scrub_drops_an_email_address_held_in_the_request_path() -> None:
    event = {"request": {"url": f"http://test/v1/x/{ADDRESS}?token=abc"}}

    request = scrub_event(event)["request"]

    assert request["url"] == f"http://test/v1/x/{FILTERED}"
