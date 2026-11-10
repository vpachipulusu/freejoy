"""One Awin promotion, read as untrusted input.

``parse_promotion`` takes whatever the API returned for one item and gives back either a ``Promotion``
(every field validated and normalised) or a ``Rejected`` carrying a reason code and nothing else: no
field value, URL or copy ever leaves this module in a rejection. Nothing is trusted:

* **Types.** The item, its ids and its flags must have exactly the JSON types the documentation
  names; an id is a positive integer (not a string, not a boolean), ``advertiser.joined`` must be the
  boolean ``true`` (``false`` is ``not_joined``).
* **Text** goes through ``app.core.plain_text``: markup removed, entities decoded once, control and
  invisible characters dropped, whitespace collapsed, lengths bounded (the limits are the admin
  API's). A title or name that is empty after cleaning rejects the item; anything over
  ``MAX_RAW_LENGTH`` rejects it as ``field_too_long``.
* **Dates** are ``YYYY-MM-DDTHH:MM:SS[.fraction][Z]`` read as UTC and nothing else (no offsets, no
  date-only, years 2000 to 2100); an end that is not after the start, or is already past, rejects the
  item. A missing or empty date is open-ended.
* **URLs.** ``url`` (optional) and ``urlTracking`` (required) go through ``validate_https_url``;
  ``urlTracking`` must also be on an Awin host (``affiliate_hosts``, subdomains included, look-alikes
  such as ``awin1.com.evil.example`` refused) and ``url`` must not be on any network's tracking host.
* **Regions.** The request asks for GB, but an item that says it is for somewhere else is refused.
* **Voucher codes are not stored.** The offer model has no field for one and putting a code in free
  text would hide it from review, so only ``has_voucher_code`` survives.

``source_hash`` fingerprints the normalised promotion (not the raw payload), so a key that appears
in the response but is not used never counts as a change at the source.
"""

import enum
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from app.core.affiliate_hosts import destination_allowed, host_matches
from app.core.catalogue_urls import InvalidUrl, validate_https_url
from app.core.plain_text import TextTooLong, fit_line, plain_line, plain_paragraphs
from app.services.awin_tracking import NETWORK_HOSTS, tracking_problem

PROVIDER = "awin"
HASH_VERSION = (
    2  # bump when the normalisation changes: every promotion then reads as changed once
)

MAX_ID = 10**12
MAX_TITLE_LENGTH = 200
MAX_SUMMARY_LENGTH = 300
MAX_PARAGRAPH_LENGTH = 2000
MAX_DESCRIPTION_PARAGRAPHS = 20
MAX_TERMS_LENGTH = 500
MAX_ADVERTISER_NAME_LENGTH = 120
MAX_VOUCHER_CODE_LENGTH = 500
MIN_YEAR, MAX_YEAR = 2000, 2100
REGION = "GB"
SUPPORTED_TYPES = frozenset({"promotion", "voucher"})
_DATE = re.compile(
    r"([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})"
    r"(?:\.([0-9]{1,9}))?Z?",
    re.ASCII,
)
MICROSECOND_DIGITS = 6


class SkipReason(enum.StrEnum):
    """Why an item was not imported. These codes are all a rejection reveals."""

    INVALID_ITEM = "invalid_item"
    INVALID_ID = "invalid_id"
    UNSUPPORTED_TYPE = "unsupported_type"
    INVALID_ADVERTISER = "invalid_advertiser"
    NOT_JOINED = "not_joined"
    INVALID_TITLE = "invalid_title"
    INVALID_TEXT = "invalid_text"
    FIELD_TOO_LONG = "field_too_long"
    INVALID_DATE = "invalid_date"
    INVALID_WINDOW = "invalid_window"
    ENDED = "ended"
    INVALID_URL = "invalid_url"
    SOURCE_URL_ON_TRACKING_HOST = "source_url_on_tracking_host"
    MISSING_TRACKING_URL = "missing_tracking_url"
    INVALID_TRACKING_URL = "invalid_tracking_url"
    TRACKING_HOST_NOT_ALLOWED = "tracking_host_not_allowed"
    TRACKING_SHAPE_UNRECOGNISED = "tracking_shape_unrecognised"
    TRACKING_OWNER_MISMATCH = "tracking_owner_mismatch"
    TRACKING_DESTINATION_MISMATCH = "tracking_destination_mismatch"
    REGION_MISMATCH = "region_mismatch"
    INVALID_VOUCHER = "invalid_voucher"
    DUPLICATE_IN_FEED = "duplicate_in_feed"
    SLUG_UNAVAILABLE = "slug_unavailable"


@dataclass(frozen=True)
class Rejected:
    reason: SkipReason


