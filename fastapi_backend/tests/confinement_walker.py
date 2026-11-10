"""The route-confinement walk (ADR-013), shared by the confinement tests.

``confinement_violations`` walks the whole application, mounts included, and lists every way it
breaks the rules below (an empty list means it keeps them). The tests run it on the real application
and on throwaway applications that break each rule, so a walker that stopped looking fails loudly.

FastAPI 0.143 keeps ``include_router`` routers lazy (``app.routes`` holds one placeholder per included
router), so the walk goes through ``fastapi.routing.iter_route_contexts``, which flattens them with
their final path and dependencies, and through the router's low-priority (frontend) routes. Both are
FastAPI internals: the version is pinned below 0.144, and the walk fails loudly if they go away.

Rules:

* exactly one mount, at ``/partner/v1``, a ``PartnerMount`` around a ``PartnerSurface``, and it is the
  first route of the application;
* every main-app route needs ``require_internal_key`` or is on the allowlist; there are no low-priority
  (frontend) routes and no dependency overrides;
* no main-app route sits under ``/partner`` (any letter case), and none can MATCH a path under the
  prefix, whatever its pattern (``/{a}/v1/{b}``, ``/{rest:path}``): each route's own path regex is probed;
* the only main-app route that can match ``/webhooks/stripe``, or that a backend rewrite reaches, is
  the Stripe route;
* every route inside the mount has an endpoint defined in ``app.partner``, shared with no main-app
  route, and does not use the internal key (partners have their own credential).
"""

import re
from collections.abc import Iterator
from typing import Any

from fastapi import FastAPI
from fastapi.dependencies.models import Dependant
from fastapi.routing import RouteContext, iter_route_contexts
from starlette.routing import BaseRoute, Mount

from app.api.auth import require_internal_key
from app.partner.mount import PartnerMount
from app.partner.surface import PARTNER_PREFIX, PartnerSurface
from tests.vercel_rewrites import BACKEND_SERVICE, destination_service

STRIPE_WEBHOOK_PATH = "/webhooks/stripe"
# Routes that are deliberately open. Each one has its own credential or none by design.
OPEN_ROUTES = frozenset({"/health", STRIPE_WEBHOOK_PATH})
PATH_PARAMETER = re.compile(r"\{[^}]*\}")
# Paths a partner caller can send. No main-app route's pattern may match any of them.
PARTNER_PROBES = (
    PARTNER_PREFIX,
    f"{PARTNER_PREFIX}/",
    f"{PARTNER_PREFIX}/x",
    f"{PARTNER_PREFIX}/x/y",
)


def carries_internal_key(dependant: Dependant | None) -> bool:
    return dependant is not None and any(
        child.call is require_internal_key or carries_internal_key(child)
        for child in dependant.dependencies
    )


def context_path(context: RouteContext) -> str | None:
    """The route's path; websocket and similar contexts keep it on their starlette route."""
    path = context.path or getattr(
        getattr(context, "starlette_route", None), "path", None
    )
    return path or None


def docs_paths(application: FastAPI) -> frozenset[str]:
    """The docs routes this application serves, empty when the docs are switched off."""
    paths = (
        application.docs_url,
        application.redoc_url,
        application.openapi_url,
        application.swagger_ui_oauth2_redirect_url if application.docs_url else None,
    )
    return frozenset(path for path in paths if path)


def walk(
    routes: list[BaseRoute], prefix: str = ""
) -> Iterator[tuple[str, RouteContext]]:
    """Every route with its full path, descending into mounts."""
    for context in iter_route_contexts(routes):
        path = prefix + (context_path(context) or "")
        yield path, context
        if isinstance(context.original_route, Mount):
            yield from walk(context.original_route.routes, path)


def mount_paths(application: FastAPI) -> list[str]:
    return [
        path
        for path, context in walk(application.routes)
        if isinstance(context.original_route, Mount)
    ]


def low_priority_contexts(application: FastAPI) -> list[RouteContext]:
    """The router's low-priority (frontend) routes, if this FastAPI version has them."""
    iterate = getattr(application.router, "_iter_low_priority_routes", None)
    if iterate is None:
        return []
    contexts: list[RouteContext] = []
    for item in iterate():
        original = getattr(item, "original_route", None)
        contexts.append(
            RouteContext(item) if original is None else RouteContext(original, item)
        )
    return contexts


