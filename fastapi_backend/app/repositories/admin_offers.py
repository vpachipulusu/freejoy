"""Queries behind the admin offers API. No business rules live here."""

from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, and_, func, not_, or_, select, update

from app.models.platform.affiliate_link import AffiliateLink
from app.models.platform.catalogue_import import OfferImport
from app.models.platform.category import Category
from app.models.platform.merchant import Merchant
from app.models.platform.offer import (
    CommercialBasis,
    Offer,
    OfferFreshness,
    OfferStatus,
    OfferType,
    PostageKind,
)
from app.repositories.base import BaseRepository
from app.repositories.catalogue import escape_like


@dataclass(frozen=True)
class OfferRecord:
    id: str
    title: str
    slug: str
    summary: str
    description: list[str]
    category_id: str
    merchant_id: str
    offer_type: OfferType
    commercial_basis: CommercialBasis
    status: OfferStatus
    featured: bool
    image_tint: str
    image_label: str
    image_alt: str
    source_url: str | None
    postage_kind: PostageKind
    postage_amount_minor: int | None
    postage_currency: str | None
    requires_card: bool
    subscription_terms: str | None
    eligibility: str
    availability: str
    steps: list[dict[str, str]]
    start_at: datetime | None
    end_at: datetime | None
    last_verified_at: datetime | None
    freshness: OfferFreshness | None
    publisher_notes: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class AffiliateLinkRecord:
    id: str
    offer_id: str
    provider: str
    destination_url: str
    tracking_id: str | None
    active: bool
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class ReviewRefs:
    """What a visitor sees of an offer's category and merchant (a field is None when the row is missing)."""

    category_name: str | None
    category_short_name: str | None
    category_enabled: bool | None
    merchant_name: str | None
    merchant_website: str | None
    merchant_logo_url: str | None
    merchant_active: bool | None


@dataclass(frozen=True)
class OfferProvenance:
    """Where an imported offer came from and whether its source moved on (``offer_imports``)."""

    provider: str
    external_id: str
    applied_at: datetime
    source_changed_at: datetime | None


@dataclass(frozen=True)
class OfferFilters:
    """``needs_review`` True keeps the review queue, False keeps everything else (None: no filter)."""

    search: str | None = None
    status: OfferStatus | None = None
    category_id: str | None = None
    needs_review: bool | None = None


@dataclass(frozen=True)
class OfferPage:
    offers: list[OfferRecord]
    total_count: int
    # By offer id, for the imported offers on this page only (a hand-made offer has no entry).
    provenance: Mapping[str, OfferProvenance] = dataclass_field(default_factory=dict)


_OFFER_FIELDS = tuple(OfferRecord.__dataclass_fields__)
_LINK_FIELDS = tuple(AffiliateLinkRecord.__dataclass_fields__)


def _offer(row: Offer) -> OfferRecord:
    return OfferRecord(**{name: getattr(row, name) for name in _OFFER_FIELDS})


def _link(row: AffiliateLink) -> AffiliateLinkRecord:
    return AffiliateLinkRecord(**{name: getattr(row, name) for name in _LINK_FIELDS})


def needs_review() -> ColumnElement[bool]:
    """The review queue rule, defined once: an imported offer that is a draft or whose source moved on.

    Needs the LEFT JOIN on ``offer_imports``. Never NULL: ``IS NOT NULL`` is a definite false for an
    offer with no import row, so ``NOT`` of it is true for hand-made offers. Archived offers are out.
    """
    return and_(
        OfferImport.id.is_not(None),
        Offer.status != OfferStatus.ARCHIVED,
        or_(
            Offer.status == OfferStatus.DRAFT,
            OfferImport.source_changed_at.is_not(None),
        ),
    )


def _conditions(filters: OfferFilters) -> list[Any]:
    conditions: list[Any] = []
    if filters.search:
        pattern = f"%{escape_like(filters.search)}%"
        conditions.append(
            Offer.title.ilike(pattern, escape="\\")
            | Offer.slug.ilike(pattern, escape="\\")
            | Offer.summary.ilike(pattern, escape="\\")
        )
    if filters.status is not None:
        conditions.append(Offer.status == filters.status)
    if filters.category_id is not None:
        conditions.append(Offer.category_id == filters.category_id)
    if filters.needs_review is not None:
        wanted = needs_review()
        conditions.append(wanted if filters.needs_review else not_(wanted))
    return conditions


