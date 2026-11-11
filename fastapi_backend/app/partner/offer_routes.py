"""The partner offer endpoints (ADR-013, FREEJ-58). Paths are relative to the mount (``/offers``, not ``/partner/v1/offers``).

Authentication and the per-key rate limit are on the sub-app (``create_partner_api``), not here. Each route takes
``PartnerContextDep`` for the calling merchant and hands it to ``SubmissionService``, which scopes every query to it.
A write route opens its own session on the restricted role (the one-session-per-request follow-up of FREEJ-57 is not
part of this story).

Errors are a closed set: 404 ``Not Found`` (a ref of another merchant is the same answer as an unknown one), 409
``Conflict`` and 429 ``Too many requests`` and 422 ``Invalid request`` with a stable ``code`` for a rule refusal, a
plain 422 for a body that is not the documented shape, and 503 when the database cannot take the write in time.
Nothing the caller sent is put in a message.
"""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import CodedHTTPException, ErrorResponse
from app.core.rate_limit_enforcement import FAIL_CLOSED_RETRY_AFTER_SECONDS
from app.partner.auth import PartnerContext, PartnerContextDep
from app.partner.deps import get_partner_session
from app.partner.errors import (
    MESSAGE_BUSY,
    MESSAGE_NOT_FOUND,
    SubmissionBusy,
    SubmissionNotFound,
    SubmissionRefused,
)
from app.partner.schemas import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    REF_PATTERN,
    CodedErrorResponse,
    EndResultResponse,
    OfferSubmission,
    SubmissionResponse,
    SubmissionResultResponse,
    SubmissionsPageResponse,
)
from app.partner.submission_service import SubmissionService

logger = logging.getLogger("yourapp.partner")

router = APIRouter(prefix="/offers", tags=["offers"])

# A timeout, a lock timeout, a deadlock or a serialisation failure: the request can simply be sent again.
BUSY_SQLSTATES = frozenset({"55P03", "57014", "40001", "40P01"})
MAX_OFFSET = 1_000_000

RefPath = Annotated[
    str,
    Path(
        pattern=REF_PATTERN,
        description="Your own id for the offer: 1 to 64 letters, digits, dots, underscores or hyphens.",
    ),
]
SessionDep = Annotated[AsyncSession, Depends(get_partner_session)]
REFUSALS: dict[int | str, dict[str, object]] = {
    404: {"model": ErrorResponse, "description": "No offer with this ref"},
    409: {
        "model": CodedErrorResponse,
        "description": "The offer was archived by an editor",
    },
    422: {"model": CodedErrorResponse, "description": "A value the rules refuse"},
    429: {
        "model": CodedErrorResponse,
        "description": "Too many requests or too many submissions",
    },
}


def build_submission_service(
    session: AsyncSession, context: PartnerContext
) -> SubmissionService:
    return SubmissionService(session, context)


def get_service(session: SessionDep, context: PartnerContextDep) -> SubmissionService:
    return build_submission_service(session, context)


ServiceDep = Annotated[SubmissionService, Depends(get_service)]


def _sqlstate(error: DBAPIError) -> str | None:
    orig = error.orig
    state = getattr(orig, "sqlstate", None) or getattr(
        getattr(orig, "__cause__", None), "sqlstate", None
    )
    return state if isinstance(state, str) else None


def _busy() -> HTTPException:
    return HTTPException(
        503, MESSAGE_BUSY, {"Retry-After": str(FAIL_CLOSED_RETRY_AFTER_SECONDS)}
    )


@contextmanager
def answers() -> Iterator[None]:
    """Turn the service's refusals into the fixed responses; anything unexpected stays a 500 (logged, with no input)."""
    try:
        yield
    except SubmissionRefused as refusal:
        raise CodedHTTPException(
            refusal.status_code, refusal.message, refusal.code
        ) from None
    except SubmissionNotFound:
        raise HTTPException(404, MESSAGE_NOT_FOUND) from None
    except SubmissionBusy:
        raise _busy() from None
    except DBAPIError as error:
        if _sqlstate(error) in BUSY_SQLSTATES:
            logger.warning("partner submission write was not taken in time")
            raise _busy() from None
        raise


@router.put(
    "/{ref}",
    response_model=SubmissionResultResponse,
    operation_id="partner-offers-put",
    summary="Create or update an offer submission",
    responses={
        **REFUSALS,
        201: {
            "model": SubmissionResultResponse,
            "description": "A new submission was created",
        },
    },
)
async def put_offer(
    ref: RefPath, body: OfferSubmission, response: Response, service: ServiceDep
) -> SubmissionResultResponse:
    """Send the whole offer. It is stored as a **draft** for an editor to review: nothing you send is ever published
    by the API. The same body twice changes nothing. ``outcome`` says what happened: ``created``; ``refreshed``
    (an editor had not touched the draft yet, so it took the new content); ``pending_update`` (an editor has edited,
    published or ended the offer, so the new content waits for an editor and the published page is unchanged);
    ``unchanged``."""
    with answers():
        result = await service.submit(ref, body)
    if result.outcome == "created":
        response.status_code = 201
    return SubmissionResultResponse(data=result)


@router.get(
    "",
    response_model=SubmissionsPageResponse,
    operation_id="partner-offers-list",
    summary="List your submissions",
)
async def list_offers(
    service: ServiceDep,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0, le=MAX_OFFSET)] = 0,
) -> SubmissionsPageResponse:
    """Your submissions, newest first: status, the public page when live, and whether an update is waiting."""
    with answers():
        return SubmissionsPageResponse(
            data=await service.list_submissions(limit, offset)
        )


@router.get(
    "/{ref}",
    response_model=SubmissionResponse,
    operation_id="partner-offers-get",
    summary="Read one submission",
    responses={404: REFUSALS[404]},
)
async def get_offer(ref: RefPath, service: ServiceDep) -> SubmissionResponse:
    with answers():
        return SubmissionResponse(data=await service.get(ref))


@router.post(
    "/{ref}/end",
    response_model=EndResultResponse,
    operation_id="partner-offers-end",
    summary="End an offer: the sample ran out",
    responses={404: REFUSALS[404]},
)
async def end_offer(ref: RefPath, service: ServiceDep) -> EndResultResponse:
    """Takes effect immediately: the offer's end date becomes now, so the public site and the outbound links already
    treat it as ended. Ending it again changes nothing."""
    with answers():
        return EndResultResponse(data=await service.end(ref))
