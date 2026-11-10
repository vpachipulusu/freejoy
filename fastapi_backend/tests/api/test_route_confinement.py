"""Route confinement (ADR-013): the whole application is walked, mounts included.

The invariant: a caller holding a valid partner key and the public partner URL can invoke only
partner handlers, and can read or change only its own merchant's data. This file proves the
routing half in the normal pytest run, so the gate carries it. The walk and its rules are in
``tests/confinement_walker.py``; the structural rules run everywhere, the ``vercel.json`` rule only
where the root ``vercel.json`` is present (and fails, not skips, in CI).

Negative tests run the walker on throwaway applications that break each rule.
"""

from pathlib import Path

import pytest
from fastapi import APIRouter, Depends, FastAPI
from starlette.routing import Mount

from app.api.auth import require_internal_key
from app.api.v1 import health, webhooks
from app.core.config import settings
from app.main import app, create_app
from app.partner.app import create_partner_api
from app.partner.mount import PartnerMount
from app.partner.surface import PARTNER_PREFIX, PartnerSurface
from tests.api.partner.support import call_raw
from tests.confinement_walker import (
    OPEN_ROUTES,
    PATH_PARAMETER,
    STRIPE_WEBHOOK_PATH,
    confinement_violations,
    context_path,
    docs_paths,
    main_app_contexts,
    mount_paths,
    reaches_backend,
    walk,
)
from tests.vercel_rewrites import (
    BACKEND_SERVICE,
    SKIP_WITHOUT_VERCEL_JSON,
    load_rewrites,
)


def _own_endpoint() -> None:
    """Defined in this test module, so outside ``app.partner``."""


def _throwaway(
    *routes: tuple[str, bool],
    mount: bool = True,
    partner_api: FastAPI | None = None,
) -> FastAPI:
    """A small app: ``(path, needs_key)`` routes, and the partner mount first unless ``mount`` is false."""
    application = FastAPI()
    router = APIRouter()
    for path, needs_key in routes:
        dependencies = [Depends(require_internal_key)] if needs_key else []
        router.add_api_route(path, lambda: None, dependencies=dependencies)
    application.include_router(router)
    if mount:
        surface = PartnerSurface(
            partner_api or create_partner_api(), enabled=lambda: True
        )
        application.router.routes.insert(
            0, PartnerMount(PARTNER_PREFIX, app=surface, name="partner")
        )
    return application


def _keyed_ok() -> list[tuple[str, bool]]:
    return [("/v1/closed", True)]


# ---------------------------------------------------------------- the real application


def test_the_real_application_keeps_every_structural_rule() -> None:
    assert confinement_violations(app) == []


@SKIP_WITHOUT_VERCEL_JSON
def test_the_real_application_keeps_the_vercel_rule_too() -> None:
    assert confinement_violations(app, load_rewrites()) == []


def test_the_walker_really_sees_the_included_routers() -> None:
    paths = {context_path(context) for context in main_app_contexts(app)}

    assert {"/health", "/health/ready", STRIPE_WEBHOOK_PATH, "/v1/me"} <= paths
    assert len(paths) > 30


def test_the_rules_hold_with_the_docs_switched_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "VERCEL_ENV", None)
    monkeypatch.setattr(settings, "API_DOCS_ENABLED", False)

    production = create_app()

    assert docs_paths(production) == frozenset()
    assert confinement_violations(production) == []


def test_the_docs_routes_are_allowed_only_while_they_are_enabled() -> None:
    served = {context_path(context) for context in main_app_contexts(app)}

    assert docs_paths(app) >= {"/docs", "/redoc", "/openapi.json"}
    assert docs_paths(app) <= served


def test_there_is_exactly_one_mount_a_partner_mount_around_a_surface() -> None:
    mounts = [r for r in app.routes if isinstance(r, Mount)]

    assert mount_paths(app) == ["/partner/v1"]
    assert len(mounts) == 1
    assert isinstance(mounts[0], PartnerMount)
    assert isinstance(mounts[0].app, PartnerSurface)
    assert app.routes[0] is mounts[0]


def test_every_allowlisted_route_still_exists() -> None:
    served = {context_path(context) for context in main_app_contexts(app)}

    assert OPEN_ROUTES <= served


