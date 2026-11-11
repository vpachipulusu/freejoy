"""The partner route table is pinned, not derived (ADR-013, FREEJ-59).

Other tests take the partner routes from the real sub-app, so a route that is not in the schema (``include_in_schema=
False``), or a raw ``Route`` or ``Mount`` appended to the sub-app, would pass all of them: authenticated, but
undocumented and unreviewed. This file states the route table in plain text. Adding a partner endpoint means editing
``EXPECTED_ROUTES`` here on purpose, in the same change as its schema, documentation and tests.
"""

from fastapi import FastAPI
from fastapi.routing import APIRoute, iter_route_contexts

from app.partner.app import create_partner_api
from app.partner.schema import partner_openapi_schema
from app.partner.surface import PARTNER_PREFIX

EXPECTED_ROUTES = [("/clicks", ["GET"]), ("/me", ["GET"])]


def route_table(api: FastAPI) -> list[tuple[str | None, list[str]]]:
    """Every route of the sub-app, whatever its kind, as ``(path, sorted methods)``."""
    return sorted(
        (context.path, sorted(getattr(context, "methods", None) or []))
        for context in iter_route_contexts(api.routes)
    )


def route_types(api: FastAPI) -> set[type]:
    return {type(context.route) for context in iter_route_contexts(api.routes)}


def paths_missing_from_the_schema(api: FastAPI) -> set[str]:
    """Route paths (relative to the mount) that the partner schema does not list."""
    documented = {
        path.removeprefix(PARTNER_PREFIX)
        for path in partner_openapi_schema(api)["paths"]
    }
    return {str(path) for path, _ in route_table(api)} - documented


def test_the_partner_sub_app_has_exactly_these_routes_and_methods() -> None:
    assert route_table(create_partner_api()) == EXPECTED_ROUTES


def test_every_route_is_an_api_route() -> None:
    assert route_types(create_partner_api()) == {APIRoute}


def test_every_route_of_the_sub_app_is_in_the_schema_and_nothing_else_is() -> None:
    api = create_partner_api()
    documented = {
        path.removeprefix(PARTNER_PREFIX)
        for path in partner_openapi_schema(api)["paths"]
    }

    assert paths_missing_from_the_schema(api) == set()
    assert documented == {path for path, _ in EXPECTED_ROUTES}


def test_a_route_kept_out_of_the_schema_is_reported() -> None:
    api = create_partner_api()

    @api.get("/probe", include_in_schema=False)
    async def probe() -> dict[str, bool]:
        return {"ok": True}

    assert paths_missing_from_the_schema(api) == {"/probe"}
    assert route_table(api) != EXPECTED_ROUTES


def test_a_raw_route_added_to_the_sub_app_changes_the_table() -> None:
    from starlette.responses import PlainTextResponse
    from starlette.routing import Mount, Route

    api = create_partner_api()
    api.router.routes.append(Route("/raw", lambda request: PlainTextResponse("x")))
    api.router.routes.append(Mount("/mounted", app=PlainTextResponse("x")))

    assert route_table(api) != EXPECTED_ROUTES
    assert route_types(api) != {APIRoute}
