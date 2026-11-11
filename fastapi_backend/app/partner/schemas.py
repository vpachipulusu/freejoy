"""Request and response models of the partner offer endpoints (ADR-013, FREEJ-58).

The request is strict: ``extra="forbid"`` (an unknown or misspelled field is refused, not ignored), a length cap on
every text, enums for every choice, real booleans and integers (no ``"yes"`` or ``"5"``), and ISO date strings only.
A text is capped at the length it is stored at, so cleaning (``app.partner.content``) never has to cut a retailer's
sentence short. Postage, card requirement, subscription terms, eligibility, availability and both dates have no
default: a retailer must say each one, and may say ``null`` where "none" is a true answer (``subscription_terms``,
``start_at`` and ``end_at``).
"""

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StringConstraints,
)

from app.core.catalogue_urls import MAX_URL_LENGTH
from app.partner.error_models import PartnerErrorResponse
from app.partner.errors import REFUSAL_CODES
from app.partner.statuses import PartnerStatus

REF_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"
MAX_TITLE = 120
MAX_SUMMARY = 200
MAX_PARAGRAPHS = 5
MAX_PARAGRAPH = 600
MAX_ELIGIBILITY = 400
MAX_AVAILABILITY = 300
MAX_TERMS = 600
MAX_STEPS = 6
MAX_STEP_TITLE = 80
MAX_STEP_DETAIL = 300
MAX_POSTAGE_MINOR = 100_000
CATEGORY_SLUG_PATTERN = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
CATEGORY_SLUG_MAX = 80
MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 20

Ref = Annotated[str, StringConstraints(pattern=REF_PATTERN)]


def _text(maximum: int) -> Any:
    return StringConstraints(min_length=1, max_length=maximum)


def _strings_only(value: Any) -> Any:
    """Dates are ISO strings: refuse a number (pydantic would read it as a Unix time) or anything else."""
    if not isinstance(value, str) or "T" not in value.upper():
        raise ValueError("a date is an ISO 8601 string with a time zone")
    return value


IsoDateTime = Annotated[AwareDatetime, BeforeValidator(_strings_only)]
OfferTypeIn = Literal["FREE_SAMPLE", "FREE_TRIAL"]
PostageKindIn = Literal["FREE", "COST", "CHECK_TERMS"]
Outcome = Literal["created", "refreshed", "pending_update", "unchanged"]


class StepIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Annotated[str, _text(MAX_STEP_TITLE)] = Field(
        description="A short heading for the step, up to 80 characters of plain text."
    )
    detail: Annotated[str, _text(MAX_STEP_DETAIL)] = Field(
        description="What the visitor does in this step, up to 300 characters of plain text."
    )


class OfferSubmission(BaseModel):
    """The body of ``PUT /partner/v1/offers/{ref}``: the whole offer, every time (a PUT replaces)."""

    model_config = ConfigDict(extra="forbid")

    title: Annotated[str, _text(MAX_TITLE)] = Field(
        description="The offer's title, up to 120 characters of plain text (markup is removed)."
    )
    summary: Annotated[str, _text(MAX_SUMMARY)] = Field(
        description="One line under the title, up to 200 characters."
    )
    description: list[Annotated[str, _text(MAX_PARAGRAPH)]] = Field(
        default_factory=list,
        max_length=MAX_PARAGRAPHS,
        description="Up to 5 paragraphs of up to 600 characters. Optional; omitting it and sending [] are the same.",
    )
    offer_type: OfferTypeIn = Field(
        description="FREE_SAMPLE or FREE_TRIAL. No other kind of offer can be submitted."
    )
    category: Annotated[
        str,
        StringConstraints(pattern=CATEGORY_SLUG_PATTERN, max_length=CATEGORY_SLUG_MAX),
    ] = Field(
        description="The slug of one of our categories that is currently open to submissions."
    )
    url: Annotated[str, StringConstraints(min_length=1, max_length=MAX_URL_LENGTH)] = (
        Field(
            description="Where the visitor claims the offer: an https address on your own website's host (or its www form)."
        )
    )
    postage_kind: PostageKindIn = Field(
        description="FREE, COST (the visitor pays postage; send the amount and currency) or CHECK_TERMS."
    )
    postage_amount_minor: StrictInt | None = Field(
        default=None,
        ge=0,
        le=MAX_POSTAGE_MINOR,
        description="The postage in the smallest currency unit (pence). Send it exactly when postage_kind is COST.",
    )
    postage_currency: (
        Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")] | None
    ) = Field(
        default=None,
        description="Three capital letters, for example GBP. Send it exactly when postage_kind is COST.",
    )
    requires_card: StrictBool = Field(
        description="True if the visitor has to give a payment card to claim the offer."
    )
    subscription_terms: Annotated[str, _text(MAX_TERMS)] | None = Field(
        description="What the visitor commits to and how to cancel, up to 600 characters. Required for FREE_TRIAL; send null for a sample with nothing to cancel."
    )
    eligibility: Annotated[str, _text(MAX_ELIGIBILITY)] = Field(
        description="Who can claim it, up to 400 characters."
    )
    availability: Annotated[str, _text(MAX_AVAILABILITY)] = Field(
        description="How long or where it is available, up to 300 characters."
    )
    steps: list[StepIn] = Field(
        default_factory=list,
        max_length=MAX_STEPS,
        description="Up to 6 steps to claim it. Optional.",
    )
    start_at: IsoDateTime | None = Field(
        description="When the offer starts, an ISO 8601 date-time with a time zone, or null. At most 90 days ahead."
    )
    end_at: IsoDateTime | None = Field(
        description="When the offer ends, an ISO 8601 date-time with a time zone, or null for no end date. Must be in the future."
    )


