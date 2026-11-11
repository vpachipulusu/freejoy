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
from typing import Annotated, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app.api.auth import require_internal_key
from app.api.deps import SessionDep, allow_sample_provider
from app.api.rate_limit import (
    RATE_LIMIT_RESPONSES,
    RateLimiterDep,
    limit_by_client_ip,
)
from app.api.v1.catalogue_schemas import ExpiredOfferResponse, expired_response
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
from app.services.outbound_clicks import (
    MAX_HOST_LENGTH,
    DisclosureNeeded,
    OutboundClickService,
)

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
    commission_disclosed: bool = Field(
        default=True,
        description=(
            "False when the page the visitor clicked on showed no commission disclosure. If the offer "
            "earns commission the answer is 409 disclosure_required and nothing is recorded. It can "
            "only make the answer stricter."
        ),
    )


ClickBody = Annotated[ClickRequest | None, Body()]


def offer_policy_for(user_agent_class: UserAgentClass | None) -> RateLimitPolicy:
    """The per-offer budget a click spends: BOT has its own, everything else (and no class) the main one."""
    if user_agent_class == UserAgentClass.BOT:
        return OUTBOUND_OFFER_BOT_POLICY
    return OUTBOUND_OFFER_POLICY


async def offer_has_room(
    request: Request,
    limiter: RateLimiterDep,
    offer_id: OfferIdPath,
    body: ClickBody = None,
) -> bool:
    """Second limit, per offer id: True while this offer may still gain a recorded click this minute.

    The device class is in the body (the route and this dependency share the one body parameter, so both must
    call it ``body``). A request classed ``BOT`` spends ``OUTBOUND_OFFER_BOT_POLICY`` and never the main
    budget; every other class, and no class, spends ``OUTBOUND_OFFER_POLICY``. FastAPI runs the dependency once
    per request, after the body is validated, so a request refused for its body spends no offer budget.
    Past the cap the visitor is still sent on (``record`` is False): refusing would punish the visitor for a
    crowd, while capping only limits how many clicks one offer can add per minute. A limiter outage records the
    click as usual, like the other public routes (the policy fails open).
    """
    policy = offer_policy_for(body.user_agent_class if body else None)
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


RecordAllowed = Annotated[bool, Depends(offer_has_room)]


class ClickData(BaseModel):
    destination_url: str = Field(description="Stored https destination; never logged")


class ClickResponse(BaseModel):
    success: Literal[True] = True
    data: ClickData


class DisclosureRequiredData(BaseModel):
    slug: str


class DisclosureRequiredResponse(BaseModel):
    """The 409 body: the page did not disclose a commission, so send the visitor back to ``slug``."""

    success: Literal[False] = False
    error: str = "Disclosure required"
    code: Literal["disclosure_required"] = "disclosure_required"
    data: DisclosureRequiredData


def get_click_service(session: SessionDep) -> OutboundClickService:
    return OutboundClickService(
        CatalogueService(CatalogueRepository(session)),
        OutboundClickRepository(session),
        session,
        allow_sample_provider=allow_sample_provider(),
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
        409: {
            "model": DisclosureRequiredResponse,
            "description": "The page did not disclose a commission this offer earns",
        },
    },
)
async def record_click(
    offer_id: OfferIdPath,
    service: ClickServiceDep,
    record: RecordAllowed,
    body: ClickBody = None,
) -> Response:
    """Write one click event for a live offer and return its affiliate destination."""
    request = body or ClickRequest()
    outcome = await service.record_click(
        offer_id,
        request.referrer_host,
        request.user_agent_class,
        record=record,
        commission_disclosed=request.commission_disclosed,
    )
    if outcome is None:
        raise HTTPException(404, "Offer not found", headers=NO_STORE)
    if isinstance(outcome, ExpiredOffer):
        return _no_store(expired_response(outcome), 410)
    if isinstance(outcome, DisclosureNeeded):
        body_409 = DisclosureRequiredResponse(
            data=DisclosureRequiredData(slug=outcome.slug)
        )
        return _no_store(body_409, 409)
    return _no_store(
        ClickResponse(data=ClickData(destination_url=outcome.destination_url))
    )
