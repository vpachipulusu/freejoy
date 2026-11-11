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
    CommercialBasis,
    OfferFreshness,
    OfferStatus,
    OfferType,
    PostageKind,
)
from app.repositories.admin_offers import (
    AffiliateLinkRecord,
    OfferProvenance,
    OfferRecord,
    PartnerSubmissionRecord,
)
from app.services.admin_offers import OfferDetail
from app.services.offer_rules import needs_basis_confirmation

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
    # Required, with no default: the editor states the commercial relationship for every new offer.
    commercial_basis: CommercialBasis
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
    """Partial update: send only the fields to change. The slug and status cannot be changed.

    ``review_token`` is the token of the offer the editor loaded. It is needed only when the request changes
    ``commercial_basis`` (409 ``offer_changed`` without it or when it is stale, so an old form cannot undo a
    sponsorship); any other edit works without it. It is not a field of the offer.
    """

    model_config = ConfigDict(extra="forbid")

    review_token: ReviewToken | None = None

    title: Title | None = None
    summary: Summary | None = None
    description: list[Paragraph] | None = Field(default=None, max_length=MAX_PARAGRAPHS)
    category_id: IdText | None = None
    merchant_id: IdText | None = None
    offer_type: OfferType | None = None
    commercial_basis: CommercialBasis | None = None
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
        fields = self.model_fields_set - {"review_token"}
        if not fields:
            raise ValueError("Supply at least one field")
        for name in fields - NULLABLE_ON_UPDATE:
            if getattr(self, name) is None:
                raise ValueError("This field cannot be null")
        return self


class PublishRequest(BaseModel):
    """Publish needs the ``review_token`` of the offer the editor loaded (409 ``offer_changed`` if stale).

    ``commercial_basis`` is the basis the editor confirms. It is required (422 ``publish_blocked``, reason
    ``commercial_basis_required``) when the offer's only active links are ``direct``; whenever it is sent it must be
    the stored one (409 ``offer_changed``).
    """

    model_config = ConfigDict(extra="forbid")

    review_token: ReviewToken
    commercial_basis: CommercialBasis | None = None


class DismissSourceChangeRequest(BaseModel):
    """Needs the ``review_token`` of the offer the editor loaded and the ``source_changed_at`` it showed.

    The token does not cover the marker, so the marker is sent too: either one stale is 409
    ``offer_changed``. ``source_changed_at`` must carry a timezone (``provenance.source_changed_at`` of the
    offer read, sent back unchanged).
    """

    model_config = ConfigDict(extra="forbid")

    review_token: ReviewToken
    source_changed_at: AwareDatetime


class PartnerUpdateRequest(BaseModel):
    """Apply or dismiss the update a retailer sent for this offer.

    Needs the ``review_token`` of the offer the editor loaded and the ``pending_hash`` of the proposal the editor was
    shown (409 ``offer_changed`` when either is stale, so nothing is applied or dismissed blind).
    """

    model_config = ConfigDict(extra="forbid")

    review_token: ReviewToken
    pending_hash: Annotated[
        str, StringConstraints(min_length=64, max_length=64, pattern=r"^[0-9a-f]+$")
    ]


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


class OfferProvenanceOut(BaseModel):
    """Where an imported offer came from.

    ``source_changed_at`` is set while the source has moved on and nobody has reviewed it: the
    importer sets it and "dismiss source change" clears it.
    """

    provider: str
    external_id: str
    applied_at: datetime
    source_changed_at: datetime | None


class PartnerPendingOut(BaseModel):
    """What a retailer proposed for an offer an editor has touched: the sanitised content and its hash.

    ``payload`` has the keys of ``app.partner.content.CleanContent.payload`` (dates as ISO strings).
    """

    hash: str
    submitted_at: datetime
    payload: dict[str, Any]


class PartnerSubmissionOut(BaseModel):
    """The retailer behind an offer submitted through the partner API."""

    ref: str
    merchant_id: str
    merchant_name: str
    submitted_at: datetime
    pending: PartnerPendingOut | None


