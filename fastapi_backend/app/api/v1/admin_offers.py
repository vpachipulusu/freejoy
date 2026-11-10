"""Admin offers API: list, create, read, update, publish lifecycle, verify and affiliate links.

Permissions: ``offers.read`` for reads, ``offers.write`` for create, update and links,
``offers.publish`` for publish, unpublish, expire, archive, verify and dismissing a source
change (they change what the public sees, the "Last checked" claim or the review queue).
The offer read (and every write's response) returns a ``review_token``; ``verify``,
``publish`` and ``dismiss-source-change`` require the one the editor loaded and answer 409
``offer_changed`` when the offer or its links changed since (the other transitions do not
need it; dismissing also sends the ``source_changed_at`` the editor saw, which the token does
not cover). Every write stages its audit row in the same transaction
(``AdminOfferService``). Status table: ``app.services.offer_lifecycle``.
"""

from typing import Annotated

from fastapi import Depends, Query, Response

from app.api.admin import AuditorDep, require_admin
from app.api.deps import SessionDep
from app.api.v1.admin_catalogue_common import (
    IdText,
    ResourceId,
    admin_router,
    catalogue_http_error,
    no_store,
)
from app.api.v1.admin_offers_schemas import (
    CreateLinkRequest,
    CreateOfferRequest,
    DismissSourceChangeRequest,
    LinkResponse,
    OfferResponse,
    OffersPage,
    OffersPageResponse,
    OfferWriteResponse,
    PublishRequest,
    UpdateLinkRequest,
    UpdateOfferRequest,
    VerifyRequest,
    link_out,
    offer_out,
    offer_write_out,
    summary_out,
)
from app.core.permissions import AdminContext
from app.models.platform.offer import CommercialBasis, OfferStatus
from app.repositories.admin_offers import AdminOfferRepository, OfferFilters
from app.services.admin_catalogue_common import MAX_PAGE_SIZE
from app.services.admin_catalogue_errors import CatalogueError
from app.services.admin_offers import AdminOfferService, OfferDetail
from app.services.offer_lifecycle import OfferAction

router = admin_router("/v1/admin/offers", "admin-offers")

ReadAdmin = Annotated[AdminContext, Depends(require_admin("offers.read"))]
WriteAdmin = Annotated[AdminContext, Depends(require_admin("offers.write"))]
PublishAdmin = Annotated[AdminContext, Depends(require_admin("offers.publish"))]


def get_offer_service(session: SessionDep, auditor: AuditorDep) -> AdminOfferService:
    return AdminOfferService(AdminOfferRepository(session), session, auditor)


ServiceDep = Annotated[AdminOfferService, Depends(get_offer_service)]


async def _transition(
    admin: AdminContext,
    offer_id: str,
    action: OfferAction,
    response: Response,
    service: AdminOfferService,
    review_token: str | None = None,
    commercial_basis: CommercialBasis | None = None,
) -> OfferWriteResponse:
    no_store(response)
    try:
        detail = await service.transition(
            admin, offer_id, action, review_token, commercial_basis
        )
    except CatalogueError as exc:
        raise catalogue_http_error(exc) from None
    return OfferWriteResponse(data=offer_write_out(detail))


@router.get("", response_model=OffersPageResponse)
async def admin_list_offers(
    response: Response,
    _admin: ReadAdmin,
    service: ServiceDep,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 20,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
    search: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
    status: OfferStatus | None = None,
    category_id: Annotated[IdText | None, Query()] = None,
    needs_review: bool | None = None,
) -> OffersPageResponse:
    no_store(response)
    page = await service.list_offers(
        OfferFilters(search, status, category_id, needs_review), limit, offset
    )
    return OffersPageResponse(
        data=OffersPage(
            offers=[
                summary_out(item, page.provenance.get(item.id)) for item in page.offers
            ],
            total_count=page.total_count,
            limit=limit,
            offset=offset,
        )
    )


@router.post("", response_model=OfferResponse, status_code=201)
async def admin_create_offer(
    body: CreateOfferRequest,
    response: Response,
    admin: WriteAdmin,
    service: ServiceDep,
) -> OfferResponse:
    no_store(response)
    try:
        detail = await service.create_offer(admin, body.model_dump())
    except CatalogueError as exc:
        raise catalogue_http_error(exc) from None
    return OfferResponse(data=offer_out(detail))