@dataclass(frozen=True)
class Promotion:
    """A validated promotion. ``tracking_url`` is kept out of ``repr`` (it carries the publisher id)."""

    promotion_id: int
    kind: str
    advertiser_id: int
    advertiser_name: str
    title: str
    summary: str
    description: tuple[str, ...]
    terms: str | None
    start_at: datetime | None
    end_at: datetime | None
    source_url: str | None
    tracking_url: str = field(repr=False)
    has_voucher_code: bool


class _Reject(Exception):
    def __init__(self, reason: SkipReason) -> None:
        super().__init__(reason.value)
        self.reason = reason


def _positive_id(value: object, reason: SkipReason) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise _Reject(reason)
    if not 0 < value <= MAX_ID:
        raise _Reject(reason)
    return value


def _line(value: object, limit: int, reason: SkipReason) -> str:
    if not isinstance(value, str):
        raise _Reject(reason)
    try:
        text = plain_line(value, limit)
    except TextTooLong:
        raise _Reject(SkipReason.FIELD_TOO_LONG) from None
    if not text:
        raise _Reject(reason)
    return text


def _optional_text(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, str):
        raise _Reject(SkipReason.INVALID_TEXT)
    try:
        return plain_paragraphs(value, MAX_DESCRIPTION_PARAGRAPHS, MAX_PARAGRAPH_LENGTH)
    except TextTooLong:
        raise _Reject(SkipReason.FIELD_TOO_LONG) from None


