"""The partner mount (ADR-013): everything under ``/partner/v1`` goes to the surface, by string match.

Three ways a request could slip past a plain Starlette ``Mount`` and be answered by a main-app route
(or the router's own 404) without the surface headers; each is closed by ``PartnerMount`` and by mounting
it first, and each is shown here.
"""

import pytest
from fastapi import APIRouter, Depends, FastAPI
from starlette.routing import Match, Mount

from app.api.auth import require_internal_key
from app.core.config import settings
from app.main import app, create_app
from app.partner.app import create_partner_app, install_partner_surface
from app.partner.mount import PartnerMount
from app.partner.surface import PARTNER_PREFIX
from tests.api.partner.support import SURFACE_HEADERS, build_scope, call_raw


@pytest.fixture(params=[False, True], ids=["disabled", "enabled"])
def switch(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> bool:
    monkeypatch.setattr(settings, "PARTNER_API_ENABLED", request.param)
    return bool(request.param)


def _is_partner_404(response_headers: list[tuple[str, str]], status: int) -> bool:
    headers = dict(response_headers)
    return status == 404 and all(
        headers.get(k) == v for k, v in SURFACE_HEADERS.items()
    )


def _keyed_app(route: str, *, mount_first: bool) -> FastAPI:
    """A main app with one keyed route, the partner mount put first (as ``create_app`` does) or last."""
    application = FastAPI()
    router = APIRouter()
    router.add_api_route(
        route, lambda: None, dependencies=[Depends(require_internal_key)]
    )
    application.include_router(router)
    if mount_first:
        install_partner_surface(application)
    else:
        application.routes.append(
            PartnerMount(PARTNER_PREFIX, app=create_partner_app(), name="partner")
        )
    return application


def test_the_real_application_mounts_the_surface_first() -> None:
    assert isinstance(create_app().router.routes[0], PartnerMount)
    assert isinstance(app.router.routes[0], PartnerMount)


def test_the_string_match_takes_what_starlettes_regex_mount_does_not() -> None:
    scope = build_scope("GET", "/partner/v1/a\nb")

    assert Mount(PARTNER_PREFIX, app=FastAPI()).matches(scope)[0] is Match.NONE
    assert PartnerMount(PARTNER_PREFIX, app=FastAPI()).matches(scope)[0] is Match.FULL


@pytest.mark.parametrize(
    "path",
    [
        "/partner/v1",
        "/partner/v1/",
        "/partner/v1/x",
        "/partner/v1/a\nb",
        "/partner/v1//x",
    ],
)
def test_everything_under_the_prefix_matches(path: str) -> None:
    mount = PartnerMount(PARTNER_PREFIX, app=FastAPI())

    assert mount.matches(build_scope("GET", path))[0] is Match.FULL


@pytest.mark.parametrize(
    "path",
    [
        "/partner/v1x",
        "/partner/v2/x",
        "/partner",
        "/Partner/v1/x",
        "/x/partner/v1",
        "/",
    ],
)
def test_nothing_else_matches(path: str) -> None:
    mount = PartnerMount(PARTNER_PREFIX, app=FastAPI())

    assert mount.matches(build_scope("GET", path))[0] is Match.NONE


def test_the_child_scope_stacks_the_root_path_like_a_starlette_mount() -> None:
    mount = PartnerMount(PARTNER_PREFIX, app=FastAPI())
    scope = build_scope("GET", "/api/partner/v1/x", root_path="/api")

    match, child = mount.matches(scope)

    assert match is Match.FULL
    assert child["root_path"] == "/api/partner/v1"
    assert child["app_root_path"] == "/api"
    assert child["endpoint"] is mount.app


def test_a_websocket_is_taken_and_a_lifespan_is_not() -> None:
    mount = PartnerMount(PARTNER_PREFIX, app=FastAPI())

    assert (
        mount.matches({"type": "websocket", "path": "/partner/v1/x"})[0] is Match.FULL
    )
    assert mount.matches({"type": "lifespan"})[0] is Match.NONE


@pytest.mark.parametrize(
    "path",
    [
        "/partner/v1/health",
        "/partner/v1/v1/me",
        "/partner/v1/docs",
        "/partner/v1/openapi.json",
    ],
)
async def test_a_root_path_of_the_prefix_cannot_move_a_request_into_a_main_app_route(
    switch: bool, path: str
) -> None:
    """A runtime that serves the app under ``root_path=/partner/v1`` strips it before routing, so
    ``/partner/v1/health`` looks like ``/health``. The mount also compares the full path."""
    response = await call_raw(app, "GET", path, root_path=PARTNER_PREFIX)

    assert _is_partner_404(response.headers, response.status)
    assert response.json() == {"success": False, "error": "Not Found"}


@pytest.mark.parametrize("route", ["/{a}/v1/{b}", "/{rest:path}", "/partner/{b}/{c}"])
@pytest.mark.parametrize("path", ["/partner/v1/x", "/partner/v1/x/y"])
async def test_a_main_app_route_that_matches_the_prefix_never_gets_the_request(
    route: str, path: str
) -> None:
    application = _keyed_app(route, mount_first=True)

    response = await call_raw(application, "GET", path)

    assert _is_partner_404(response.headers, response.status)


async def test_a_mount_added_last_would_lose_to_such_a_route() -> None:
    """Why the mount goes first: appended, the main-app route answers (401, no surface header)."""
    application = _keyed_app("/{a}/v1/{b}", mount_first=False)

    response = await call_raw(application, "GET", "/partner/v1/x")

    assert response.status == 401
    assert "x-freejoy-surface" not in response.header_names()
