"""A database that cannot answer is a 503, not a 500 (ADR-013).

Authentication already answers a fixed 503 with ``Retry-After`` when the database fails (``app.partner.auth``). A
route can fail the same way later: the role's statement timeout cancels a query, a connection is refused because the
role's connection limit is reached, or the partner role is refused. One handler on the sub-app gives every partner
route (the ones added later included) the same answer, instead of the shared 500 with a traceback that holds the SQL
and its parameters.

The log line holds the key id and the class name of the error and nothing else: no ``exc_info``, no statement, no
parameter, so no merchant id or visitor data reaches the logs through it.
"""

import logging

from fastapi import FastAPI, Request
from sqlalchemy.exc import DBAPIError
from starlette.responses import Response

from app.core.database import PartnerDatabaseUnavailable, PartnerRoleNotRestricted
from app.partner.auth import CONTEXT_SCOPE_KEY, PartnerContext, unavailable

logger = logging.getLogger("yourapp.partner")

# What "the database cannot answer" looks like from a route: the driver's error (a timeout, a refused connection, a
# failed statement), an operating system error on the connection, and the partner session provider's own refusals.
UNAVAILABLE_ERRORS: tuple[type[Exception], ...] = (
    DBAPIError,
    OSError,
    PartnerDatabaseUnavailable,
    PartnerRoleNotRestricted,
)


async def database_unavailable_handler(request: Request, exc: Exception) -> Response:
    context = request.scope.get(CONTEXT_SCOPE_KEY)
    key_id = context.key_id if isinstance(context, PartnerContext) else "unknown"
    logger.error(
        "partner database unavailable: key_id=%s error=%s", key_id, type(exc).__name__
    )
    return unavailable()


def register_failure_handlers(api: FastAPI) -> None:
    for error in UNAVAILABLE_ERRORS:
        api.add_exception_handler(error, database_unavailable_handler)
