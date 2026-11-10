"""The process-wide rate limiter (ADR-007), shared by the internal API and the partner surface.

Moved here from ``app.api.deps`` (which re-exports ``get_rate_limiter``) so that ``app.partner``, which may
import only ``app.core``, uses the same limiter and the same Upstash client. Tests override ``get_rate_limiter``
as a FastAPI dependency, or reset ``_rate_limiter``.
"""

import logging

from app.core.config import settings
from app.core.rate_limit import DisabledRateLimiter, RateLimiter, UpstashRateLimiter

logger = logging.getLogger(__name__)

_rate_limiter: RateLimiter | None = None


def get_rate_limiter() -> RateLimiter:
    """The limiter routes and readiness use; tests override this with a fake.

    Without Upstash settings (local dev, tests) rate limiting is disabled.
    """
    global _rate_limiter
    if _rate_limiter is None:
        token = settings.UPSTASH_REDIS_REST_TOKEN
        if settings.UPSTASH_REDIS_REST_URL and token:
            _rate_limiter = UpstashRateLimiter.from_credentials(
                settings.UPSTASH_REDIS_REST_URL, token.get_secret_value()
            )
        else:
            logger.warning("Upstash Redis not configured: rate limiting is disabled")
            _rate_limiter = DisabledRateLimiter()
    return _rate_limiter
