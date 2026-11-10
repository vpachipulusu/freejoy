"""Read access to the public catalogue: offers with their category and merchant.

Every query selects an explicit column list, so editorial-only columns such as
``publisher_notes`` and rows of other tables (``affiliate_links``) never leave this layer.
All values reach the database as bound parameters.
"""

import enum
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, Select, func, or_, select

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

MAX_PAGE_SIZE = 50
LIKE_ESCAPE = "\\"


class OfferSort(enum.StrEnum):
    NEWEST = "newest"
    ENDING_SOON = "ending_soon"
    A_Z = "a_z"


@dataclass(frozen=True)
class OfferFilters:
    """What the list query may filter and order by; ``None`` / empty means no filter."""

    query: str | None = None
    category_slug: str | None = None
    offer_types: tuple[OfferType, ...] = ()
    free_postage_only: bool = False
    featured: bool | None = None
    sort: OfferSort = OfferSort.NEWEST


@dataclass(frozen=True)
class OfferRecord:
    """One offer as the public pages need it. Carries no editorial-only field."""

    id: str
    slug: str
    title: str
    summary: str
    description: list[Any]
    category_slug: str
    category_name: str
    merchant_slug: str
    merchant_name: str
    merchant_logo_url: str | None
    offer_type: OfferType
    # Never serialised as it is: the public API only says whether it is ``SPONSORED``.
    commercial_basis: CommercialBasis
    status: OfferStatus
    featured: bool
    image_tint: str
    image_label: str
    image_alt: str
    postage_kind: PostageKind
    postage_amount_minor: int | None
    postage_currency: str | None
    requires_card: bool
    subscription_terms: str | None
    eligibility: str
    availability: str
    start_at: datetime
    end_at: datetime | None
    last_verified_at: datetime | None
    freshness: OfferFreshness | None
    steps: list[Any]


@dataclass(frozen=True)
class CategoryRecord:
    slug: str
    name: str
    short_name: str
    description: str
    intro: str
    tint: str
    sort_order: int
    offer_count: int


# `start_at` is nullable in the table; pages always show a start, so fall back to creation.
START_AT = func.coalesce(Offer.start_at, Offer.created_at)

_OFFER_COLUMNS = (
    Offer.id,
    Offer.slug,
    Offer.title,
    Offer.summary,
    Offer.description,
    Category.slug.label("category_slug"),
    Category.name.label("category_name"),
    Merchant.slug.label("merchant_slug"),
    Merchant.name.label("merchant_name"),
    Merchant.logo_url.label("merchant_logo_url"),
    Offer.offer_type,
    Offer.commercial_basis,
    Offer.status,
    Offer.featured,
    Offer.image_tint,
    Offer.image_label,
    Offer.image_alt,
    Offer.postage_kind,
    Offer.postage_amount_minor,
    Offer.postage_currency,
    Offer.requires_card,
    Offer.subscription_terms,
    Offer.eligibility,
    Offer.availability,
    START_AT.label("start_at"),
    Offer.end_at,
    Offer.last_verified_at,
    Offer.freshness,
    Offer.steps,
)


def escape_like(term: str) -> str:
    """Make ``%``, ``_`` and the escape character match themselves inside ``ILIKE``."""
    return (
        term.replace(LIKE_ESCAPE, LIKE_ESCAPE * 2)
        .replace("%", f"{LIKE_ESCAPE}%")
        .replace("_", f"{LIKE_ESCAPE}_")
    )


def live_conditions(now: datetime) -> list[ColumnElement[bool]]:
    """Published and not past its end date. Mirrors ``CatalogueService.is_live``."""
    return [
        Offer.status == OfferStatus.PUBLISHED,
        or_(Offer.end_at.is_(None), Offer.end_at > now),
    ]


def _visible_conditions() -> list[ColumnElement[bool]]:
    """A disabled category or inactive merchant hides its offers from the public."""
    return [Category.enabled.is_(True), Merchant.active.is_(True)]


def _search_condition(term: str) -> ColumnElement[bool]:
    pattern = f"%{escape_like(term)}%"
    return or_(
        Offer.title.ilike(pattern, escape=LIKE_ESCAPE),
        Offer.summary.ilike(pattern, escape=LIKE_ESCAPE),
        Merchant.name.ilike(pattern, escape=LIKE_ESCAPE),
    )