def test_the_application_has_no_dependency_overrides() -> None:
    assert create_app().dependency_overrides == {}
    assert app.dependency_overrides == {}


@SKIP_WITHOUT_VERCEL_JSON
def test_the_only_main_app_route_a_backend_rewrite_reaches_is_stripe() -> None:
    rewrites = load_rewrites()

    reached = {
        context_path(context)
        for context in main_app_contexts(app)
        if reaches_backend(context_path(context) or "", rewrites)
    }

    assert reached == {STRIPE_WEBHOOK_PATH}


async def test_every_main_app_route_without_a_key_answers_401() -> None:
    """Behaviour, not structure: each route that is not allowlisted refuses a request with no key."""
    open_paths = OPEN_ROUTES | docs_paths(app)
    not_refused: list[tuple[str, str, int]] = []
    checked = 0

    for context in main_app_contexts(app):
        path = context_path(context) or ""
        if path in open_paths:
            continue
        for method in sorted((context.methods or set()) - {"HEAD", "OPTIONS"}):
            response = await call_raw(app, method, PATH_PARAMETER.sub("x", path))
            checked += 1
            if response.status != 401:
                not_refused.append((method, path, response.status))

    assert checked > 40
    assert not_refused == []


# ---------------------------------------------------------------- negative: the walker must fail


def test_the_walker_accepts_a_route_with_the_internal_key() -> None:
    assert confinement_violations(_throwaway(*_keyed_ok())) == []


def test_the_walker_fails_on_a_route_without_the_internal_key() -> None:
    problems = confinement_violations(_throwaway(("/v1/open", False)))

    assert problems == ["/v1/open has no internal key and is not on the allowlist"]


@pytest.mark.parametrize(
    "path", ["/partner/v1", "/partner/v1/x", "/Partner/x", "/PARTNER", "/partnerx"]
)
def test_no_main_app_route_may_sit_under_partner_in_any_case(path: str) -> None:
    problems = confinement_violations(_throwaway((path, True)))

    assert any("under /partner" in problem for problem in problems)


@pytest.mark.parametrize("pattern", ["/{a}/v1/{b}", "/{rest:path}", "/partner/{b}/{c}"])
def test_a_route_whose_pattern_can_match_the_partner_prefix_is_a_violation(
    pattern: str,
) -> None:
    problems = confinement_violations(_throwaway((pattern, True)))

    assert any(f"{pattern} can match /partner/v1" in problem for problem in problems)


def test_a_route_whose_pattern_can_match_the_stripe_path_is_a_violation() -> None:
    problems = confinement_violations(_throwaway(("/webhooks/{provider}", True)))

    assert problems == ["/webhooks/{provider} can match /webhooks/stripe"]


def test_the_stripe_route_itself_may_match_the_stripe_path() -> None:
    application = _throwaway((STRIPE_WEBHOOK_PATH, False))

    assert confinement_violations(application) == []


def test_the_walker_fails_when_there_is_no_partner_mount() -> None:
    problems = confinement_violations(_throwaway(*_keyed_ok(), mount=False))

    assert len(problems) == 1
    assert problems[0].startswith("mounts must be exactly")


def test_the_walker_fails_on_a_second_mount() -> None:
    application = _throwaway(*_keyed_ok())
    application.mount("/other", FastAPI())

    problems = confinement_violations(application)

    assert problems[0].startswith("mounts must be exactly")


def test_the_walker_fails_on_a_plain_mount_at_the_prefix() -> None:
    application = _throwaway(*_keyed_ok(), mount=False)
    application.router.routes.insert(0, Mount(PARTNER_PREFIX, app=FastAPI()))

    problems = confinement_violations(application)

    assert "/partner/v1 is a plain Mount, not a PartnerMount" in problems
    assert "/partner/v1 does not wrap its app in a PartnerSurface" in problems


def test_the_walker_fails_when_the_mount_is_not_the_first_route() -> None:
    application = _throwaway(*_keyed_ok(), mount=False)
    surface = PartnerSurface(create_partner_api(), enabled=lambda: True)
    application.routes.append(PartnerMount(PARTNER_PREFIX, app=surface))

    assert "the partner mount is not the first route" in confinement_violations(
        application
    )


