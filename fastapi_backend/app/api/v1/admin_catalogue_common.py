"""Pieces shared by the admin categories, merchants and offers routers.

Errors keep the standard envelope and add a stable ``code`` (``CatalogueErrorResponse``): the
admin UI branches on ``code``, never on the message. Messages are fixed strings; no input
value, id or provider text is echoed back.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Response
from pydantic import StringConstraints

from app.api.auth import require_internal_key
from app.api.rate_limit import limit_by_subject
from app.api.v1.auth import current_subject
from app.core.errors import CodedHTTPException, ErrorResponse
from app.core.rate_limit import API_POLICY
from app.services.admin_catalogue_errors import (
    CatalogueError,
    CatalogueNotFound,
    IllegalTransition,
    OfferChanged,
    PublishBlocked,
)

# No control characters at all (C0, DEL, C1, line and paragraph separators).
LINE_PATTERN = r"^[^\x00-\x1f\x7f-\x9f\u2028\u2029]+$"
# Paragraph text may also contain tab, line feed and carriage return.
PARAGRAPH_PATTERN = r"^[^\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\u2028\u2029]+$"
MAX_URL_INPUT = 2048

ResourceId = Annotated[
    str, Path(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
]
IdText = Annotated[
    str, StringConstraints(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
]
SlugText = Annotated[str, StringConstraints(min_length=2, max_length=80)]
UrlText = Annotated[str, StringConstraints(min_length=1, max_length=MAX_URL_INPUT)]
TintText = Annotated[
    str, StringConstraints(min_length=1, max_length=32, pattern=r"^[a-z0-9_-]+$")
]


def line(max_length: int) -> StringConstraints:
    """Single-line, trimmed, non-empty text."""
    return StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=max_length,
        pattern=LINE_PATTERN,
    )


def paragraph(max_length: int) -> StringConstraints:
    """Multi-line, trimmed, non-empty text."""
    return StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=max_length,
        pattern=PARAGRAPH_PATTERN,
    )


class CatalogueErrorResponse(ErrorResponse):
    """Error body of the admin catalogue routes: the envelope plus a stable ``code``."""

    code: str | None = None


ERROR_DOCS: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse},
    403: {"model": ErrorResponse},
    404: {"model": CatalogueErrorResponse},
    409: {"model": CatalogueErrorResponse},
    422: {"model": CatalogueErrorResponse, "description": "Invalid request"},
}

NOT_FOUND_MESSAGES = {
    "offer": "Offer not found",
    "link": "Affiliate link not found",
    "category": "Category not found",
    "merchant": "Merchant not found",
}
INVALID_MESSAGES = {
    "invalid_url": "Supply a valid https URL",
    "invalid_slug": "Supply a valid slug",
    "invalid_postage": "Postage amount and currency must match the postage kind",
    "invalid_window": "The end date must be after the start date",
    "unknown_category": "The category does not exist",
    "unknown_merchant": "The merchant does not exist",
    "unknown_provider": "The provider is not recognised",
    "destination_not_allowed": "Destination host is not allowed for this provider",
    "review_token_required": "Send the review token from the offer you reviewed",
}
SLUG_TAKEN = "That slug is already in use"
ILLEGAL_TRANSITION = "Operation not allowed in the current state"
OFFER_CHANGED = "The offer changed after it was loaded: review it again"
PUBLISH_BLOCKED = "The offer cannot be published yet"
PUBLISH_BLOCKERS = {
    "category_disabled": "its category is disabled",
    "merchant_inactive": "its merchant is inactive",
    "no_destination": "it has no active affiliate link or source URL",
    "end_date_passed": "its end date has passed",
    "not_verified": "it has not been verified",
}


def catalogue_http_error(exc: CatalogueError) -> HTTPException:
    """The HTTP form of a domain error: 404, 409 or 422, always with its ``code``."""
    if isinstance(exc, CatalogueNotFound):
        return CodedHTTPException(
            404, NOT_FOUND_MESSAGES.get(exc.resource, "Not found"), exc.code
        )
    if isinstance(exc, IllegalTransition):
        return CodedHTTPException(409, ILLEGAL_TRANSITION, exc.code)
    if isinstance(exc, OfferChanged):
        return CodedHTTPException(409, OFFER_CHANGED, exc.code)
    if isinstance(exc, PublishBlocked):
        reasons = "; ".join(
            PUBLISH_BLOCKERS.get(reason, reason) for reason in exc.reasons
        )
        return CodedHTTPException(422, f"{PUBLISH_BLOCKED}: {reasons}", exc.code)
    if exc.code == "slug_taken":
        return CodedHTTPException(409, SLUG_TAKEN, exc.code)
    return CodedHTTPException(
        422, INVALID_MESSAGES.get(exc.code, "Invalid request"), exc.code
    )


def no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


def admin_router(prefix: str, tag: str) -> APIRouter:
    """Router behind the internal key and the per-admin rate limit, like ``admin_users``."""
    return APIRouter(
        prefix=prefix,
        tags=[tag],
        dependencies=[
            Depends(require_internal_key),
            Depends(limit_by_subject(API_POLICY, current_subject)),
        ],
        responses=ERROR_DOCS,
    )
