"""The partner endpoints (ADR-013). Paths are written relative to the mount (``/me``, not ``/partner/v1/me``).

Authentication and the rate limit are on the sub-app (``create_partner_api``), not here: a route added to this
router cannot forget them. A route takes ``PartnerContextDep`` to learn who is calling, and scopes every query
to ``context.merchant_id``.
"""

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.partner.auth import PartnerContextDep
from app.partner.deps import KeyStoreDep

router = APIRouter()

KEY_GONE_MESSAGE = "Unauthorized"


class MeData(BaseModel):
    merchant_name: str
    key_label: str


class MeResponse(BaseModel):
    success: Literal[True] = True
    data: MeData


@router.get(
    "/me",
    response_model=MeResponse,
    operation_id="partner-me",
    summary="Who this key belongs to",
)
async def read_me(context: PartnerContextDep, store: KeyStoreDep) -> MeResponse:
    """The merchant the key was issued to and the key's label. It changes nothing; it proves the whole path."""
    profile = await store.profile(context.key_id)
    if profile is None:  # revoked and deleted between the authentication and here
        raise HTTPException(401, KEY_GONE_MESSAGE, {"WWW-Authenticate": "Bearer"})
    return MeResponse(
        data=MeData(merchant_name=profile.merchant_name, key_label=profile.key_label)
    )
