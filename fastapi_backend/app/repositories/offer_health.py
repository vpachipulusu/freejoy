"""Queries behind the offer health command. No business rules live here.

"Live" means ``PUBLISHED`` with ``end_at`` null or in the future, the same cut-off the command uses
to decide what to expire, so an offer is in exactly one of the two sets.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from sqlalchemy import ColumnElement, CursorResult, and_, or_, select, update

from app.core.affiliate_hosts import DIRECT_PROVIDER
from app.models.platform.affiliate_link import AffiliateLink
from app.models.platform.merchant import Merchant
from app.models.platform.offer import Offer, OfferStatus
from app.repositories.base import BaseRepository


@dataclass(frozen=True)
class DueOffer:
    """A published offer whose ``end_at`` has passed."""

    id: str
    status: OfferStatus
    end_at: datetime | None


@dataclass(frozen=True)
class SourceUrlTarget:
    offer_id: str
    url: str
    last_verified_at: datetime | None


@dataclass(frozen=True)
class DirectLinkTarget:
    offer_id: str
    link_id: str
    provider: str
    url: str
    merchant_website: str | None
    last_verified_at: datetime | None


def _live(now: datetime) -> ColumnElement[bool]:
    return and_(
        Offer.status == OfferStatus.PUBLISHED,
        or_(Offer.end_at.is_(None), Offer.end_at > now),
    )


class OfferHealthRepository(BaseRepository):
    async def lock_due_offers(self, now: datetime, limit: int) -> list[DueOffer]:
        """Up to ``limit`` published offers that have ended, oldest first, row-locked.

        ``SKIP LOCKED`` leaves an offer an editor is saving at this moment to the next run, and
        the lock is why the status written afterwards cannot overwrite a concurrent edit.
        """
        result = await self._session.execute(
            select(Offer.id, Offer.status, Offer.end_at)
            .where(
                Offer.status == OfferStatus.PUBLISHED,
                Offer.end_at.is_not(None),
                Offer.end_at <= now,
            )
            .order_by(Offer.end_at, Offer.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return [DueOffer(row.id, row.status, row.end_at) for row in result.all()]

    async def mark_expired(self, offer_ids: Sequence[str]) -> int:
        """Set ``EXPIRED`` on locked, still-published offers; returns the rows changed."""
        if not offer_ids:
            return 0
        result = await self._session.execute(
            update(Offer)
            .where(Offer.id.in_(offer_ids), Offer.status == OfferStatus.PUBLISHED)
            .values(status=OfferStatus.EXPIRED)
        )
        return int(cast("CursorResult[Any]", result).rowcount)

    async def list_source_url_targets(
        self, now: datetime, limit: int
    ) -> list[SourceUrlTarget]:
        """Live offers with a source URL, least recently verified first."""
        result = await self._session.execute(
            select(Offer.id, Offer.source_url, Offer.last_verified_at)
            .where(_live(now), Offer.source_url.is_not(None), Offer.source_url != "")
            .order_by(Offer.last_verified_at.asc().nulls_first(), Offer.id)
            .limit(limit)
        )
        return [
            SourceUrlTarget(row.id, row.source_url, row.last_verified_at)
            for row in result.all()
        ]

    async def list_direct_link_targets(
        self, now: datetime, limit: int
    ) -> list[DirectLinkTarget]:
        """Active ``direct`` links of live offers. Any other provider is excluded in SQL."""
        result = await self._session.execute(
            select(
                AffiliateLink.offer_id,
                AffiliateLink.id,
                AffiliateLink.provider,
                AffiliateLink.destination_url,
                Merchant.website,
                Offer.last_verified_at,
            )
            .select_from(AffiliateLink)
            .join(Offer, Offer.id == AffiliateLink.offer_id)
            .join(Merchant, Merchant.id == Offer.merchant_id)
            .where(
                _live(now),
                AffiliateLink.provider == DIRECT_PROVIDER,
                AffiliateLink.active.is_(True),
            )
            .order_by(Offer.last_verified_at.asc().nulls_first(), AffiliateLink.id)
            .limit(limit)
        )
        return [
            DirectLinkTarget(
                offer_id=row.offer_id,
                link_id=row.id,
                provider=row.provider,
                url=row.destination_url,
                merchant_website=row.website,
                last_verified_at=row.last_verified_at,
            )
            for row in result.all()
        ]