class SubmissionView(BaseModel):
    """What a retailer sees of one of its submissions."""

    ref: str = Field(description="Your own id for the offer, as you sent it.")
    status: PartnerStatus = Field(
        description=(
            "pending_review: with our editors, waiting to be published. live: published and not past its end date. "
            "ended: past its end date, or one of our editors retired it after it was live. "
            "declined: one of our editors turned it down before it was ever published; it is final, so send a new ref "
            "to submit again."
        )
    )
    title: str = Field(description="The title as it is stored now.")
    public_url: str | None = Field(
        description="The offer's page on our site. Present only while status is live."
    )
    update_pending: bool = Field(
        description=(
            "True while an update you sent is waiting for one of our editors, who may apply it or dismiss it. "
            "The published page has not changed. It going back to false does not mean the update was applied: "
            "read the offer's status and content. Sending content an editor dismissed again is answered "
            "unchanged and is not queued again; send changed content to propose it afresh."
        )
    )
    submitted_at: datetime = Field(
        description="When you last sent this offer (an ISO 8601 time, UTC)."
    )
    ends_at: datetime | None = Field(
        description="The offer's end date as stored, or null. Ending an offer sets it to the moment you called end."
    )


class SubmissionResult(SubmissionView):
    """The answer to a ``PUT``: what happened to it."""

    outcome: Outcome = Field(
        description=(
            "created: a new submission. refreshed: our editors had not touched it, so it took your new content. "
            "pending_update: our editors have edited, published or ended it, so your new content waits for them and the "
            "published page is unchanged. unchanged: nothing needed to change (the same content again, or content that "
            "matches what the offer already shows)."
        )
    )


class EndResult(SubmissionView):
    """The answer to ``end``: ``changed`` is false when the offer had already ended."""

    changed: bool = Field(
        description="True if this call ended the offer, false if it had already ended (nothing was changed)."
    )


class SubmissionsPage(BaseModel):
    offers: list[SubmissionView] = Field(
        description="This page of your submissions, newest first."
    )
    total_count: int = Field(description="How many submissions you have in all.")
    limit: int = Field(description="The page size that was used.")
    offset: int = Field(description="How many submissions were skipped.")


class SubmissionResponse(BaseModel):
    success: Literal[True] = Field(default=True, description="Always true.")
    data: SubmissionView = Field(description="The submission.")


class SubmissionResultResponse(BaseModel):
    success: Literal[True] = Field(default=True, description="Always true.")
    data: SubmissionResult = Field(
        description="The submission and what happened to it."
    )


class EndResultResponse(BaseModel):
    success: Literal[True] = Field(default=True, description="Always true.")
    data: EndResult = Field(description="The submission after the call.")


class SubmissionsPageResponse(BaseModel):
    success: Literal[True] = Field(default=True, description="Always true.")
    data: SubmissionsPage = Field(description="A page of submissions.")


class CodedErrorResponse(PartnerErrorResponse):
    """An error with a fixed message and, for a rule refusal, a stable ``code`` a retailer's software can branch on.

    A body that is not the documented shape is a plain 422 with no ``code``."""

    code: REFUSAL_CODES | None = Field(
        default=None,
        description=(
            "A stable code to branch on. open_submission_cap: you have 50 offers waiting for review and it clears only "
            "as our editors review them, not with time. daily_submission_cap: 20 new offers a day (UTC); try again "
            "tomorrow. daily_write_budget: 2,000 writes a day (UTC). offer_closed: one of our editors archived this "
            "offer, which is permanent; use a new ref."
        ),
    )
