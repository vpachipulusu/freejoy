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
from app.partner.errors import REFUSAL_CODES

REF_PATTERN = r"^[A-Za-z0-9._-]{1,64}$"
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
    if not isinstance(value, str):
        raise ValueError("a date is an ISO 8601 string with a time zone")  # noqa: TRY004 - pydantic needs ValueError
    return value


IsoDateTime = Annotated[AwareDatetime, BeforeValidator(_strings_only)]
OfferTypeIn = Literal["FREE_SAMPLE", "FREE_TRIAL"]
PostageKindIn = Literal["FREE", "COST", "CHECK_TERMS"]
PartnerStatus = Literal["pending_review", "live", "ended", "declined"]
Outcome = Literal["created", "refreshed", "pending_update", "unchanged"]


class StepIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Annotated[str, _text(MAX_STEP_TITLE)]
    detail: Annotated[str, _text(MAX_STEP_DETAIL)]


class OfferSubmission(BaseModel):
    """The body of ``PUT /partner/v1/offers/{ref}``: the whole offer, every time (a PUT replaces)."""

    model_config = ConfigDict(extra="forbid")

    title: Annotated[str, _text(MAX_TITLE)]
    summary: Annotated[str, _text(MAX_SUMMARY)]
    description: list[Annotated[str, _text(MAX_PARAGRAPH)]] = Field(
        default_factory=list, max_length=MAX_PARAGRAPHS
    )
    offer_type: OfferTypeIn
    category: Annotated[
        str,
        StringConstraints(pattern=CATEGORY_SLUG_PATTERN, max_length=CATEGORY_SLUG_MAX),
    ]
    url: Annotated[str, StringConstraints(min_length=1, max_length=MAX_URL_LENGTH)]
    postage_kind: PostageKindIn
    postage_amount_minor: StrictInt | None = Field(
        default=None, ge=0, le=MAX_POSTAGE_MINOR
    )
    postage_currency: (
        Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")] | None
    ) = None
    requires_card: StrictBool
    subscription_terms: Annotated[str, _text(MAX_TERMS)] | None
    eligibility: Annotated[str, _text(MAX_ELIGIBILITY)]
    availability: Annotated[str, _text(MAX_AVAILABILITY)]
    steps: list[StepIn] = Field(default_factory=list, max_length=MAX_STEPS)
    start_at: IsoDateTime | None
    end_at: IsoDateTime | None


class SubmissionView(BaseModel):
    """What a retailer sees of one of its submissions."""

    ref: str
    status: PartnerStatus
    title: str
    # The page on the public site; only while ``status`` is ``live``.
    public_url: str | None
    # True while an update the retailer sent is waiting for an editor (the published page has not changed).
    update_pending: bool
    submitted_at: datetime
    ends_at: datetime | None


class SubmissionResult(SubmissionView):
    """The answer to a ``PUT``: what happened to it."""

    outcome: Outcome


class EndResult(SubmissionView):
    """The answer to ``end``: ``changed`` is false when the offer had already ended."""

    changed: bool


class SubmissionsPage(BaseModel):
    offers: list[SubmissionView]
    total_count: int
    limit: int
    offset: int


class SubmissionResponse(BaseModel):
    success: Literal[True] = True
    data: SubmissionView


class SubmissionResultResponse(BaseModel):
    success: Literal[True] = True
    data: SubmissionResult


class EndResultResponse(BaseModel):
    success: Literal[True] = True
    data: EndResult


class SubmissionsPageResponse(BaseModel):
    success: Literal[True] = True
    data: SubmissionsPage


class CodedErrorResponse(BaseModel):
    """An error with a fixed message and, for a rule refusal, a stable ``code`` a retailer's software can branch on.

    A body that is not the documented shape is a plain 422 with no ``code``."""

    success: Literal[False] = False
    error: str
    code: REFUSAL_CODES | None = None
