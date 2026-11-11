"""The status a retailer sees for a submission (ADR-013, FREEJ-58): derived from the offer, never stored.

One pure function, so the mapping has one definition and one table test (``tests/api/partner/test_partner_statuses.py``).
Inputs are the offer's own status, whether it is past its end date on the database clock, and whether it was ever
published (``partner_submissions.first_published_at``, set by a trigger the first time the offer becomes PUBLISHED).

=================================  ==============  ===========================================================
the offer is                       the partner sees  why
=================================  ==============  ===========================================================
ARCHIVED, never published          declined         an editor turned the submission down
ARCHIVED, once published           ended            an editor retired a live offer
EXPIRED                            ended            the health job or an editor expired it
past its end date (any other)      ended            the public site already treats it as ended
PUBLISHED, not past its end date   live             on the site
DRAFT, not past its end date       pending_review   waiting for, or back with, an editor
=================================  ==============  ===========================================================
"""

from typing import Final, Literal

from app.core.offer_vocabulary import (
    STATUS_ARCHIVED,
    STATUS_DRAFT,
    STATUS_EXPIRED,
    STATUS_PUBLISHED,
)

PartnerStatus = Literal["pending_review", "live", "ended", "declined"]
PENDING_REVIEW: Final = "pending_review"
LIVE: Final = "live"
ENDED: Final = "ended"
DECLINED: Final = "declined"
PARTNER_STATUSES: Final = (PENDING_REVIEW, LIVE, ENDED, DECLINED)


def partner_status(
    *, offer_status: str, past_end: bool, ever_published: bool
) -> PartnerStatus:
    """The partner-facing status of an offer. An unknown ``offer_status`` is a bug and raises."""
    if offer_status == STATUS_ARCHIVED:
        return ENDED if ever_published else DECLINED
    if offer_status == STATUS_EXPIRED or past_end:
        return ENDED
    if offer_status == STATUS_PUBLISHED:
        return LIVE
    if offer_status == STATUS_DRAFT:
        return PENDING_REVIEW
    raise ValueError("unknown offer status")
