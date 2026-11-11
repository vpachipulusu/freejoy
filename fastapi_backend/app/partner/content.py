"""Turning a validated partner submission into the content a draft offer is written with (ADR-013, FREEJ-58).

Pure: no I/O. The route layer has already checked the shape (``app.partner.schemas``); this module cleans every text
through the shared plain-text function (``app.core.plain_text``: markup removed, entities decoded once, control and
invisible characters dropped, whitespace collapsed), validates the link through the shared URL function and the host
rule (``app.core.catalogue_urls``, ``app.core.affiliate_hosts``) and applies the shared postage and window rules
(``app.core.offer_content``). What comes back, ``CleanContent``, is the one thing that is ever stored: a create writes
it, a refresh of an untouched draft writes it, and a pending update stores its ``payload`` (never the raw request).
Its ``hash`` is the SHA-256 of that payload, so sending the same content twice is recognisable.

Every refusal is a ``SubmissionRefused`` with a fixed code; nothing the caller sent is put in it.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from app.core.affiliate_hosts import DIRECT_PROVIDER, destination_allowed
from app.core.catalogue_urls import InvalidUrl, validate_https_url
from app.core.offer_content import postage_problem, window_problem
from app.core.plain_text import TextTooLong, plain_line
from app.partner.errors import SubmissionRefused
from app.partner.schemas import (
    MAX_AVAILABILITY,
    MAX_ELIGIBILITY,
    MAX_PARAGRAPH,
    MAX_STEP_DETAIL,
    MAX_STEP_TITLE,
    MAX_SUMMARY,
    MAX_TERMS,
    MAX_TITLE,
    OfferSubmission,
)

FREE_TRIAL = "FREE_TRIAL"
MAX_YEAR = 2100


@dataclass(frozen=True)
class CleanContent:
    """The sanitised, validated content of one submission. Immutable: ``payload`` is a fresh dict each time."""

    title: str
    summary: str
    description: tuple[str, ...]
    category_id: str
    offer_type: str
    postage_kind: str
    postage_amount_minor: int | None
    postage_currency: str | None
    requires_card: bool
    subscription_terms: str | None
    eligibility: str
    availability: str
    steps: tuple[tuple[str, str], ...]
    start_at: datetime | None
    end_at: datetime | None
    destination_url: str

    def payload(self) -> dict[str, Any]:
        """The JSON form that is stored as ``pending_payload`` and hashed (dates as UTC ISO strings)."""
        return {
            "title": self.title,
            "summary": self.summary,
            "description": list(self.description),
            "category_id": self.category_id,
            "offer_type": self.offer_type,
            "postage_kind": self.postage_kind,
            "postage_amount_minor": self.postage_amount_minor,
            "postage_currency": self.postage_currency,
            "requires_card": self.requires_card,
            "subscription_terms": self.subscription_terms,
            "eligibility": self.eligibility,
            "availability": self.availability,
            "steps": [
                {"title": title, "detail": detail} for title, detail in self.steps
            ],
            "start_at": _iso(self.start_at),
            "end_at": _iso(self.end_at),
            "destination_url": self.destination_url,
        }

    @property
    def hash(self) -> str:
        return hash_payload(self.payload())

    def offer_values(self) -> dict[str, Any]:
        """The columns of ``offers`` a create or a refresh writes from this content (dates as datetimes)."""
        values = self.payload()
        values.pop("destination_url")
        values["start_at"], values["end_at"] = self.start_at, self.end_at
        return values


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value is not None else None


def hash_payload(payload: dict[str, Any]) -> str:
    text = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _line(raw: str, maximum: int) -> str:
    """One cleaned line, never empty: a text that is nothing but markup or invisible characters is refused."""
    try:
        cleaned = plain_line(raw, maximum)
    except TextTooLong:
        raise SubmissionRefused("empty_text") from None
    if not cleaned:
        raise SubmissionRefused("empty_text")
    return cleaned


def _optional_line(raw: str | None, maximum: int) -> str | None:
    return None if raw is None else _line(raw, maximum)


def _paragraphs(raw: list[str]) -> tuple[str, ...]:
    """Each paragraph cleaned to one line; one that is nothing but markup is dropped, not refused."""
    cleaned: list[str] = []
    for item in raw:
        try:
            line = plain_line(item, MAX_PARAGRAPH)
        except TextTooLong:
            raise SubmissionRefused("empty_text") from None
        if line:
            cleaned.append(line)
    return tuple(cleaned)


def clean_destination(raw: str, merchant_website: str | None) -> str:
    """The link's normalised https URL, provided its host is exactly the merchant's website host or its www form.

    The partner API can never write ``merchants.website``, so the retailer cannot widen what it may link to.
    """
    try:
        url = validate_https_url(raw)
    except InvalidUrl:
        raise SubmissionRefused("invalid_url") from None
    host = urlsplit(url).hostname or ""
    allowed = destination_allowed(
        DIRECT_PROVIDER, host, merchant_website=merchant_website, allow_sample=False
    )
    if not allowed:
        raise SubmissionRefused("link_host_not_allowed")
    return url


def _normalised(value: datetime | None) -> datetime | None:
    """The date in UTC; one that cannot be written in UTC (year 1 with an offset) or is past ``MAX_YEAR`` is refused."""
    if value is None:
        return None
    try:
        moved = value.astimezone(UTC)
    except OverflowError:
        raise SubmissionRefused("invalid_window") from None
    if moved.year > MAX_YEAR:
        raise SubmissionRefused("invalid_window")
    return moved


def _check_money_and_window(
    submission: OfferSubmission,
    start_at: datetime | None,
    end_at: datetime | None,
    now: datetime,
) -> None:
    postage = {
        "postage_kind": submission.postage_kind,
        "postage_amount_minor": submission.postage_amount_minor,
        "postage_currency": submission.postage_currency,
    }
    problem = postage_problem(postage) or window_problem(start_at, end_at)
    if problem is None and end_at is not None and end_at <= now:
        problem = "invalid_window"  # a submission that has already ended is of no use to anyone
    if problem is not None:
        raise SubmissionRefused(problem)


def clean_submission(
    submission: OfferSubmission,
    *,
    category_id: str,
    merchant_website: str | None,
    now: datetime,
) -> CleanContent:
    """The content to store for ``submission``, or ``SubmissionRefused`` with the code of the first rule it breaks."""
    start_at, end_at = _normalised(submission.start_at), _normalised(submission.end_at)
    _check_money_and_window(submission, start_at, end_at, now)
    terms = _optional_line(submission.subscription_terms, MAX_TERMS)
    if submission.offer_type == FREE_TRIAL and terms is None:
        raise SubmissionRefused("subscription_terms_required")
    return CleanContent(
        title=_line(submission.title, MAX_TITLE),
        summary=_line(submission.summary, MAX_SUMMARY),
        description=_paragraphs(submission.description),
        category_id=category_id,
        offer_type=submission.offer_type,
        postage_kind=submission.postage_kind,
        postage_amount_minor=submission.postage_amount_minor,
        postage_currency=submission.postage_currency,
        requires_card=submission.requires_card,
        subscription_terms=terms,
        eligibility=_line(submission.eligibility, MAX_ELIGIBILITY),
        availability=_line(submission.availability, MAX_AVAILABILITY),
        steps=tuple(
            (_line(step.title, MAX_STEP_TITLE), _line(step.detail, MAX_STEP_DETAIL))
            for step in submission.steps
        ),
        start_at=start_at,
        end_at=end_at,
        destination_url=clean_destination(submission.url, merchant_website),
    )
