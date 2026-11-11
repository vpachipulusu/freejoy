"""A merchant's daily budget of writes through the partner API (ADR-013, FREEJ-58).

The per-key rate limit (``app.partner.limits``) scales with the number of keys a merchant holds, and every accepted
``PUT`` or ``end`` writes an audit row, so one more limit is keyed by the merchant: ``PARTNER_MERCHANT_WRITE_POLICY``
(2,000 a day, fails closed). It is a dependency of the two write routes, so reads and refused requests never spend it.
"""

import logging

from fastapi import HTTPException

from app.core.errors import CodedHTTPException
from app.core.rate_limit import PARTNER_MERCHANT_WRITE_POLICY
from app.core.rate_limit_enforcement import (
    FAIL_CLOSED_RETRY_AFTER_SECONDS,
    TOO_MANY_REQUESTS_MESSAGE,
    UNAVAILABLE_MESSAGE,
    rate_limit_headers,
    retry_after_seconds,
)
from app.core.rate_limiter_provider import get_rate_limiter
from app.partner.auth import PartnerContextDep

logger = logging.getLogger("yourapp.partner")

WRITE_BUDGET_CODE = "daily_write_budget"


async def enforce_merchant_write_budget(context: PartnerContextDep) -> None:
    """Spend one write of the calling merchant's day, or answer 429 with the code ``daily_write_budget``."""
    policy = PARTNER_MERCHANT_WRITE_POLICY
    bucket = f"{policy.scope}:merchant:{context.merchant_id}"
    try:
        decision = await get_rate_limiter().hit(bucket, policy)
    except Exception as exc:
        logger.error("partner write budget unavailable", exc_info=exc)
        raise HTTPException(
            503,
            UNAVAILABLE_MESSAGE,
            {"Retry-After": str(FAIL_CLOSED_RETRY_AFTER_SECONDS)},
        ) from None
    if not decision.allowed:
        raise CodedHTTPException(
            429,
            TOO_MANY_REQUESTS_MESSAGE,
            WRITE_BUDGET_CODE,
            {
                **rate_limit_headers(decision),
                "Retry-After": str(retry_after_seconds(decision)),
            },
        )
