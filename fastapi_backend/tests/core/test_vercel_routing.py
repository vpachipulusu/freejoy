"""The root vercel.json must send only the intended public paths to the backend.

The backend service has no public route of its own (ADR-011): a path reaches it only
through a top-level rewrite placed before the frontend catch-all. Without one for
``/webhooks/stripe`` Stripe never reaches the backend and a paid plan stays Free. The only other
backend source is the partner prefix (ADR-013), which must stay confined to exactly that prefix.

The matching rules, and what is assumed about Vercel's path-to-regexp, are in
``tests/vercel_rewrites.py``.
"""

import inspect
import json

import pytest

from tests.vercel_rewrites import (
    BACKEND_SERVICE,
    BACKEND_VERCEL_JSON,
    FRONTEND_SERVICE,
    SKIP_WITHOUT_VERCEL_JSON,
    VERCEL_JSON,
    backend_sources,
    destination_service,
    load_rewrites,
    source_pattern,
)

pytestmark = SKIP_WITHOUT_VERCEL_JSON
STRIPE_WEBHOOK_PATH = "/webhooks/stripe"
PARTNER_SOURCE = "/partner/v1/(.*)"


def _service(path: str) -> str | None:
    return destination_service(path, load_rewrites())


def test_everything_else_still_reaches_the_frontend() -> None:
    assert _service("/dashboard") == FRONTEND_SERVICE
    assert _service("/internal-notes") == FRONTEND_SERVICE


@pytest.mark.parametrize(
    ("path", "service"),
    [
        (STRIPE_WEBHOOK_PATH, BACKEND_SERVICE),
        ("/webhooks/stripe/", FRONTEND_SERVICE),
        ("/webhooks/stripe/x", FRONTEND_SERVICE),
        ("/webhooks/stripe-other", FRONTEND_SERVICE),
        ("/webhooks", FRONTEND_SERVICE),
        ("/api/webhooks/stripe", FRONTEND_SERVICE),
    ],
)
def test_only_the_exact_stripe_path_reaches_the_backend(
    path: str, service: str
) -> None:
    assert _service(path) == service


@pytest.mark.parametrize(
    "path",
    [
        "/partner/v1/x",
        "/partner/v1/",
        "/partner/v1/a/b",
        "/partner/v1/../v1/me",
        "/partner/v1//x",
    ],
)
def test_the_partner_prefix_reaches_the_backend(path: str) -> None:
    assert _service(path) == BACKEND_SERVICE


@pytest.mark.parametrize(
    "path",
    [
        "/partner/v1",
        "/partner/v2/x",
        "/partner/v2",
        "/partner",
        "/partner/",
        "/partnerx/v1/x",
        "/partner/v1x/y",
        "/Partner/v1/x",
        "/api/partner/v1/x",
        "/v1/me",
        "/v1/partner/v1/x",
        "/openapi.json",
        "/docs",
        "/redoc",
        "/health",
    ],
)
def test_everything_outside_the_partner_prefix_reaches_the_frontend(path: str) -> None:
    assert _service(path) == FRONTEND_SERVICE


def test_the_backend_sources_are_exactly_stripe_and_the_partner_prefix() -> None:
    assert backend_sources(load_rewrites()) == [STRIPE_WEBHOOK_PATH, PARTNER_SOURCE]


def test_the_catch_all_is_last_so_it_cannot_shadow_a_backend_rewrite() -> None:
    rewrites = load_rewrites()

    assert rewrites[-1]["source"] == "/(.*)"
    assert rewrites[-1]["destination"] == {"service": FRONTEND_SERVICE}


def test_the_first_matching_rewrite_wins() -> None:
    rewrites = [
        {"source": "/a/(.*)", "destination": {"service": "one"}},
        {"source": "/a/b", "destination": {"service": "two"}},
    ]

    assert destination_service("/a/b", rewrites) == "one"
    assert destination_service("/c", rewrites) is None


@pytest.mark.parametrize("source", ["/a/:id", "/a/(\\d+)", "/a?", "/(.*)+", "/a|b"])
def test_a_source_with_syntax_the_matcher_does_not_know_is_refused(source: str) -> None:
    with pytest.raises(ValueError, match="does not know"):
        source_pattern(source)


@pytest.mark.parametrize("condition", ["has", "missing"])
def test_a_conditional_rewrite_is_refused_not_matched_as_unconditional(
    condition: str,
) -> None:
    rewrites = [
        {
            "source": "/(.*)",
            condition: [{"type": "host", "value": "partners.example"}],
            "destination": {"service": "one"},
        }
    ]

    with pytest.raises(ValueError, match="conditional rewrite"):
        destination_service("/x", rewrites)


def test_every_rewrite_has_exactly_a_source_and_one_of_the_two_known_destinations() -> (
    None
):
    """No ``has``, ``missing``, ``headers`` or other key, and no third destination."""
    known = [{"service": BACKEND_SERVICE}, {"service": FRONTEND_SERVICE}]

    for rule in load_rewrites():
        assert set(rule) == {"source", "destination"}, rule
        assert rule["destination"] in known, rule


def test_the_root_vercel_json_has_only_the_top_level_keys_it_has_today() -> None:
    """A new top-level key (``routes``, ``redirects``, ``headers``, ``trailingSlash``...) can change what
    reaches the backend; adding one has to be a decision that also updates this test."""
    config = json.loads(VERCEL_JSON.read_text(encoding="utf-8"))

    assert set(config) == {"$schema", "git", "services", "rewrites"}
    assert set(config["services"]) == {FRONTEND_SERVICE, BACKEND_SERVICE}
    assert set(config["services"][BACKEND_SERVICE]) == {
        "root",
        "framework",
        "entrypoint",
    }


def test_the_backend_services_own_vercel_json_has_no_routing_keys() -> None:
    routing_keys = {
        "rewrites",
        "routes",
        "redirects",
        "headers",
        "trailingSlash",
        "cleanUrls",
    }
    config = json.loads(BACKEND_VERCEL_JSON.read_text(encoding="utf-8"))

    assert routing_keys & set(config) == set()


def test_the_stripe_route_matches_the_rewrite_and_checks_the_signature() -> None:
    from app.api.v1.webhooks import router

    assert {route.path for route in router.routes} == {STRIPE_WEBHOOK_PATH}  # type: ignore[attr-defined]
    for route in router.routes:
        assert "_verified_body(" in inspect.getsource(route.endpoint)  # type: ignore[attr-defined]