def main_app_contexts(application: FastAPI) -> list[RouteContext]:
    """The routes of the application itself: everything except the mounts."""
    return [
        context
        for context in iter_route_contexts(application.routes)
        if not isinstance(context.original_route, Mount)
    ]


def confinement_violations(
    application: FastAPI, rewrites: list[dict[str, Any]] | None = None
) -> list[str]:
    """Every way ``application`` breaks the confinement rules (empty when it keeps them).

    Without ``rewrites`` the ``vercel.json`` rule is not checked; the rest always is.
    """
    problems = _mount_problems(application)
    main = main_app_contexts(application)
    main_endpoints = [context.endpoint for context in main if context.endpoint]
    for route in application.routes:
        if isinstance(route, Mount):
            problems.extend(_inside_mount_problems(route, main_endpoints))
    open_paths = OPEN_ROUTES | docs_paths(application)
    for context in main:
        problems.extend(_main_route_problems(context, open_paths, rewrites))
    for context in low_priority_contexts(application):
        problems.append(
            f"low-priority (frontend) route {context_path(context)!r} is not allowed"
        )
    if application.dependency_overrides:
        problems.append("the application has dependency overrides")
    return problems


def _mount_problems(application: FastAPI) -> list[str]:
    problems: list[str] = []
    found = mount_paths(application)
    if found != [PARTNER_PREFIX]:
        problems.append(f"mounts must be exactly [{PARTNER_PREFIX}], found {found}")
    mounts = [r for r in application.routes if isinstance(r, Mount)]
    for mount in mounts:
        if not isinstance(mount, PartnerMount):
            problems.append(f"{mount.path} is a plain Mount, not a PartnerMount")
        if not isinstance(mount.app, PartnerSurface):
            problems.append(f"{mount.path} does not wrap its app in a PartnerSurface")
    if mounts and application.routes[0] is not mounts[0]:
        problems.append("the partner mount is not the first route")
    return problems


def _inside_mount_problems(mount: Mount, main_endpoints: list[Any]) -> list[str]:
    problems: list[str] = []
    for path, context in walk(mount.routes, mount.path):
        if isinstance(context.original_route, Mount):
            continue  # counted by the mount rule
        endpoint = context.endpoint
        if endpoint is None:
            problems.append(f"{path} inside the partner mount has no endpoint")
            continue
        if not getattr(endpoint, "__module__", "").startswith("app.partner."):
            problems.append(
                f"{path} inside the partner mount is defined outside app.partner"
            )
        if any(endpoint is other for other in main_endpoints):
            problems.append(
                f"{path} inside the partner mount shares its endpoint with a main-app route"
            )
        if carries_internal_key(getattr(context, "dependant", None)):
            problems.append(f"{path} inside the partner mount uses the internal key")
    return problems


def _main_route_problems(
    context: RouteContext,
    open_paths: frozenset[str],
    rewrites: list[dict[str, Any]] | None,
) -> list[str]:
    path = context_path(context)
    if path is None:
        return [f"{context.original_route!r} has no path, so it cannot be checked"]
    problems: list[str] = []
    if path.lower().startswith("/partner"):
        problems.append(f"{path} is a main-app route under /partner")
    problems.extend(_probe_problems(context, path))
    keyed = carries_internal_key(getattr(context, "dependant", None))
    if not keyed and path not in open_paths:
        problems.append(f"{path} has no internal key and is not on the allowlist")
    if (
        rewrites is not None
        and path != STRIPE_WEBHOOK_PATH
        and reaches_backend(path, rewrites)
    ):
        problems.append(f"{path} is reached by a backend rewrite in vercel.json")
    return problems


def _probe_problems(context: RouteContext, path: str) -> list[str]:
    """Match the route's own regex against the paths it must never answer."""
    regex = getattr(context, "path_regex", None)
    if regex is None:
        return [f"{path} has no path regex, so it cannot be probed"]
    problems = [
        f"{path} can match {probe}" for probe in PARTNER_PROBES if regex.match(probe)
    ]
    if path != STRIPE_WEBHOOK_PATH and regex.match(STRIPE_WEBHOOK_PATH):
        problems.append(f"{path} can match {STRIPE_WEBHOOK_PATH}")
    return problems


def reaches_backend(path: str, rewrites: list[dict[str, Any]]) -> bool:
    """True when a backend rewrite catches ``path``, with or without a trailing slash."""
    sample = PATH_PARAMETER.sub("x", path)
    return BACKEND_SERVICE in {
        destination_service(variant, rewrites) for variant in (sample, f"{sample}/")
    }
