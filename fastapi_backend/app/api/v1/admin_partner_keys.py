"""Admin partner keys API (ADR-013): list, issue and revoke a merchant's partner API keys.

Behind the internal key and an admin permission, like ``admin_merchants``: listing needs
``merchants.read``, issuing and revoking need ``merchants.write``. The key is returned once, by the
create call, and is never stored, logged or audited; every response is ``no-store``.
"""

from datetime import datetime
from typing import Annotated, Literal

from fastapi import Depends, Response
from pydantic import BaseModel, ConfigDict

from app.api.admin import AuditorDep, require_admin
from app.api.deps import SessionDep
from app.api.v1.admin_catalogue_common import (
    ResourceId,
    admin_router,
    catalogue_http_error,
    line,
    no_store,
)
from app.core.config import settings
from app.core.partner_keys import running_environment
from app.core.permissions import AdminContext
from app.repositories.admin_merchants import AdminMerchantRepository
from app.repositories.admin_partner_keys import (
    AdminPartnerKeyRepository,
    PartnerKeyRecord,
)
from app.services.admin_catalogue_errors import CatalogueError
from app.services.admin_partner_keys import AdminPartnerKeyService
from app.services.admin_partner_keys import IssuedKey as IssuedKeyResult

router = admin_router(
    "/v1/admin/merchants/{merchant_id}/partner-keys", "admin-partner-keys"
)

ReadAdmin = Annotated[AdminContext, Depends(require_admin("merchants.read"))]
WriteAdmin = Annotated[AdminContext, Depends(require_admin("merchants.write"))]

Label = Annotated[str, line(80)]


class PartnerKeyOut(BaseModel):
    """A key's metadata. Never the key and never its hash."""

    id: str
    merchant_id: str
    label: str
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


class PartnerKeysResponse(BaseModel):
    success: Literal[True] = True
    data: list[PartnerKeyOut]


class PartnerKeyResponse(BaseModel):
    success: Literal[True] = True
    data: PartnerKeyOut


class IssuedPartnerKey(BaseModel):
    key: PartnerKeyOut
    # Shown once: this response is the only place the value ever exists.
    api_key: str


class IssuedPartnerKeyResponse(BaseModel):
    success: Literal[True] = True
    data: IssuedPartnerKey


class CreatePartnerKeyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: Label


def get_partner_key_service(
    session: SessionDep, auditor: AuditorDep
) -> AdminPartnerKeyService:
    return AdminPartnerKeyService(
        AdminPartnerKeyRepository(session),
        AdminMerchantRepository(session),
        session,
        auditor,
        running_environment(settings.VERCEL_ENV, settings.ENVIRONMENT),
    )


ServiceDep = Annotated[AdminPartnerKeyService, Depends(get_partner_key_service)]


def _out(record: PartnerKeyRecord) -> PartnerKeyOut:
    return PartnerKeyOut(
        id=record.id,
        merchant_id=record.merchant_id,
        label=record.label,
        created_at=record.created_at,
        last_used_at=record.last_used_at,
        revoked_at=record.revoked_at,
    )


def _issued(result: IssuedKeyResult) -> IssuedPartnerKey:
    return IssuedPartnerKey(key=_out(result.record), api_key=result.value)


@router.get("", response_model=PartnerKeysResponse)
async def admin_list_partner_keys(
    merchant_id: ResourceId, response: Response, _admin: ReadAdmin, service: ServiceDep
) -> PartnerKeysResponse:
    no_store(response)
    try:
        records = await service.list_keys(merchant_id)
    except CatalogueError as exc:
        raise catalogue_http_error(exc) from None
    return PartnerKeysResponse(data=[_out(record) for record in records])


@router.post("", response_model=IssuedPartnerKeyResponse, status_code=201)
async def admin_create_partner_key(
    merchant_id: ResourceId,
    body: CreatePartnerKeyRequest,
    response: Response,
    admin: WriteAdmin,
    service: ServiceDep,
) -> IssuedPartnerKeyResponse:
    no_store(response)
    try:
        issued = await service.create_key(admin, merchant_id, body.label)
    except CatalogueError as exc:
        raise catalogue_http_error(exc) from None
    return IssuedPartnerKeyResponse(data=_issued(issued))


@router.post("/{key_id}/revoke", response_model=PartnerKeyResponse)
async def admin_revoke_partner_key(
    merchant_id: ResourceId,
    key_id: ResourceId,
    response: Response,
    admin: WriteAdmin,
    service: ServiceDep,
) -> PartnerKeyResponse:
    no_store(response)
    try:
        revoked = await service.revoke_key(admin, merchant_id, key_id)
    except CatalogueError as exc:
        raise catalogue_http_error(exc) from None
    return PartnerKeyResponse(data=_out(revoked))