@router.get("/{offer_id}", response_model=OfferResponse)
async def admin_read_offer(
    offer_id: ResourceId, response: Response, _admin: ReadAdmin, service: ServiceDep
) -> OfferResponse:
    no_store(response)
    try:
        detail: OfferDetail = await service.get_offer(offer_id)
    except CatalogueError as exc:
        raise catalogue_http_error(exc) from None
    return OfferResponse(data=offer_out(detail))


@router.patch("/{offer_id}", response_model=OfferWriteResponse)
async def admin_update_offer(
    offer_id: ResourceId,
    body: UpdateOfferRequest,
    response: Response,
    admin: WriteAdmin,
    service: ServiceDep,
) -> OfferWriteResponse:
    no_store(response)
    try:
        detail = await service.update_offer(
            admin, offer_id, body.model_dump(exclude_unset=True)
        )
    except CatalogueError as exc:
        raise catalogue_http_error(exc) from None
    return OfferWriteResponse(data=offer_write_out(detail))


@router.post("/{offer_id}/publish", response_model=OfferWriteResponse)
async def admin_publish_offer(
    offer_id: ResourceId,
    body: PublishRequest,
    response: Response,
    admin: PublishAdmin,
    service: ServiceDep,
) -> OfferWriteResponse:
    return await _transition(
        admin,
        offer_id,
        OfferAction.PUBLISH,
        response,
        service,
        body.review_token,
        body.commercial_basis,
    )


@router.post("/{offer_id}/unpublish", response_model=OfferWriteResponse)
async def admin_unpublish_offer(
    offer_id: ResourceId, response: Response, admin: PublishAdmin, service: ServiceDep
) -> OfferWriteResponse:
    return await _transition(admin, offer_id, OfferAction.UNPUBLISH, response, service)


@router.post("/{offer_id}/expire", response_model=OfferWriteResponse)
async def admin_expire_offer(
    offer_id: ResourceId, response: Response, admin: PublishAdmin, service: ServiceDep
) -> OfferWriteResponse:
    return await _transition(admin, offer_id, OfferAction.EXPIRE, response, service)


@router.post("/{offer_id}/archive", response_model=OfferWriteResponse)
async def admin_archive_offer(
    offer_id: ResourceId, response: Response, admin: PublishAdmin, service: ServiceDep
) -> OfferWriteResponse:
    return await _transition(admin, offer_id, OfferAction.ARCHIVE, response, service)


@router.post("/{offer_id}/verify", response_model=OfferWriteResponse)
async def admin_verify_offer(
    offer_id: ResourceId,
    response: Response,
    body: VerifyRequest,
    admin: PublishAdmin,
    service: ServiceDep,
) -> OfferWriteResponse:
    no_store(response)
    try:
        detail = await service.verify(admin, offer_id, body.note, body.review_token)
    except CatalogueError as exc:
        raise catalogue_http_error(exc) from None
    return OfferWriteResponse(data=offer_write_out(detail))


@router.post("/{offer_id}/dismiss-source-change", response_model=OfferWriteResponse)
async def admin_dismiss_offer_source_change(
    offer_id: ResourceId,
    response: Response,
    body: DismissSourceChangeRequest,
    admin: PublishAdmin,
    service: ServiceDep,
) -> OfferWriteResponse:
    no_store(response)
    try:
        detail = await service.dismiss_source_change(
            admin, offer_id, body.review_token, body.source_changed_at
        )
    except CatalogueError as exc:
        raise catalogue_http_error(exc) from None
    return OfferWriteResponse(data=offer_write_out(detail))


@router.post("/{offer_id}/links", response_model=LinkResponse, status_code=201)
async def admin_create_offer_link(
    offer_id: ResourceId,
    body: CreateLinkRequest,
    response: Response,
    admin: WriteAdmin,
    service: ServiceDep,
) -> LinkResponse:
    no_store(response)
    try:
        link = await service.create_link(admin, offer_id, body.model_dump())
    except CatalogueError as exc:
        raise catalogue_http_error(exc) from None
    return LinkResponse(data=link_out(link))


@router.patch("/{offer_id}/links/{link_id}", response_model=LinkResponse)
async def admin_update_offer_link(
    offer_id: ResourceId,
    link_id: ResourceId,
    body: UpdateLinkRequest,
    response: Response,
    admin: WriteAdmin,
    service: ServiceDep,
) -> LinkResponse:
    no_store(response)
    try:
        link = await service.update_link(
            admin, offer_id, link_id, body.model_dump(exclude_unset=True)
        )
    except CatalogueError as exc:
        raise catalogue_http_error(exc) from None
    return LinkResponse(data=link_out(link))
