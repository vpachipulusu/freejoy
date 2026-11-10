"""Route confinement (ADR-013): the whole application is walked, mounts included.

The invariant: a caller holding a valid partner key and the public partner URL can invoke only
partner handlers, and can read or change only its own merchant's data. This file proves the
routing half in the normal pytest run, so the gate carries it:

* exactly one mount, at ``/partner/v1``;
* every main-app route needs the internal key, or is on a short explicit allowlist;
* no main-app route sits under ``/partner`` (in any letter case);
* the only main-app route a backend rewrite in ``vercel.json`` can reach is the Stripe webhook.

``confinement_violations`` is also run against throwaway apps that break each rule, so a walker
that stopped looking would fail here instead of passing quietly.

FastAPI 0.143 keeps ``include_router`` routers lazy (``app.routes`` holds one placeholder per
included router), so the walk goes through ``fastapi.routing.iter_route_contexts``, which
flattens them with their final path and dependencies. The version is pinned below 0.144.
"""

import re
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.dependencies.models import Dependant
from fastapi.routing import RouteContext, iter_route_contexts
from starlette.routing import BaseRoute, Mount

from app.api.auth import require_internal_key
from app.core.config import settings
from app.main import app, create_app
from app.partner.app import create_partner_app
from app.partner.mount import PartnerMount
from app.partner.surface import PARTNER_PREFIX
from tests.vercel_rewrites import (
    BACKEND_SERVICE,
    VERCEL_JSON,
    destination_service,
    load_rewrites,
)

pytestmark = pytest.mark.skipif(
    not VERCEL_JSON.exists(), reason="root vercel.json is not in this checkout"
)

STRIPE_WEBHOOK_PATH = "/webhooks/stripe"
# Routes that are deliberately open. Each one has its own credential or none by design.
OPEN_ROUTES = frozenset({"/health", STRIPE_WEBHOOK_PATH})
PATH_PARAMETER = re.compile(r"\{[^}]*\}")


def _carries_internal_key(dependant: Dependant | None) -> bool:
    return dependant is not None and any(
        child.call is require_internal_key or _carries_internal_key(child)
        for child in dependant.dependencies
    )


def _docs_paths(application: FastAPI) -> frozenset[str]:
    """The docs routes this application serves, empty when the docs are switched off."""
    paths = (
        application.docs_url,
        application.redoc_url,
        application.openapi_url,
        application.swagger_ui_oauth2_redirect_url if application.docs_url else None,
    )
    return frozenset(path for path in paths if path)


def _walk(
    routes: list[BaseRoute], prefix: str = ""
) -> Iterator[tuple[str, RouteContext]]:
    """Every route with its full path, descending into mounts."""
    for context in iter_route_contexts(routes):
        path = prefix + (context.path or "")
        yield path, context
        if isinstance(context.original_route, Mount):
            yield from _walk(context.original_route.routes, path)


def _mount_paths(application: FastAPI) -> list[str]:
    return [
        path
        for path, context in _walk(application.routes)
        if isinstance(context.original_route, Mount)
    ]


def _main_app_routes(application: FastAPI) -> list[RouteContext]:
    """The routes of the application itself: everything except the mounts."""
    return [
        context
        for context in iter_route_contexts(application.routes)
        if not isinstance(context.original_route, Mount)
    ]


def confinement_violations(
    application: FastAPI, rewrites: list[dict[str, Any]]
) -> list[str]:
    """Every way ``application`` breaks the confinement rules (empty when it keeps them)."""
    problems: list[str] = []
    if _mount_paths(application) != [PARTNER_PREFIX]:
        problems.append(
            f"mounts must be exactly [{PARTNER_PREFIX}], found {_mount_paths(application)}"
        )
    open_paths = OPEN_ROUTES | _docs_paths(application)
    for context in _main_app_routes(application):
        problems.extend(_main_route_problems(context, open_paths, rewrites))
    return problems


def _main_route_problems(
    context: RouteContext, open_paths: frozenset[str], rewrites: list[dict[str, Any]]
) -> list[str]:
    path = context.path
    if path is None:
        return [f"{context.original_route!r} has no path, so it cannot be checked"]
    problems: list[str] = []
    if path.lower().startswith("/partner"):
        problems.append(f"{path} is a main-app route under /partner")
    if (
        not _carries_internal_key(getattr(context, "dependant", None))
        and path not in open_paths
    ):
        problems.append(f"{path} has no internal key and is not on the allowlist")
    if path != STRIPE_WEBHOOK_PATH and _reaches_backend(path, rewrites):
        problems.append(f"{path} is reached by a backend rewrite in vercel.json")
    return problems