def _terms(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise _Reject(SkipReason.INVALID_TEXT)
    try:
        return plain_line(value, MAX_TERMS_LENGTH) or None
    except TextTooLong:
        raise _Reject(SkipReason.FIELD_TOO_LONG) from None


def _advertiser(item: dict[str, Any]) -> tuple[int, str]:
    advertiser = item.get("advertiser")
    if not isinstance(advertiser, dict):
        raise _Reject(SkipReason.INVALID_ADVERTISER)
    advertiser_id = _positive_id(advertiser.get("id"), SkipReason.INVALID_ADVERTISER)
    joined = advertiser.get("joined")
    if joined is False:
        raise _Reject(SkipReason.NOT_JOINED)
    if joined is not True:
        raise _Reject(SkipReason.INVALID_ADVERTISER)
    name = _line(
        advertiser.get("name"),
        MAX_ADVERTISER_NAME_LENGTH,
        SkipReason.INVALID_ADVERTISER,
    )
    return advertiser_id, name


def parse_utc_timestamp(value: object) -> datetime | None:
    """``YYYY-MM-DDTHH:MM:SS[.fraction][Z]`` read as UTC (years 2000 to 2100), else ``None``.

    Shared by every Awin parser (promotions and transactions) so one rule decides what a timestamp is.
    """
    match = _DATE.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        return None
    year, month, day, hour, minute, second = (int(match[n]) for n in range(1, 7))
    micro = int((match[7] or "0").ljust(MICROSECOND_DIGITS, "0")[:MICROSECOND_DIGITS])
    if not MIN_YEAR <= year <= MAX_YEAR:
        return None
    try:
        return datetime(year, month, day, hour, minute, second, micro, tzinfo=UTC)
    except ValueError:
        return None


def _date(value: object) -> datetime | None:
    if value is None or value == "":
        return None
    parsed = parse_utc_timestamp(value)
    if parsed is None:
        raise _Reject(SkipReason.INVALID_DATE)
    return parsed


def _window(
    item: dict[str, Any], now: datetime
) -> tuple[datetime | None, datetime | None]:
    start, end = _date(item.get("startDate")), _date(item.get("endDate"))
    if start is not None and end is not None and end <= start:
        raise _Reject(SkipReason.INVALID_WINDOW)
    if end is not None and end <= now:
        raise _Reject(SkipReason.ENDED)
    return start, end


def _url_text(value: object) -> object | None:
    """The stripped URL text, ``None`` when there is none; a non-string is returned for validation to refuse."""
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    return value


def _source_url(item: dict[str, Any]) -> str | None:
    text = _url_text(item.get("url"))
    if text is None:
        return None
    try:
        url = validate_https_url(text)
    except InvalidUrl:
        raise _Reject(SkipReason.INVALID_URL) from None
    if host_matches(urlsplit(url).hostname or "", NETWORK_HOSTS):
        raise _Reject(SkipReason.SOURCE_URL_ON_TRACKING_HOST)
    return url


def _tracking_url(
    item: dict[str, Any], publisher_id: int, advertiser_id: int, source_url: str | None
) -> str:
    text = _url_text(item.get("urlTracking"))
    if text is None:
        raise _Reject(SkipReason.MISSING_TRACKING_URL)
    try:
        url = validate_https_url(text)
    except InvalidUrl:
        raise _Reject(SkipReason.INVALID_TRACKING_URL) from None
    host = urlsplit(url).hostname or ""
    if not destination_allowed(
        PROVIDER, host, merchant_website=None, allow_sample=False
    ):
        raise _Reject(SkipReason.TRACKING_HOST_NOT_ALLOWED)
    problem = tracking_problem(
        url,
        publisher_id=publisher_id,
        advertiser_id=advertiser_id,
        source_url=source_url,
    )
    if problem is not None:
        raise _Reject(SkipReason(problem))
    return url


def _check_region(item: dict[str, Any]) -> None:
    regions = item.get("regions")
    if regions is None:
        return
    if isinstance(regions, dict):
        listed = regions.get("list")
        if regions.get("all") is True or (
            isinstance(listed, list)
            and any(
                isinstance(r, dict) and r.get("countryCode") == REGION for r in listed
            )
        ):
            return
    raise _Reject(SkipReason.REGION_MISMATCH)


def _has_voucher_code(item: dict[str, Any]) -> bool:
    voucher = item.get("voucher")
    if voucher is None or item.get("type") != "voucher":
        return False
    if not isinstance(voucher, dict):
        raise _Reject(SkipReason.INVALID_VOUCHER)
    code = voucher.get("code")
    if code is None:
        return False
    if not isinstance(code, str) or len(code) > MAX_VOUCHER_CODE_LENGTH:
        raise _Reject(SkipReason.INVALID_VOUCHER)
    return bool(code.strip())


def _build(item: dict[str, Any], now: datetime, publisher_id: int) -> Promotion:
    promotion_id = _positive_id(item.get("promotionId"), SkipReason.INVALID_ID)
    kind = item.get("type")
    if not isinstance(kind, str) or kind not in SUPPORTED_TYPES:
        raise _Reject(SkipReason.UNSUPPORTED_TYPE)
    advertiser_id, advertiser_name = _advertiser(item)
    title = _line(item.get("title"), MAX_TITLE_LENGTH, SkipReason.INVALID_TITLE)
    description = _optional_text(item.get("description"))
    terms = _terms(item.get("terms"))
    start_at, end_at = _window(item, now)
    _check_region(item)
    source_url = _source_url(item)
    return Promotion(
        promotion_id=promotion_id,
        kind=kind,
        advertiser_id=advertiser_id,
        advertiser_name=advertiser_name,
        title=title,
        summary=fit_line(description[0], MAX_SUMMARY_LENGTH) if description else title,
        description=description,
        terms=terms,
        start_at=start_at,
        end_at=end_at,
        source_url=source_url,
        tracking_url=_tracking_url(item, publisher_id, advertiser_id, source_url),
        has_voucher_code=_has_voucher_code(item),
    )


def parse_promotion(
    item: object, now: datetime, publisher_id: int
) -> Promotion | Rejected:
    """The validated promotion, or the reason it was refused. Never raises for bad input.

    ``publisher_id`` is ours: a tracking link must carry it (``awin_tracking``).
    """
    if not isinstance(item, dict):
        return Rejected(SkipReason.INVALID_ITEM)
    try:
        return _build(item, now, publisher_id)
    except _Reject as rejection:
        return Rejected(rejection.reason)


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value is not None else None


def source_hash(promotion: Promotion) -> str:
    """SHA-256 (hex) of the normalised promotion: what a change at the source means."""
    canonical = {
        "v": HASH_VERSION,
        "promotion_id": promotion.promotion_id,
        "kind": promotion.kind,
        "advertiser_id": promotion.advertiser_id,
        "title": promotion.title,
        "summary": promotion.summary,
        "description": list(promotion.description),
        "terms": promotion.terms,
        "start_at": _iso(promotion.start_at),
        "end_at": _iso(promotion.end_at),
        "source_url": promotion.source_url,
        "tracking_url": promotion.tracking_url,
        "has_voucher_code": promotion.has_voucher_code,
    }
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def tracking_outcome(item: object, publisher_id: int) -> str:
    """What the importer's tracking-link check says about this item's link, as if the programme were joined.

    ``accepted``, or the skip reason code (``SkipReason`` value) of the first check that refuses it: the
    advertiser id and the item's own ``url`` are read the way ``parse_promotion`` reads them, then
    ``_tracking_url`` runs. Used by the dry-run shape report, which counts these; it never returns text from
    the item.
    """
    if not isinstance(item, dict):
        return SkipReason.INVALID_ITEM.value
    advertiser = item.get("advertiser")
    try:
        advertiser_id = _positive_id(
            advertiser.get("id") if isinstance(advertiser, dict) else None,
            SkipReason.INVALID_ADVERTISER,
        )
        source_url = _source_url(item)
        _tracking_url(item, publisher_id, advertiser_id, source_url)
    except _Reject as rejection:
        return rejection.reason.value
    return "accepted"
