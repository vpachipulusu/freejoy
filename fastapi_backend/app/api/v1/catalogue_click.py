"""Outbound click: ``POST /v1/catalogue/offers/{offer_id}/click``.

Called only by the Next.js ``/go/[offerId]`` route handler. It records one click event and
returns the stored affiliate destination, or refuses without writing anything:

* 404 ``Offer not found``: unknown, draft, archived, hidden, or no usable affiliate link. The
  body is identical for every one of these, so a caller cannot probe for unpublished offers.
* 410 ``This offer has ended``: only an offer that provably was public (same rule and body as
  ``GET /v1/catalogue/offers/{slug}``); the stub carries the slug so the caller can show the page.

Every response is ``Cache-Control: no-store``. The destination is never logged.
"""

import logging
from collections.abc import Awaitable, Callable
from typing import Annotated, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app.api.auth import require_internal_key
from app.api.deps import SessionDep
from app.api.rate_limit import (
    RATE_LIMIT_RESPONSES,
    RateLimiterDep,
    limit_by_client_ip,
)
from app.api.v1.catalogue_schemas import ExpiredOfferResponse, expired_response
from app.core.config import settings
from app.core.errors import ErrorResponse
from app.core.rate_limit import (
    OUTBOUND_CLICK_POLICY,
    OUTBOUND_OFFER_BOT_POLICY,
    OUTBOUND_OFFER_POLICY,
    RateLimitPolicy,
)
from app.core.sentry import report_exception
from app.models.platform.click_event import UserAgentClass
from app.repositories.catalogue import CatalogueRepository
from app.repositories.outbound_clicks import OutboundClickRepository
from app.services.catalogue import CatalogueService, ExpiredOffer
from app.services.outbound_clicks import MAX_HOST_LENGTH, OutboundClickService

logger = logging.getLogger("yourapp.outbound")
NO_STORE = {"Cache-Control": "no-store"}
OFFER_ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"

router = APIRouter(
    prefix="/v1/catalogue",
    tags=["catalogue"],
    dependencies=[
        Depends(require_internal_key),
        Depends(limit_by_client_ip(OUTBOUND_CLICK_POLICY)),
    ],
    responses={**RATE_LIMIT_RESPONSES, 401: {"model": ErrorResponse}},
)


OfferIdPath = Annotated[str, Path(pattern=OFFER_ID_PATTERN)]


RecordGate = Callable[[UserAgentClass | None], Awaitable[bool]]


def offer_policy_for(user_agent_class: UserAgentClass | None) -> RateLimitPolicy:
    """The per-offer budget a click spends: BOT has its own, everything else (and no class) the main one."""
    if user_agent_class is UserAgentClass.BOT:
        return OUTBOUND_OFFER_BOT_POLICY
    return OUTBOUND_OFFER_POLICY


def offer_recording_gate(
    request: Request, limiter: RateLimiterDep, offer_id: OfferIdPath
) -> RecordGate:
    """Second limit, per offer id: a gate that says whether this offer may still gain a recorded click.

    The device class is in the body, which a dependency does not see, so the route calls the gate once it has the
    class. The gate spends exactly one hit, on the bucket the class selects (``offer_policy_for``), and a
    ``BOT`` request never touches the main budget. Past the cap the visitor is still sent on (``record`` is
    False): refusing would punish the visitor for a crowd, while capping only limits how many clicks one offer
    can add per minute. A limiter outage records the click as usual, like the other public routes (the policy
    fails open).
    """

    async def may_record(user_agent_class: UserAgentClass | None) -> bool:
        policy = offer_policy_for(user_agent_class)
        identifier = f"{policy.scope}:offer:{offer_id}"
        try:
            decision = await limiter.hit(identifier, policy)
        except Exception as exc:
            request_id = request.scope.get("state", {}).get("request_id")
            logger.warning(
                "offer cap limiter unavailable, recording the click",
                exc_info=exc,
                extra={"request_id": request_id},
            )
            report_exception(exc)
            return True
        return decision.allowed

    return may_record


RecordGateDep = Annotated[RecordGate, Depends(offer_recording_gate)]


class ClickRequest(BaseModel):
    """What the Next.js route may tell the backend about the click. Nothing else is accepted."""

    model_config = ConfigDict(extra="forbid")

    referrer_host: Annotated[
        str | None,
        Field(
            max_length=MAX_HOST_LENGTH,
            description="Host name of the referring page; anything else is dropped",
        ),
    ] = None
    user_agent_class: UserAgentClass | None = Field(
        default=None,
        description="Coarse device class; the raw user agent is never sent",
    )


class ClickData(BaseModel):
    destination_url: str = Field(description="Stored https destination; never logged")


class ClickResponse(BaseModel):
    success: Literal[True] = True
    data: ClickData


def get_click_service(session: SessionDep) -> OutboundClickService:
    return OutboundClickService(
        CatalogueService(CatalogueRepository(session)),
        OutboundClickRepository(session),
        session,
        allow_sample_provider=settings.relaxed_environment,
    )


ClickServiceDep = Annotated[OutboundClickService, Depends(get_click_service)]


def _no_store(body: BaseModel, status_code: int = 200) -> Response:
    return Response(
        body.model_dump_json(),
        status_code=status_code,
        media_type="application/json",
        headers=NO_STORE,
    )


@router.post(
    "/offers/{offer_id}/click",
    response_model=ClickResponse,
    summary="Record an outbound click and return the destination",
    responses={
        404: {"model": ErrorResponse, "description": "Not available"},
        410: {"model": ExpiredOfferResponse, "description": "The offer has ended"},
    },
)
async def record_click(
    offer_id: OfferIdPath,
    service: ClickServiceDep,
    may_record: RecordGateDep,
    body: Annotated[ClickRequest | None, Body()] = None,
) -> Response:
    """Write one click event for a live offer and return its affiliate destination."""
    request = body or ClickRequest()
    record = await may_record(request.user_agent_class)
    outcome = await service.record_click(
        offer_id, request.referrer_host, request.user_agent_class, record=record
    )
    if outcome is None:
        raise HTTPException(404, "Offer not found", headers=NO_STORE)
    if isinstance(outcome, ExpiredOffer):
        return _no_store(expired_response(outcome), 410)
    return _no_store(
        ClickResponse(data=ClickData(destination_url=outcome.destination_url))
    )