class OfferSummaryOut(BaseModel):
    id: str
    title: str
    slug: str
    category_id: str
    merchant_id: str
    offer_type: OfferType
    commercial_basis: CommercialBasis
    status: OfferStatus
    featured: bool
    end_at: datetime | None
    last_verified_at: datetime | None
    updated_at: datetime
    # Both null for an offer made by hand.
    import_provider: str | None
    source_changed_at: datetime | None
    # The retailer's own ref, for an offer submitted through the partner API (its "Submitted by retailer" marker).
    partner_ref: str | None


class OfferOut(OfferSummaryOut):
    review_token: str
    # True when publishing needs the editor to confirm the commercial basis (see ``needs_basis_confirmation``).
    needs_basis_confirmation: bool
    # True while the stored commercial basis is the placeholder of a retailer's draft that no editor has put live:
    # nobody chose it, so a form must not present it as the current basis.
    basis_is_placeholder: bool
    provenance: OfferProvenanceOut | None
    # Null unless the offer was submitted through the partner API.
    partner_submission: PartnerSubmissionOut | None
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


class OfferWriteOut(OfferOut):
    """The offer after a write, plus the token of the state the write replaced.

    Read under the same row lock, before the write. If it is not the token the editor loaded, somebody else
    changed the offer first and the response already contains their change.
    """

    previous_review_token: str


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


class OfferWriteResponse(BaseModel):
    success: Literal[True] = True
    data: OfferWriteOut


class LinkResponse(BaseModel):
    success: Literal[True] = True
    data: AffiliateLinkOut


def link_out(record: AffiliateLinkRecord) -> AffiliateLinkOut:
    return AffiliateLinkOut(**record.__dict__)


def _import_fields(
    provenance: OfferProvenance | None, partner_ref: str | None = None
) -> dict[str, Any]:
    return {
        "partner_ref": partner_ref,
        "import_provider": provenance.provider if provenance else None,
        "source_changed_at": provenance.source_changed_at if provenance else None,
    }


def summary_out(
    record: OfferRecord,
    provenance: OfferProvenance | None,
    partner_ref: str | None = None,
) -> OfferSummaryOut:
    return OfferSummaryOut(**record.__dict__, **_import_fields(provenance, partner_ref))


def partner_out(record: PartnerSubmissionRecord | None) -> PartnerSubmissionOut | None:
    if record is None:
        return None
    pending = None
    if (
        record.pending_payload is not None
        and record.pending_hash is not None
        and record.pending_at is not None
    ):
        pending = PartnerPendingOut(
            hash=record.pending_hash,
            submitted_at=record.pending_at,
            payload=record.pending_payload,
        )
    return PartnerSubmissionOut(
        ref=record.ref,
        merchant_id=record.merchant_id,
        merchant_name=record.merchant_name,
        submitted_at=record.submitted_at,
        pending=pending,
    )


def _offer_fields(detail: OfferDetail) -> dict[str, Any]:
    provenance = detail.provenance
    return {
        **detail.offer.__dict__,
        **_import_fields(provenance, detail.partner.ref if detail.partner else None),
        "links": [link_out(i) for i in detail.links],
        "review_token": detail.review_token,
        "basis_is_placeholder": detail.partner is not None
        and detail.partner.first_published_at is None,
        "needs_basis_confirmation": detail.partner is not None
        or needs_basis_confirmation(
            (link.provider, link.active) for link in detail.links
        ),
        "provenance": OfferProvenanceOut(**provenance.__dict__) if provenance else None,
        "partner_submission": partner_out(detail.partner),
    }


def offer_out(detail: OfferDetail) -> OfferOut:
    return OfferOut(**_offer_fields(detail))


def offer_write_out(detail: OfferDetail) -> OfferWriteOut:
    if detail.previous_review_token is None:
        raise ValueError("a write response needs the token of the state it replaced")
    return OfferWriteOut(
        **_offer_fields(detail), previous_review_token=detail.previous_review_token
    )
