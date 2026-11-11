"""A body the parser rejects is the same plain 422 as one that fails validation (ADR-013, FREEJ-58).

FastAPI answers a body it cannot parse (invalid UTF-8, nesting too deep, a huge integer) with ``400`` and its own text,
which is outside the closed set of messages the partner surface speaks. The sub-app maps that 400 to the plain
``422 Invalid request``; every other error keeps the shared handler.
"""

from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request
from starlette.responses import Response

from app.core.errors import (
    INVALID_REQUEST_MESSAGE,
    error_response,
    http_exception_handler,
)


async def partner_http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> Response:
    if exc.status_code == 400:
        return error_response(422, INVALID_REQUEST_MESSAGE)
    return await http_exception_handler(request, exc)
