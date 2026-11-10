import enum
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base
from app.models.platform.enums import enum_check, enum_column
from app.models.platform.mixins import IdMixin, TimestampMixin


class OfferType(enum.StrEnum):
    FREE_SAMPLE = "FREE_SAMPLE"
    FREE_TRIAL = "FREE_TRIAL"
    CASHBACK = "CASHBACK"
    VOUCHER = "VOUCHER"
    COMPETITION = "COMPETITION"


class CommercialBasis(enum.StrEnum):
    """The commercial relationship behind a listing, stated honestly to the editor.

    ``SPONSORED`` is the only value visitors are told about (a "Sponsored" label); the three-way
    value itself is never public.
    """

    # No payment or benefit of any kind.
    EDITORIAL = "EDITORIAL"
    # Commission through an affiliate network.
    AFFILIATE = "AFFILIATE"
    # The merchant pays or gives a benefit for the listing or its placement.
    SPONSORED = "SPONSORED"


class OfferStatus(enum.StrEnum):
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    EXPIRED = "EXPIRED"
    ARCHIVED = "ARCHIVED"


class OfferFreshness(enum.StrEnum):
    """Editorial label set by the publisher; never computed from the clock."""

    NEW_TODAY = "NEW_TODAY"
    JUST_ADDED = "JUST_ADDED"
    POPULAR = "POPULAR"
    ENDS_SOON = "ENDS_SOON"
    LIMITED = "LIMITED"


class PostageKind(enum.StrEnum):
    FREE = "FREE"
    COST = "COST"
    CHECK_TERMS = "CHECK_TERMS"


# Postage amount and currency are present exactly when the postage costs something.
POSTAGE_COST_COMPLETE = (
    "(postage_kind = 'COST' AND postage_amount_minor IS NOT NULL"
    " AND postage_amount_minor >= 0 AND postage_currency IS NOT NULL"
    " AND char_length(postage_currency) = 3"
    " AND postage_currency = upper(postage_currency))"
    " OR (postage_kind <> 'COST' AND postage_amount_minor IS NULL"
    " AND postage_currency IS NULL)"
)


class Offer(IdMixin, TimestampMixin, Base):
    """A freebie, trial, cashback, voucher or competition shown on the site."""

    __tablename__ = "offers"
    __table_args__ = (
        Index("ix_offers_status_end_at", "status", "end_at"),
        Index("ix_offers_category_id_status", "category_id", "status"),
        CheckConstraint(POSTAGE_COST_COMPLETE, name="postage_cost_complete"),
        # The page always renders "Last checked", so a live offer must have a date.
        CheckConstraint(
            "status <> 'PUBLISHED' OR last_verified_at IS NOT NULL",
            name="published_is_verified",
        ),
        enum_check("offer_type", OfferType),
        enum_check("commercial_basis", CommercialBasis),
        enum_check("status", OfferStatus),
        enum_check("postage_kind", PostageKind),
        enum_check("freshness", OfferFreshness),
    )

    title: Mapped[str]
    slug: Mapped[str] = mapped_column(unique=True)
    # One line under the title.
    summary: Mapped[str]
    # Original editorial paragraphs, as a JSON list of strings.
    description: Mapped[list[Any]] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb")
    )
    # Restrict: a category or merchant with offers cannot be deleted by accident.
    category_id: Mapped[str] = mapped_column(
        ForeignKey("categories.id", ondelete="RESTRICT")
    )
    merchant_id: Mapped[str] = mapped_column(
        ForeignKey("merchants.id", ondelete="RESTRICT"), index=True
    )
    offer_type: Mapped[OfferType] = mapped_column(enum_column(OfferType))
    # Deliberately no default: every writer states it (the admin form requires it, the Awin importer
    # writes AFFILIATE), so a basis is never assumed at publish time.
    commercial_basis: Mapped[CommercialBasis] = mapped_column(
        enum_column(CommercialBasis)
    )
    status: Mapped[OfferStatus] = mapped_column(
        enum_column(OfferStatus), server_default=OfferStatus.DRAFT.value
    )
    featured: Mapped[bool] = mapped_column(server_default=text("false"))

    # Illustrated placeholder: tint key, short text drawn inside it, and its alt text.
    image_tint: Mapped[str]
    image_label: Mapped[str]
    image_alt: Mapped[str]
    source_url: Mapped[str | None]

    postage_kind: Mapped[PostageKind] = mapped_column(enum_column(PostageKind))
    # Minor units (pence); with `postage_currency` only when `postage_kind` is COST.
    postage_amount_minor: Mapped[int | None]
    postage_currency: Mapped[str | None]

    requires_card: Mapped[bool] = mapped_column(server_default=text("false"))
    # What the visitor commits to and how to cancel (trials and subscriptions).
    subscription_terms: Mapped[str | None]
    eligibility: Mapped[str]
    availability: Mapped[str]
    # Claim steps as a JSON list of {"title": ..., "detail": ...}.
    steps: Mapped[list[Any]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))

    start_at: Mapped[datetime | None]
    end_at: Mapped[datetime | None]
    # When an editor last checked the offer; shown as "Last checked".
    last_verified_at: Mapped[datetime | None]
    freshness: Mapped[OfferFreshness | None] = mapped_column(
        enum_column(OfferFreshness)
    )
    publisher_notes: Mapped[str | None]
