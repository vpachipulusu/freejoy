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
    client_key,
    limit_by_client_ip,
)
from app.api.v1.catalogue_schemas import ExpiredOfferResponse, expired_response
from app.core.errors import ErrorResponse
from app.core.rate_limit import (
    CLICK_IPV6_GROUP_PREFIX,
    OUTBOUND_CLICK_OFFER_POLICY,
    OUTBOUND_CLICK_POLICY,
    OUTBOUND_OFFER_BOT_POLICY,
    OUTBOUND_OFFER_CAP_SIGNAL_POLICY,
    OUTBOUND_OFFER_POLICY,
    RateLimiter,
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
        Depends(limit_by_client_ip(OUTBOUND_CLICK_POLICY, CLICK_IPV6_GROUP_PREFIX)),
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


def _request_id(request: Request) -> str | None:
    request_id: str | None = request.scope.get("state", {}).get("request_id")
    return request_id


async def _hit_or_allow(
    request: Request,
    limiter: RateLimiter,
    identifier: str,
    policy: RateLimitPolicy,
    outage_message: str,
) -> bool:
    """One recording-limit hit: True while allowed. A limiter outage allows (the policy fails open), with a
    warning that carries the request id and a report, so the click is recorded as usual."""
    try:
        decision = await limiter.hit(identifier, policy)
    except Exception as exc:
        logger.warning(
            outage_message, exc_info=exc, extra={"request_id": _request_id(request)}
        )
        report_exception(exc)
        return True
    return decision.allowed


async def _announce_cap_reached(
    request: Request, limiter: RateLimiter, offer_id: str
) -> None:
    """Log that the main per-offer budget is spent, at most once a minute for the offer (the limiter keeps the
    count). Only for the main bucket. The signal is never worth a failure: if its own hit cannot be answered it
    stays silent, so an outage cannot become one error line per request. No destination, address or user agent."""
    policy = OUTBOUND_OFFER_CAP_SIGNAL_POLICY
    try:
        decision = await limiter.hit(f"{policy.scope}:offer:{offer_id}", policy)
    except Exception:  # noqa: BLE001 - a signal about a cap must never add errors of its own
        return
    if decision.allowed:
        logger.warning(
            "offer recording cap reached",
            extra={
                "offer_id": offer_id,
                "scope": OUTBOUND_OFFER_POLICY.scope,
                "request_id": _request_id(request),
            },
        )


async def offer_has_room(
    request: Request,
    limiter: RateLimiterDep,
    offer_id: OfferIdPath,
    body: ClickBody = None,
) -> bool:
    """The recording limits: True while this click may be recorded. At most two hits, in this order.

    1. ``OUTBOUND_CLICK_OFFER_POLICY``, per client address (IPv6 by /56) and offer, for every device class. When
       it is spent the answer is False and the per-offer bucket is NOT hit, so one address can never use more
       than its few clicks of an offer's budget.
    2. The per-offer bucket: ``OUTBOUND_OFFER_BOT_POLICY`` for a request classed ``BOT``, otherwise
       ``OUTBOUND_OFFER_POLICY``. When the main one is spent, one warning a minute says so.

    The device class is in the body (the route and this dependency share the one body parameter, so both must
    call it ``body``). FastAPI runs the dependency once per request, after the body is validated, so a request
    refused for its body hits neither limit. Past a limit the visitor is still sent on (``record`` is False):
    refusing would punish the visitor for a crowd, while the limits only cap how many clicks are recorded. A
    limiter outage records the click as usual, like the other public routes (the policies fail open).
    """
    address = OUTBOUND_CLICK_OFFER_POLICY
    address_id = (
        f"{address.scope}:ip:{client_key(request, CLICK_IPV6_GROUP_PREFIX)}"
        f":offer:{offer_id}"
    )
    if not await _hit_or_allow(
        request,
        limiter,
        address_id,
        address,
        "click address limiter unavailable, recording the click",
    ):
        return False
    policy = offer_policy_for(body.user_agent_class if body else None)
    allowed = await _hit_or_allow(
        request,
        limiter,
        f"{policy.scope}:offer:{offer_id}",
        policy,
        "offer cap limiter unavailable, recording the click",
    )
    if not allowed and policy is OUTBOUND_OFFER_POLICY:
        await _announce_cap_reached(request, limiter, offer_id)
    return allowed


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
