"""Request and response models of the admin offers API."""

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from app.api.v1.admin_catalogue_common import (
    IdText,
    SlugText,
    TintText,
    UrlText,
    line,
    paragraph,
)
from app.models.platform.offer import (
    OfferFreshness,
    OfferStatus,
    OfferType,
    PostageKind,
)
from app.repositories.admin_offers import AffiliateLinkRecord, OfferRecord
from app.services.admin_offers import OfferDetail

MAX_PARAGRAPHS = 20
MAX_STEPS = 20
MAX_POSTAGE_MINOR = 1_000_000
ReviewToken = Annotated[
    str, StringConstraints(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
]
Currency = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
Title = Annotated[str, line(200)]
Summary = Annotated[str, line(300)]
Paragraph = Annotated[str, paragraph(2000)]
Detail = Annotated[str, paragraph(500)]
Notes = Annotated[str, paragraph(2000)]
# Fields a PATCH may clear with an explicit null; every other field must be omitted instead.
NULLABLE_ON_UPDATE = frozenset(
    {
        "source_url",
        "postage_amount_minor",
        "postage_currency",
        "subscription_terms",
        "start_at",
        "end_at",
        "freshness",
        "publisher_notes",
    }
)


class StepIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Annotated[str, line(120)]
    detail: Annotated[str, paragraph(1000)]


class CreateOfferRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Title
    slug: SlugText | None = None
    summary: Summary
    description: list[Paragraph] = Field(
        default_factory=list, max_length=MAX_PARAGRAPHS
    )
    category_id: IdText
    merchant_id: IdText
    offer_type: OfferType
    featured: bool = False
    image_tint: TintText
    image_label: Annotated[str, line(40)]
    image_alt: Annotated[str, line(200)]
    source_url: UrlText | None = None
    postage_kind: PostageKind
    postage_amount_minor: int | None = Field(default=None, ge=0, le=MAX_POSTAGE_MINOR)
    postage_currency: Currency | None = None
    requires_card: bool = False
    subscription_terms: Annotated[str, paragraph(1000)] | None = None
    eligibility: Detail
    availability: Detail
    steps: list[StepIn] = Field(default_factory=list, max_length=MAX_STEPS)
    start_at: AwareDatetime | None = None
    end_at: AwareDatetime | None = None
    freshness: OfferFreshness | None = None
    publisher_notes: Notes | None = None


class UpdateOfferRequest(BaseModel):
    """Partial update: send only the fields to change. The slug and status cannot be changed."""

    model_config = ConfigDict(extra="forbid")

    title: Title | None = None
    summary: Summary | None = None
    description: list[Paragraph] | None = Field(default=None, max_length=MAX_PARAGRAPHS)
    category_id: IdText | None = None
    merchant_id: IdText | None = None
    offer_type: OfferType | None = None
    featured: bool | None = None
    image_tint: TintText | None = None
    image_label: Annotated[str, line(40)] | None = None
    image_alt: Annotated[str, line(200)] | None = None
    source_url: UrlText | None = None
    postage_kind: PostageKind | None = None
    postage_amount_minor: int | None = Field(default=None, ge=0, le=MAX_POSTAGE_MINOR)
    postage_currency: Currency | None = None
    requires_card: bool | None = None
    subscription_terms: Annotated[str, paragraph(1000)] | None = None
    eligibility: Detail | None = None
    availability: Detail | None = None
    steps: list[StepIn] | None = Field(default=None, max_length=MAX_STEPS)
    start_at: AwareDatetime | None = None
    end_at: AwareDatetime | None = None
    freshness: OfferFreshness | None = None
    publisher_notes: Notes | None = None

    @model_validator(mode="after")
    def supply_values(self) -> "UpdateOfferRequest":
        if not self.model_fields_set:
            raise ValueError("Supply at least one field")
        for name in self.model_fields_set - NULLABLE_ON_UPDATE:
            if getattr(self, name) is None:
                raise ValueError("This field cannot be null")
        return self


class PublishRequest(BaseModel):
    """Publish needs the ``review_token`` of the offer the editor loaded (409 ``offer_changed`` if stale)."""

    model_config = ConfigDict(extra="forbid")

    review_token: ReviewToken


class VerifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_token: ReviewToken
    note: Annotated[str, line(500)] | None = None


class CreateLinkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_-]{0,39}$")]
    destination_url: UrlText
    tracking_id: Annotated[str, line(100)] | None = None
    active: bool = True


class UpdateLinkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: (
        Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_-]{0,39}$")] | None
    ) = None
    destination_url: UrlText | None = None
    tracking_id: Annotated[str, line(100)] | None = None
    active: bool | None = None

    @model_validator(mode="after")
    def supply_values(self) -> "UpdateLinkRequest":
        if not self.model_fields_set:
            raise ValueError("Supply at least one field")
        for name in self.model_fields_set - {"tracking_id"}:
            if getattr(self, name) is None:
                raise ValueError("This field cannot be null")
        return self


class AffiliateLinkOut(BaseModel):
    id: str
    offer_id: str
    provider: str
    destination_url: str
    tracking_id: str | None
    active: bool
    created_at: datetime
    updated_at: datetime


class OfferSummaryOut(BaseModel):
    id: str
    title: str
    slug: str
    category_id: str
    merchant_id: str
    offer_type: OfferType
    status: OfferStatus
    featured: bool
    end_at: datetime | None
    last_verified_at: datetime | None
    updated_at: datetime


class OfferOut(OfferSummaryOut):
    review_token: str
    summary: str
    description: list[str]
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
    steps: list[dict[str, Any]]
    start_at: datetime | None
    freshness: OfferFreshness | None
    publisher_notes: str | None
    created_at: datetime
    links: list[AffiliateLinkOut]


class OffersPage(BaseModel):
    offers: list[OfferSummaryOut]
    total_count: int
    limit: int
    offset: int


class OffersPageResponse(BaseModel):
    success: Literal[True] = True
    data: OffersPage


class OfferResponse(BaseModel):
    success: Literal[True] = True
    data: OfferOut


class LinkResponse(BaseModel):
    success: Literal[True] = True
    data: AffiliateLinkOut


def link_out(record: AffiliateLinkRecord) -> AffiliateLinkOut:
    return AffiliateLinkOut(**record.__dict__)


def summary_out(record: OfferRecord) -> OfferSummaryOut:
    return OfferSummaryOut.model_validate(record, from_attributes=True)


def offer_out(detail: OfferDetail) -> OfferOut:
    fields = {
        **detail.offer.__dict__,
        "links": [link_out(i) for i in detail.links],
        "review_token": detail.review_token,
    }
    return OfferOut(**fields)
