"""Partner authentication is attached to the sub-app, not to individual routes (ADR-013).

A route added to the partner sub-app cannot forget it: the dependency is on the app, every route has it in
its dependency tree, and no partner route reaches the internal credentials (``require_internal_key``,
``get_session_identity``) or the forwarded-client-IP helpers. The checks are functions so that throwaway apps
can show they fail on a bad one.
"""

from collections.abc import Callable
from typing import Annotated, Any

from fastapi import Depends, FastAPI
from fastapi.dependencies.models import Dependant
from fastapi.routing import iter_route_contexts

from app.api.auth import (
    get_current_identity,
    get_session_identity,
    require_internal_key,
)
from app.api.rate_limit import client_ip, client_key, forwarded_client_ip
from app.main import app
from app.partner.app import create_partner_api
from app.partner.auth import require_partner_key
from app.partner.limits import limit_partner_requests
from app.partner.mount import PartnerMount

FORBIDDEN_CALLS = (
    require_internal_key,
    get_session_identity,
    get_current_identity,
    client_ip,
    client_key,
    forwarded_client_ip,
)


def tree_calls(dependant: Dependant | None) -> list[Callable[..., Any]]:
    """Every callable in a dependency tree."""
    if dependant is None:
        return []
    calls: list[Callable[..., Any]] = []
    for child in dependant.dependencies:
        if child.call is not None:
            calls.append(child.call)
        calls.extend(tree_calls(child))
    return calls


def app_level_calls(api: FastAPI) -> list[Callable[..., Any]]:
    return [d.dependency for d in api.router.dependencies if d.dependency is not None]


def attachment_problems(api: FastAPI) -> list[str]:
    """Every way ``api`` fails to carry partner authentication and a rate limit on the app itself."""
    problems: list[str] = []
    attached = app_level_calls(api)
    for required in (require_partner_key, limit_partner_requests):
        if required not in attached:
            problems.append(f"{required.__name__} is not a dependency of the sub-app")
    for context in iter_route_contexts(api.routes):
        path = context.path or "?"
        calls = tree_calls(getattr(context, "dependant", None))
        if require_partner_key not in calls:
            problems.append(f"{path} has no require_partner_key in its tree")
        for forbidden in FORBIDDEN_CALLS:
            if forbidden in calls:
                problems.append(f"{path} uses {forbidden.__name__}")
    return problems


def mounted_partner_api() -> FastAPI:
    mount = next(r for r in app.routes if isinstance(r, PartnerMount))
    api = mount.app.app  # type: ignore[attr-defined]
    assert isinstance(api, FastAPI)
    return api


def test_the_dependency_is_on_the_sub_app_and_every_route_has_it_in_its_tree() -> None:
    assert attachment_problems(mounted_partner_api()) == []
    assert attachment_problems(create_partner_api()) == []


def test_the_proof_looks_at_routes_at_all() -> None:
    paths = [c.path for c in iter_route_contexts(mounted_partner_api().routes)]

    assert "/me" in paths


def test_the_proof_fails_on_a_sub_app_without_the_dependency() -> None:
    bare = FastAPI()

    @bare.get("/open")
    async def open_route() -> None:
        return None

    problems = attachment_problems(bare)

    assert "require_partner_key is not a dependency of the sub-app" in problems
    assert "limit_partner_requests is not a dependency of the sub-app" in problems
    assert "/open has no require_partner_key in its tree" in problems


def test_the_proof_fails_on_a_route_that_uses_the_internal_key() -> None:
    api = create_partner_api()

    @api.get("/internal", dependencies=[Depends(require_internal_key)])
    async def internal() -> None:
        return None

    assert "/internal uses require_internal_key" in attachment_problems(api)


def test_the_proof_fails_on_a_route_that_uses_a_session_identity() -> None:
    api = create_partner_api()

    @api.get("/session")
    async def session(_identity: Annotated[Any, Depends(get_session_identity)]) -> None:
        return None

    assert "/session uses get_session_identity" in attachment_problems(api)


def test_the_proof_fails_on_a_route_that_reads_the_forwarded_client_ip() -> None:
    api = create_partner_api()

    @api.get("/ip")
    async def ip(_ip: Annotated[Any, Depends(forwarded_client_ip)]) -> None:
        return None

    assert "/ip uses forwarded_client_ip" in attachment_problems(api)
