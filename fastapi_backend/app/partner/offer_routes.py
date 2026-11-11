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
from app.partner.write_budget import enforce_merchant_write_budget

logger = logging.getLogger("yourapp.partner")

router = APIRouter(prefix="/offers", tags=["offers"])

# A timeout, a lock timeout, a deadlock or a serialisation failure: the request can simply be sent again.
BUSY_SQLSTATES = frozenset({"55P03", "57014", "40001", "40P01"})
MAX_OFFSET = 50_000

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
    dependencies=[Depends(enforce_merchant_write_budget)],
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
    """Send the whole offer. It is stored for our editors to review: nothing you send is ever published by this API,
    and an editor decides whether it goes live. Send the same body again and nothing changes; send changed content and
    the answer's `outcome` says what happened (see the field's description).

    `ref` is your own id for the offer. Two merchants may use the same ref; yours are only ever yours.

    A `PUT` on a ref one of our editors has archived is always a 409 (`offer_closed`); that is permanent, so submit
    again under a new ref. New refs are limited to 50 offers waiting for review (`open_submission_cap`, which clears
    only as our editors review them) and 20 new offers a day (`daily_submission_cap`); updating an existing ref is
    never refused for either. Each merchant has 2,000 writes a day across all its keys (`daily_write_budget`).
    """
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
    """Your submissions, newest first, with each one's status (pending_review, live, ended or declined), the public
    page while it is live and whether an update is waiting for our editors."""
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
    """One of your submissions. A ref that is not yours is answered exactly like one that does not exist (404)."""
    with answers():
        return SubmissionResponse(data=await service.get(ref))


@router.post(
    "/{ref}/end",
    response_model=EndResultResponse,
    operation_id="partner-offers-end",
    summary="End an offer: the sample ran out",
    dependencies=[Depends(enforce_merchant_write_budget)],
    responses={404: REFUSALS[404]},
)
async def end_offer(ref: RefPath, service: ServiceDep) -> EndResultResponse:
    """Use this when the sample has run out. The offer's end date becomes now, so it stops being offered at once.

    Ending cannot be undone by this API: sending the offer again without a future `end_at` does not reopen it, and
    an update that was waiting for our editors when you ended the offer is discarded. Calling it again changes
    nothing (`changed` is false).
    """
    with answers():
        return EndResultResponse(data=await service.end(ref))