@SKIP_WITHOUT_VERCEL_JSON
def test_the_walker_fails_on_a_route_a_backend_rewrite_would_reach() -> None:
    rewrites = [
        {"source": "/v1/leaky/(.*)", "destination": {"service": BACKEND_SERVICE}},
        *load_rewrites(),
    ]

    problems = confinement_violations(_throwaway(("/v1/leaky/{id}", True)), rewrites)

    assert problems == ["/v1/leaky/{id} is reached by a backend rewrite in vercel.json"]


def test_the_walker_fails_on_a_dependency_override() -> None:
    application = _throwaway(*_keyed_ok())
    application.dependency_overrides[require_internal_key] = lambda: None

    assert "the application has dependency overrides" in confinement_violations(
        application
    )


def test_the_walker_fails_on_a_low_priority_frontend_route(tmp_path: Path) -> None:
    application = _throwaway(*_keyed_ok())
    application.frontend("/", directory=tmp_path, check_dir=False)

    problems = confinement_violations(application)

    assert any("low-priority (frontend) route" in problem for problem in problems)


# ---------------------------------------------------------------- negative: inside the mount


def _partner_api_with(extra) -> FastAPI:  # type: ignore[no-untyped-def]
    api = create_partner_api()
    extra(api)
    return api


def test_a_partner_sub_app_that_includes_the_health_router_is_a_violation() -> None:
    api = _partner_api_with(lambda a: a.include_router(health.router))

    problems = confinement_violations(_throwaway(*_keyed_ok(), partner_api=api))

    assert any("defined outside app.partner" in problem for problem in problems)
    assert any("uses the internal key" in problem for problem in problems)
    assert (
        any("shares its endpoint with a main-app route" in p for p in problems) is False
    )


def test_a_partner_sub_app_that_includes_the_stripe_router_is_a_violation() -> None:
    api = _partner_api_with(lambda a: a.include_router(webhooks.router))

    problems = confinement_violations(_throwaway(*_keyed_ok(), partner_api=api))

    assert any(
        "/partner/v1/webhooks/stripe" in p and "outside app.partner" in p
        for p in problems
    )


def test_a_partner_route_defined_outside_app_partner_is_a_violation() -> None:
    api = _partner_api_with(lambda a: a.add_api_route("/own", _own_endpoint))

    problems = confinement_violations(_throwaway(*_keyed_ok(), partner_api=api))

    assert problems == [
        "/partner/v1/own inside the partner mount is defined outside app.partner"
    ]


def test_a_partner_route_that_shares_an_endpoint_with_a_main_route_is_a_violation() -> (
    None
):
    def shared() -> None:
        return None

    shared.__module__ = (
        "app.partner.fake"  # passes the module rule, so only the sharing rule fires
    )
    api = _partner_api_with(lambda a: a.add_api_route("/shared", shared))
    application = _throwaway(partner_api=api)
    application.add_api_route(
        "/v1/shared", shared, dependencies=[Depends(require_internal_key)]
    )

    problems = confinement_violations(application)

    assert problems == [
        "/partner/v1/shared inside the partner mount shares its endpoint with a main-app route"
    ]


def test_a_partner_route_with_the_internal_key_is_a_violation() -> None:
    def keyed() -> None:
        return None

    keyed.__module__ = "app.partner.fake"
    api = _partner_api_with(
        lambda a: a.add_api_route(
            "/keyed", keyed, dependencies=[Depends(require_internal_key)]
        )
    )

    problems = confinement_violations(_throwaway(partner_api=api))

    assert problems == [
        "/partner/v1/keyed inside the partner mount uses the internal key"
    ]


def test_a_partner_route_inside_app_partner_is_accepted() -> None:
    def fine() -> None:
        return None

    fine.__module__ = "app.partner.fake"
    api = _partner_api_with(lambda a: a.add_api_route("/fine", fine))

    assert confinement_violations(_throwaway(partner_api=api)) == []


def test_the_walker_descends_into_the_sub_app() -> None:
    def fine() -> None:
        return None

    fine.__module__ = "app.partner.fake"
    api = _partner_api_with(lambda a: a.add_api_route("/fine", fine))

    paths = [path for path, _ in walk(_throwaway(partner_api=api).routes)]

    assert "/partner/v1/fine" in paths