class AdminOfferRepository(BaseRepository):
    async def list_offers(
        self, filters: OfferFilters, limit: int, offset: int
    ) -> OfferPage:
        """One page of offers with their provenance in the same query (LEFT JOIN, no per-row lookups)."""
        conditions = _conditions(filters)
        result = await self._session.execute(
            select(
                Offer,
                OfferImport.provider,
                OfferImport.external_id,
                OfferImport.applied_at,
                OfferImport.source_changed_at,
            )
            .outerjoin(OfferImport, OfferImport.offer_id == Offer.id)
            .where(*conditions)
            .order_by(Offer.updated_at.desc(), Offer.id)
            .limit(limit)
            .offset(offset)
        )
        rows = result.all()
        total = await self._session.scalar(
            select(func.count())
            .select_from(Offer)
            .outerjoin(OfferImport, OfferImport.offer_id == Offer.id)
            .where(*conditions)
        )
        provenance = {
            row.Offer.id: OfferProvenance(
                row.provider, row.external_id, row.applied_at, row.source_changed_at
            )
            for row in rows
            if row.provider is not None
        }
        return OfferPage([_offer(row.Offer) for row in rows], total or 0, provenance)

    async def get_provenance(
        self, offer_id: str, *, lock: bool = False
    ) -> OfferProvenance | None:
        """The offer's import row (None for a hand-made offer), row-locked when ``lock``.

        Columns, not the entity, so a read after ``clear_source_change`` never sees a cached row.
        """
        statement = select(
            OfferImport.provider,
            OfferImport.external_id,
            OfferImport.applied_at,
            OfferImport.source_changed_at,
        ).where(OfferImport.offer_id == offer_id)
        row = (
            await self._session.execute(
                statement.with_for_update() if lock else statement
            )
        ).first()
        return OfferProvenance(*row) if row else None

    async def clear_source_change(self, offer_id: str) -> None:
        """Clear the marker only: the importer's hashes are left exactly as they are."""
        await self._session.execute(
            update(OfferImport)
            .where(OfferImport.offer_id == offer_id)
            .values(source_changed_at=None)
        )

    async def get(self, offer_id: str, *, lock: bool = False) -> OfferRecord | None:
        row = await self._session.get(
            Offer, offer_id, with_for_update=lock, populate_existing=lock
        )
        return _offer(row) if row else None

    async def slug_exists(self, slug: str) -> bool:
        found = await self._session.scalar(select(Offer.id).where(Offer.slug == slug))
        return found is not None

    async def insert(self, values: dict[str, Any]) -> OfferRecord:
        row = Offer(**values)
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return _offer(row)

    async def update(self, offer_id: str, values: dict[str, Any]) -> OfferRecord:
        row = await self._session.get(Offer, offer_id)
        if row is None:  # pragma: no cover - the caller holds the row lock
            raise LookupError(offer_id)
        for field, value in values.items():
            setattr(row, field, value)
        await self._session.flush()
        await self._session.refresh(row)
        return _offer(row)

    async def category_enabled(self, category_id: str) -> bool | None:
        """Whether the category is enabled (None: it does not exist), under a share lock.

        FOR SHARE makes a concurrent disable wait until this transaction commits, and a
        publish that starts after a disable wait for it, so the two cannot interleave.
        """
        return await self._session.scalar(
            select(Category.enabled)
            .where(Category.id == category_id)
            .with_for_update(read=True)
        )

    async def merchant_active(self, merchant_id: str) -> bool | None:
        """Whether the merchant is active (None: it does not exist), under a share lock."""
        return await self._session.scalar(
            select(Merchant.active)
            .where(Merchant.id == merchant_id)
            .with_for_update(read=True)
        )

    async def review_refs(
        self, category_id: str, merchant_id: str, *, share: bool = False
    ) -> ReviewRefs:
        """The category and merchant fields a visitor sees, category first (the lock order).

        With ``share`` both rows are read FOR SHARE, so a concurrent edit of either waits for this
        transaction and the token compared under the offer's row lock cannot go stale before the commit.
        """
        category = select(Category.name, Category.short_name, Category.enabled).where(
            Category.id == category_id
        )
        merchant = select(
            Merchant.name, Merchant.website, Merchant.logo_url, Merchant.active
        ).where(Merchant.id == merchant_id)
        if share:
            category, merchant = (
                category.with_for_update(read=True),
                merchant.with_for_update(read=True),
            )
        found_category = (await self._session.execute(category)).first()
        found_merchant = (await self._session.execute(merchant)).first()
        return ReviewRefs(
            category_name=found_category.name if found_category else None,
            category_short_name=found_category.short_name if found_category else None,
            category_enabled=found_category.enabled if found_category else None,
            merchant_name=found_merchant.name if found_merchant else None,
            merchant_website=found_merchant.website if found_merchant else None,
            merchant_logo_url=found_merchant.logo_url if found_merchant else None,
            merchant_active=found_merchant.active if found_merchant else None,
        )

    async def merchant_website(self, merchant_id: str) -> str | None:
        """The merchant's stored website, used to judge ``direct`` links."""
        return await self._session.scalar(
            select(Merchant.website).where(Merchant.id == merchant_id)
        )

    async def list_links(self, offer_id: str) -> list[AffiliateLinkRecord]:
        rows = await self._session.scalars(
            select(AffiliateLink)
            .where(AffiliateLink.offer_id == offer_id)
            .order_by(AffiliateLink.created_at, AffiliateLink.id)
        )
        return [_link(row) for row in rows]

    async def count_active_links(self, offer_id: str) -> int:
        total = await self._session.scalar(
            select(func.count())
            .select_from(AffiliateLink)
            .where(AffiliateLink.offer_id == offer_id, AffiliateLink.active.is_(True))
        )
        return total or 0

    async def get_link(
        self, offer_id: str, link_id: str, *, lock: bool = False
    ) -> AffiliateLinkRecord | None:
        statement = select(AffiliateLink).where(
            AffiliateLink.id == link_id, AffiliateLink.offer_id == offer_id
        )
        row = await self._session.scalar(
            statement.with_for_update() if lock else statement
        )
        return _link(row) if row else None

    async def insert_link(self, values: dict[str, Any]) -> AffiliateLinkRecord:
        row = AffiliateLink(**values)
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return _link(row)

    async def update_link(
        self, link_id: str, values: dict[str, Any]
    ) -> AffiliateLinkRecord:
        row = await self._session.get(AffiliateLink, link_id)
        if row is None:  # pragma: no cover - the caller holds the row lock
            raise LookupError(link_id)
        for field, value in values.items():
            setattr(row, field, value)
        await self._session.flush()
        await self._session.refresh(row)
        return _link(row)
