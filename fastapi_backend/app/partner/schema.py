"""The partner OpenAPI document (the committed copy is ``docs/partner/openapi.json``)."""

from typing import Any

from fastapi import FastAPI

from app.partner.surface import PARTNER_PREFIX


def partner_openapi_schema(api: FastAPI) -> dict[str, Any]:
    """The sub-app's schema with every path written as a retailer calls it (``/partner/v1/...``).

    The sub-app knows its paths relative to the mount; the retailer-facing contract must show the
    full ones. ``api.openapi()`` is cached by FastAPI, so a new dict is returned and it is left alone.
    """
    schema: dict[str, Any] = api.openapi()
    return {
        **schema,
        "paths": {
            f"{PARTNER_PREFIX}{path}": item for path, item in schema["paths"].items()
        },
    }
