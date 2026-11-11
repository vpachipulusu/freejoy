"""Public catalogue: ``/v1/catalogue/offers``, ``/offers/{slug}`` and ``/categories``.

Called only by the Next.js server, so every route needs the internal service key but no user
session. Bodies are identical for every caller and cacheable: ``Cache-Control: private,
max-age=60`` plus a content-hash ``ETag`` answered with 304 on ``If-None-Match``.

Only published, non-expired offers are listed. An offer page for an offer that has ended
answers ``410 Gone`` with ``expired: true`` (see ``docs/developer/backend-catalogue.md``);
drafts, archived offers and unknown slugs all answer the same 404.
"""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, Path, Query, Response
from pydantic import BaseModel

from app.api.auth import require_internal_key
from app.api.deps import SessionDep, allow_sample_provider
from app.api.http_cache import etag_for, matches
from app.api.rate_limit import RATE_LIMIT_RESPONSES, limit_by_client_ip
from app.api.v1.catalogue_schemas import (
    OFFER_TYPE_VALUES,
    CategoryListResponse,
    ExpiredOfferResponse,
    OfferListResponse,
    OfferResponse,
    OfferTypeValue,
    category_data,
    expired_response,
    offer_detail_data,
    offer_list_response,
)
from app.core.errors import CodedHTTPException, ErrorResponse
from app.core.rate_limit import PUBLIC_CATALOGUE_POLICY
from app.models.platform.offer import OfferType
from app.repositories.catalogue import (
    MAX_PAGE_SIZE,
    CatalogueRepository,
    OfferFilters,
    OfferSort,
)
from app.repositories.outbound_clicks import OutboundClickRepository
from app.services.catalogue import CatalogueService, ExpiredOffer
from app.services.offer_commission import OfferCommissionService

CACHE_CONTROL = "private, max-age=60"
# The 404 of an unknown or unpublished offer. The Next.js app prunes a visitor's saved offer only on this code,
# so a route-level "Not Found" (no code) can never be mistaken for it. Never branch on the message.
OFFER_NOT_FOUND = "offer_not_found"
DEFAULT_PAGE_SIZE = 12
MAX_OFFSET = 10_000
MAX_TEXT_LENGTH = 100

router = APIRouter(
    prefix="/v1/catalogue",
    tags=["catalogue"],
    dependencies=[
        Depends(require_internal_key),
        Depends(limit_by_client_ip(PUBLIC_CATALOGUE_POLICY)),
    ],
    responses={**RATE_LIMIT_RESPONSES, 401: {"model": ErrorResponse}},
)


def get_catalogue_service(session: SessionDep) -> CatalogueService:
    return CatalogueService(CatalogueRepository(session))


def get_offer_commission_service(session: SessionDep) -> OfferCommissionService:
    return OfferCommissionService(
        OutboundClickRepository(session),
        allow_sample_provider=allow_sample_provider(),
    )


ServiceDep = Annotated[CatalogueService, Depends(get_catalogue_service)]
CommissionDep = Annotated[OfferCommissionService, Depends(get_offer_commission_service)]
IfNoneMatch = Annotated[str | None, Header(description="ETag from an earlier response")]
NOT_MODIFIED: dict[int | str, dict[str, Any]] = {
    304: {"description": "Not modified (If-None-Match matched)"}
}


def cached_response(
    body: BaseModel, if_none_match: str | None, status_code: int = 200
) -> Response:
    """Serialise ``body`` with the shared cache headers, or answer 304 when it is unchanged."""
    content = body.model_dump_json()
    etag = etag_for(content.encode())
    headers = {"Cache-Control": CACHE_CONTROL, "ETag": etag}
    # Only a 200 can be "not modified"; a 410 must keep telling the caller the offer is gone.
    if status_code == 200 and matches(if_none_match, etag):
        return Response(status_code=304, headers=headers)
    return Response(
        content,
        status_code=status_code,
        media_type="application/json",
        headers=headers,
    )


@router.get(
    "/offers",
    response_model=OfferListResponse,
    summary="List published offers",
    responses=NOT_MODIFIED,
)
async def list_offers(
    service: ServiceDep,
    if_none_match: IfNoneMatch = None,
    q: Annotated[
        str | None,
        Query(
            max_length=MAX_TEXT_LENGTH, description="Matches title, summary or merchant"
        ),
    ] = None,
    category: Annotated[
        str | None,
        Query(
            max_length=MAX_TEXT_LENGTH, description="Category slug; unknown gives none"
        ),
    ] = None,
    offer_types: Annotated[
        list[OfferTypeValue] | None,
        Query(alias="type", max_length=len(OFFER_TYPE_VALUES)),
    ] = None,
    postage: Literal["free"] | None = None,
    sort: Literal["newest", "ending_soon", "a_z"] = "newest",
    featured: bool | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0, le=MAX_OFFSET)] = 0,
) -> Response:
    """Published offers that have not ended, with the total before paging.

    ``type`` may repeat. Without ``sort`` the newest come first.
    """
    filters = OfferFilters(
        query=(q or "").strip() or None,
        category_slug=category or None,
        offer_types=tuple(OfferType(value.upper()) for value in offer_types or ()),
        free_postage_only=postage == "free",
        featured=featured,
        sort=OfferSort(sort),
    )
    page = await service.list_offers(filters, limit, offset)
    return cached_response(offer_list_response(page, limit, offset), if_none_match)


@router.get(
    "/offers/{slug}",
    response_model=OfferResponse,
    summary="Read one published offer",
    responses={
        **NOT_MODIFIED,
        404: {"model": ErrorResponse, "description": "Not published or unknown"},
        410: {"model": ExpiredOfferResponse, "description": "The offer has ended"},
    },
)
async def read_offer(
    slug: Annotated[str, Path(min_length=1, max_length=MAX_TEXT_LENGTH)],
    service: ServiceDep,
    commission: CommissionDep,
    if_none_match: IfNoneMatch = None,
) -> Response:
    """A live offer, with ``earns_commission``. An ended offer answers 410 with ``expired: true`` and its title."""
    offer = await service.get_offer(slug)
    if offer is None:
        raise CodedHTTPException(404, "Offer not found", OFFER_NOT_FOUND)
    if isinstance(offer, ExpiredOffer):
        return cached_response(expired_response(offer), if_none_match, 410)
    earns_commission = await commission.earns_commission(offer)
    body = OfferResponse(data=offer_detail_data(offer, earns_commission))
    return cached_response(body, if_none_match)


@router.get(
    "/categories",
    response_model=CategoryListResponse,
    summary="List enabled categories",
    responses=NOT_MODIFIED,
)
async def list_categories(
    service: ServiceDep, if_none_match: IfNoneMatch = None
) -> Response:
    """Enabled categories in display order, each with its count of published offers."""
    categories = await service.list_categories()
    body = CategoryListResponse(data=[category_data(item) for item in categories])
    return cached_response(body, if_none_match)
