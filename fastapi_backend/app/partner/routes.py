"""The partner endpoints (ADR-013). Paths are written relative to the mount (``/me``, not ``/partner/v1/me``).

Authentication and the rate limit are on the sub-app (``create_partner_api``), not here: a route added to this
router cannot forget them. A route takes ``PartnerContextDep`` to learn who is calling, and scopes every query
to ``context.merchant_id``.
"""

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.partner.auth import PartnerContextDep
from app.partner.deps import KeyStoreDep

router = APIRouter()

KEY_GONE_MESSAGE = "Unauthorized"


class MeData(BaseModel):
    merchant_name: str = Field(
        description="The name of the merchant this key was issued to."
    )
    key_label: str = Field(
        description="The label the key was given when it was issued."
    )


class MeResponse(BaseModel):
    success: Literal[True] = Field(default=True, description="Always true.")
    data: MeData = Field(description="Who this key belongs to.")


@router.get(
    "/me",
    response_model=MeResponse,
    operation_id="partner-me",
    summary="Who this key belongs to",
    description=(
        "Returns the name of the merchant your key was issued to and the label the key was given. "
        "It changes nothing: use it to check that your key and the address work."
    ),
)
async def read_me(context: PartnerContextDep, store: KeyStoreDep) -> MeResponse:
    """The merchant the key was issued to and the key's label. It changes nothing."""
    profile = await store.profile(context.key_id)
    if (
        profile is None
    ):  # the key (or its merchant) was deleted between the authentication and here
        raise HTTPException(401, KEY_GONE_MESSAGE, {"WWW-Authenticate": "Bearer"})
    return MeResponse(
        data=MeData(merchant_name=profile.merchant_name, key_label=profile.key_label)
    )
