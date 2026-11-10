"""Public response models for the catalogue routes.

These are the whole public surface of an offer: a field exists here only when a public page
shows it. ``publisher_notes``, ``status``, the three-way ``commercial_basis`` (only
``sponsored``, true for ``SPONSORED``, is public), affiliate links and every internal id except the
offer id (the future ``/go/[id]`` redirect) are deliberately absent. Enum values are the
lower-case form the frontend contract uses (``nextjs-frontend/src/lib/catalogue/types.ts``).
"""

from datetime import datetime
from typing import Any, Literal, cast, get_args

from pydantic import BaseModel, Field

from app.models.platform.offer import CommercialBasis
from app.repositories.catalogue import CategoryRecord, OfferRecord
from app.services.catalogue import ExpiredOffer, OfferPage

OfferTypeValue = Literal[
    "free_sample", "free_trial", "cashback", "voucher", "competition"
]
FreshnessValue = Literal["new_today", "just_added", "popular", "ends_soon", "limited"]
PostageKindValue = Literal["free", "cost", "check_terms"]
OFFER_TYPE_VALUES: tuple[str, ...] = get_args(OfferTypeValue)


class MerchantData(BaseModel):
    slug: str
    name: str
    logo_url: str | None


class CategoryRef(BaseModel):
    slug: str
    name: str


class ImageData(BaseModel):
    tint: str
    label: str
    alt: str


class PostageData(BaseModel):
    kind: PostageKindValue
    # Minor units and an upper-case currency code, present only when ``kind`` is ``cost``.
    amount_minor: int | None
    currency: str | None


class ClaimStep(BaseModel):
    title: str
    detail: str


class OfferData(BaseModel):
    id: str
    slug: str
    title: str
    summary: str
    description: list[str]
    category: CategoryRef
    merchant: MerchantData
    offer_type: OfferTypeValue
    # True only when the merchant pays or gives a benefit for the listing: shown as a "Sponsored" label.
    sponsored: bool
    featured: bool
    image: ImageData
    postage: PostageData
    requires_card: bool
    subscription_terms: str | None
    eligibility: str
    availability: str
    start_at: datetime
    end_at: datetime | None
    last_verified_at: datetime | None
    freshness: FreshnessValue | None
    steps: list[ClaimStep]


class OfferListData(BaseModel):
    items: list[OfferData]
    total: int
    limit: int
    offset: int


class OfferListResponse(BaseModel):
    success: Literal[True] = True
    data: OfferListData


class OfferResponse(BaseModel):
    success: Literal[True] = True
    data: OfferData


class ExpiredOfferData(BaseModel):
    slug: str
    title: str
    category: CategoryRef
    ended_at: datetime | None


class ExpiredOfferResponse(BaseModel):
    """The 410 body: the error envelope plus ``expired`` and just enough to render the page."""

    success: Literal[False] = False
    error: str = "This offer has ended"
    expired: Literal[True] = True
    data: ExpiredOfferData


class CategoryData(BaseModel):
    slug: str
    name: str
    short_name: str
    description: str
    intro: str
    tint: str
    sort_order: int
    offer_count: int = Field(description="Live offers in this category")


class CategoryListResponse(BaseModel):
    success: Literal[True] = True
    data: list[CategoryData]


def _paragraphs(raw: list[Any]) -> list[str]:
    return [item for item in raw if isinstance(item, str)]


def _steps(raw: list[Any]) -> list[ClaimStep]:
    return [
        ClaimStep(title=str(item.get("title", "")), detail=str(item.get("detail", "")))
        for item in raw
        if isinstance(item, dict)
    ]


def offer_data(record: OfferRecord) -> OfferData:
    freshness = record.freshness.value.lower() if record.freshness else None
    return OfferData(
        id=record.id,
        slug=record.slug,
        title=record.title,
        summary=record.summary,
        description=_paragraphs(record.description),
        category=CategoryRef(slug=record.category_slug, name=record.category_name),
        merchant=MerchantData(
            slug=record.merchant_slug,
            name=record.merchant_name,
            logo_url=record.merchant_logo_url,
        ),
        offer_type=cast(OfferTypeValue, record.offer_type.value.lower()),
        sponsored=record.commercial_basis is CommercialBasis.SPONSORED,
        featured=record.featured,
        image=ImageData(
            tint=record.image_tint, label=record.image_label, alt=record.image_alt
        ),
        postage=PostageData(
            kind=cast(PostageKindValue, record.postage_kind.value.lower()),
            amount_minor=record.postage_amount_minor,
            currency=record.postage_currency,
        ),
        requires_card=record.requires_card,
        subscription_terms=record.subscription_terms,
        eligibility=record.eligibility,
        availability=record.availability,
        start_at=record.start_at,
        end_at=record.end_at,
        last_verified_at=record.last_verified_at,
        freshness=cast(FreshnessValue | None, freshness),
        steps=_steps(record.steps),
    )


def offer_list_response(page: OfferPage, limit: int, offset: int) -> OfferListResponse:
    return OfferListResponse(
        data=OfferListData(
            items=[offer_data(item) for item in page.items],
            total=page.total,
            limit=limit,
            offset=offset,
        )
    )


def expired_response(expired: ExpiredOffer) -> ExpiredOfferResponse:
    return ExpiredOfferResponse(
        data=ExpiredOfferData(
            slug=expired.slug,
            title=expired.title,
            category=CategoryRef(
                slug=expired.category_slug, name=expired.category_name
            ),
            ended_at=expired.ended_at,
        )
    )


def category_data(record: CategoryRecord) -> CategoryData:
    return CategoryData(
        slug=record.slug,
        name=record.name,
        short_name=record.short_name,
        description=record.description,
        intro=record.intro,
        tint=record.tint,
        sort_order=record.sort_order,
        offer_count=record.offer_count,
    )
