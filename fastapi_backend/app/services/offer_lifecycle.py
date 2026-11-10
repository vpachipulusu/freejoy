"""The offer status machine and the rules that gate publishing.

Transition table (rows: current status; ``-`` means refused with 409 ``illegal_transition``):

=========  =========  =========  =========  =========  ============
current    publish    unpublish  expire     archive    verify
=========  =========  =========  =========  =========  ============
DRAFT      PUBLISHED  -          -          ARCHIVED   DRAFT
PUBLISHED  -          DRAFT      EXPIRED    ARCHIVED   PUBLISHED
EXPIRED    PUBLISHED  -          -          ARCHIVED   EXPIRED
ARCHIVED   -          -          -          -          -
=========  =========  =========  =========  =========  ============

``ARCHIVED`` is terminal and an archived offer cannot be edited. Re-publishing an
``EXPIRED`` offer is allowed and re-runs every publish rule, so an editor must first move
``end_at`` into the future (or clear it) and verify the offer again if it was never verified.
"""

import enum
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from app.models.platform.offer import OfferStatus
from app.services.admin_catalogue_errors import IllegalTransition


class OfferAction(enum.StrEnum):
    PUBLISH = "publish"
    UNPUBLISH = "unpublish"
    EXPIRE = "expire"
    ARCHIVE = "archive"
    VERIFY = "verify"


S, A = OfferStatus, OfferAction

TRANSITIONS: Mapping[tuple[OfferStatus, OfferAction], OfferStatus] = {
    (S.DRAFT, A.PUBLISH): S.PUBLISHED,
    (S.DRAFT, A.ARCHIVE): S.ARCHIVED,
    (S.DRAFT, A.VERIFY): S.DRAFT,
    (S.PUBLISHED, A.UNPUBLISH): S.DRAFT,
    (S.PUBLISHED, A.EXPIRE): S.EXPIRED,
    (S.PUBLISHED, A.ARCHIVE): S.ARCHIVED,
    (S.PUBLISHED, A.VERIFY): S.PUBLISHED,
    (S.EXPIRED, A.PUBLISH): S.PUBLISHED,
    (S.EXPIRED, A.ARCHIVE): S.ARCHIVED,
    (S.EXPIRED, A.VERIFY): S.EXPIRED,
}


def next_status(current: OfferStatus, action: OfferAction) -> OfferStatus:
    """Status after ``action``, or ``IllegalTransition``."""
    target = TRANSITIONS.get((current, action))
    if target is None:
        raise IllegalTransition(current.value, action.value)
    return target


def require_editable(current: OfferStatus) -> None:
    """Content edits are refused once an offer is archived."""
    if current is OfferStatus.ARCHIVED:
        raise IllegalTransition(current.value, "update")


class PublishReason(enum.StrEnum):
    CATEGORY_DISABLED = "category_disabled"
    MERCHANT_INACTIVE = "merchant_inactive"
    NO_DESTINATION = "no_destination"
    END_DATE_PASSED = "end_date_passed"
    NOT_VERIFIED = "not_verified"
    COMMERCIAL_BASIS_REQUIRED = "commercial_basis_required"


@dataclass(frozen=True)
class PublishFacts:
    """What the publish rules need to know about an offer and what surrounds it."""

    category_enabled: bool
    merchant_active: bool
    active_links: int
    has_source_url: bool
    end_at: datetime | None
    last_verified_at: datetime | None
    # True when the offer's only active links are ``direct`` and the editor has not confirmed its basis.
    basis_unconfirmed: bool = False


def publish_blockers(facts: PublishFacts, now: datetime) -> list[PublishReason]:
    """Every unmet publish rule (empty when the offer may go live).

    Rules: the category is enabled, the merchant is active, there is at least one active
    affiliate link or a source URL, ``end_at`` is null or in the future, and the offer has
    been verified (``last_verified_at`` set; publishing never stamps it itself), and an offer whose only
    active links are ``direct`` (no network is paid for it, so the basis is a judgement call) has had its
    commercial basis confirmed by the editor in the publish request.
    """
    blockers: list[PublishReason] = []
    if not facts.category_enabled:
        blockers.append(PublishReason.CATEGORY_DISABLED)
    if not facts.merchant_active:
        blockers.append(PublishReason.MERCHANT_INACTIVE)
    if facts.active_links < 1 and not facts.has_source_url:
        blockers.append(PublishReason.NO_DESTINATION)
    if facts.end_at is not None and facts.end_at <= now:
        blockers.append(PublishReason.END_DATE_PASSED)
    if facts.last_verified_at is None:
        blockers.append(PublishReason.NOT_VERIFIED)
    if facts.basis_unconfirmed:
        blockers.append(PublishReason.COMMERCIAL_BASIS_REQUIRED)
    return blockers