def _reaches_backend(path: str, rewrites: list[dict[str, Any]]) -> bool:
    """True when a backend rewrite catches ``path``, with or without a trailing slash."""
    sample = PATH_PARAMETER.sub("x", path)
    return BACKEND_SERVICE in {
        destination_service(variant, rewrites) for variant in (sample, f"{sample}/")
    }


def _throwaway(*routes: tuple[str, bool], mount: bool = True) -> FastAPI:
    """A small app: ``(path, needs_key)`` routes, and the partner mount unless ``mount`` is false."""
    application = FastAPI()
    router = APIRouter()
    for path, needs_key in routes:
        dependencies = [Depends(require_internal_key)] if needs_key else []
        router.add_api_route(path, lambda: None, dependencies=dependencies)
    application.include_router(router)
    if mount:
        application.routes.append(
            PartnerMount(PARTNER_PREFIX, app=create_partner_app(), name="partner")
        )
    return application


def test_the_real_application_keeps_every_confinement_rule() -> None:
    assert confinement_violations(app, load_rewrites()) == []


def test_the_walker_really_sees_the_included_routers() -> None:
    paths = {context.path for context in _main_app_routes(app)}

    assert {"/health", "/health/ready", STRIPE_WEBHOOK_PATH, "/v1/me"} <= paths
    assert len(paths) > 30


def test_the_rules_hold_with_the_docs_switched_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "VERCEL_ENV", None)
    monkeypatch.setattr(settings, "API_DOCS_ENABLED", False)

    production = create_app()

    assert _docs_paths(production) == frozenset()
    assert confinement_violations(production, load_rewrites()) == []


def test_the_docs_routes_are_allowed_only_while_they_are_enabled() -> None:
    served = {context.path for context in _main_app_routes(app)}

    assert _docs_paths(app) >= {"/docs", "/redoc", "/openapi.json"}
    assert _docs_paths(app) <= served


def test_there_is_exactly_one_mount_at_the_partner_prefix() -> None:
    assert _mount_paths(app) == ["/partner/v1"]


def test_every_allowlisted_route_still_exists() -> None:
    served = {context.path for context in _main_app_routes(app)}

    assert OPEN_ROUTES <= served


def test_the_only_main_app_route_a_backend_rewrite_reaches_is_stripe() -> None:
    rewrites = load_rewrites()

    reached = {
        context.path
        for context in _main_app_routes(app)
        if context.path and _reaches_backend(context.path, rewrites)
    }

    assert reached == {STRIPE_WEBHOOK_PATH}


@pytest.mark.parametrize(
    "path", ["/partner/v1", "/partner/v1/x", "/Partner/x", "/PARTNER", "/partnerx"]
)
def test_no_main_app_route_may_sit_under_partner_in_any_case(path: str) -> None:
    problems = confinement_violations(_throwaway((path, True)), load_rewrites())

    assert any("under /partner" in problem for problem in problems)


def test_the_walker_fails_on_a_route_without_the_internal_key() -> None:
    problems = confinement_violations(_throwaway(("/v1/open", False)), load_rewrites())

    assert problems == ["/v1/open has no internal key and is not on the allowlist"]


def test_the_walker_accepts_a_route_with_the_internal_key() -> None:
    application = _throwaway(("/v1/closed", True))

    assert confinement_violations(application, load_rewrites()) == []


def test_the_walker_fails_when_there_is_no_partner_mount() -> None:
    application = _throwaway(("/v1/closed", True), mount=False)

    problems = confinement_violations(application, load_rewrites())

    assert len(problems) == 1
    assert problems[0].startswith("mounts must be exactly")


def test_the_walker_fails_on_a_second_mount() -> None:
    application = _throwaway(("/v1/closed", True))
    application.mount("/other", FastAPI())

    problems = confinement_violations(application, load_rewrites())

    assert len(problems) == 1
    assert problems[0].startswith("mounts must be exactly")


def test_the_walker_fails_on_a_route_a_backend_rewrite_would_reach() -> None:
    rewrites = [
        {"source": "/v1/leaky/(.*)", "destination": {"service": BACKEND_SERVICE}},
        *load_rewrites(),
    ]

    problems = confinement_violations(_throwaway(("/v1/leaky/{id}", True)), rewrites)

    assert problems == ["/v1/leaky/{id} is reached by a backend rewrite in vercel.json"]