def _filter_conditions(filters: OfferFilters) -> list[ColumnElement[bool]]:
    conditions: list[ColumnElement[bool]] = []
    if filters.query:
        conditions.append(_search_condition(filters.query))
    if filters.category_slug:
        conditions.append(Category.slug == filters.category_slug)
    if filters.offer_types:
        conditions.append(Offer.offer_type.in_(filters.offer_types))
    if filters.free_postage_only:
        conditions.append(Offer.postage_kind == PostageKind.FREE)
    if filters.featured is not None:
        conditions.append(Offer.featured.is_(filters.featured))
    return conditions


def _order_by(sort: OfferSort) -> list[Any]:
    """Every order ends on the id, so pages never overlap or skip rows."""
    if sort is OfferSort.A_Z:
        return [func.lower(Offer.title), Offer.id]
    if sort is OfferSort.ENDING_SOON:
        return [Offer.end_at.asc().nulls_last(), START_AT.desc(), Offer.id]
    return [START_AT.desc(), Offer.id]


def _joined(statement: Select[Any]) -> Select[Any]:
    return (
        statement.select_from(Offer)
        .join(Category, Category.id == Offer.category_id)
        .join(Merchant, Merchant.id == Offer.merchant_id)
    )


class CatalogueRepository(BaseRepository):
    async def list_live_offers(
        self, filters: OfferFilters, now: datetime, limit: int, offset: int
    ) -> tuple[int, list[OfferRecord]]:
        """The matching live offers as (total, one page). ``limit`` is clamped to 1..50."""
        conditions = [
            *live_conditions(now),
            *_visible_conditions(),
            *_filter_conditions(filters),
        ]
        total = (
            await self._session.execute(
                _joined(select(func.count(Offer.id))).where(*conditions)
            )
        ).scalar_one()
        page = (
            _joined(select(*_OFFER_COLUMNS))
            .where(*conditions)
            .order_by(*_order_by(filters.sort))
            .limit(max(1, min(limit, MAX_PAGE_SIZE)))
            .offset(max(0, offset))
        )
        rows = (await self._session.execute(page)).mappings().all()
        return int(total), [OfferRecord(**row) for row in rows]

    async def find_visible_offer(self, slug: str) -> OfferRecord | None:
        """The offer with this slug in any status, unless its category or merchant is hidden.

        The caller decides what the status means; this never filters on it.
        """
        return await self._find_visible(Offer.slug == slug)

    async def find_visible_offer_by_id(self, offer_id: str) -> OfferRecord | None:
        """``find_visible_offer`` keyed by the offer id (the outbound redirect knows no slug)."""
        return await self._find_visible(Offer.id == offer_id)

    async def _find_visible(self, match: ColumnElement[bool]) -> OfferRecord | None:
        statement = (
            _joined(select(*_OFFER_COLUMNS))
            .where(match, *_visible_conditions())
            .limit(1)
        )
        row = (await self._session.execute(statement)).mappings().first()
        return OfferRecord(**row) if row is not None else None

    async def list_enabled_categories(self, now: datetime) -> Sequence[CategoryRecord]:
        """Enabled categories in display order, each with its count of live offers."""
        counts = (
            select(Offer.category_id.label("category_id"), func.count().label("n"))
            .join(Merchant, Merchant.id == Offer.merchant_id)
            .where(*live_conditions(now), Merchant.active.is_(True))
            .group_by(Offer.category_id)
            .subquery()
        )
        statement = (
            select(
                Category.slug,
                Category.name,
                Category.short_name,
                Category.description,
                Category.intro,
                Category.tint,
                Category.sort_order,
                func.coalesce(counts.c.n, 0).label("offer_count"),
            )
            .outerjoin(counts, counts.c.category_id == Category.id)
            .where(Category.enabled.is_(True))
            .order_by(Category.sort_order, Category.name, Category.id)
        )
        rows = (await self._session.execute(statement)).mappings().all()
        return [CategoryRecord(**row) for row in rows]
