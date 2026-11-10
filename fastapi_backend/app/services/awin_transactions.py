"""One row of Awin's transactions report, read as untrusted input.

``parse_transaction`` takes whatever the API returned for one transaction and gives back a ``ReportRow``
(every field validated and normalised) or a ``Rejected`` carrying a reason code and nothing else: no
field value ever leaves this module in a rejection. Awin's documentation does not describe the response
body (only the request), so the field names below are an **assumption** that the first ``--dry-run``
confirms or breaks (``docs/developer/backend-network-reports.md``). Nothing is guessed: a row that does
not look exactly as assumed is rejected with a code, and the code is counted.

Assumed row shape::

    {"id": 5001, "advertiserId": 77, "transactionDate": "2026-11-03T10:15:00",
     "commissionStatus": "approved",
     "commissionAmount": {"amount": 1.5, "currency": "GBP"},
     "saleAmount": {"amount": 30.0, "currency": "GBP"},
     "clickRefs": {"clickRef": "..."}}              (a top-level "clickRef" is read when there is no object)

* **Ids** are positive integers (not strings, not booleans).
* **The date** is ``YYYY-MM-DDTHH:MM:SS[.fraction][Z]`` and is read as UTC, because the request asks for
  ``timezone=UTC``; any other shape (an offset, a date alone, a number) is ``invalid_date``.
* **The status** is one of Awin's ``pending``, ``approved``, ``declined``, ``paid`` or ``deleted``
  (case-insensitive), stored one to one; the value as written is kept (32 plain characters at most). Any
  other word is ``unknown_status``: it is counted, never mapped onto the nearest state.
* **Amounts** are decimal numbers or decimal strings in **major units** (``1.5`` is 1.50), converted to
  integer minor units with ``decimal.Decimal``, never by multiplying a float. Negative, non-finite,
  exponent notation, more than two decimals, or more than 999,999,999.99 is ``invalid_amount``.
  A missing amount is rejected too (a lead without a sale amount is not assumed to be zero).
* **The currency** is three upper-case ASCII letters, the same on both amounts, and one of
  ``TWO_DECIMAL_CURRENCIES``: minor units are x100 only for a two-decimal currency, so any other code
  (JPY, KWD, ...) is ``unsupported_currency`` rather than stored at the wrong scale.
* **The click reference** is at most 100 of ``[A-Za-z0-9_.-]``; empty or absent is ``None``.

``report_hash`` fingerprints the normalised row (not the raw payload), so a key that appears in the
response but is not used, a reordered object or a number written ``1.5`` or ``"1.50"`` never counts as a
change at the network.
"""

import enum
import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from app.models.platform.network_report import (
    MAX_CLICK_REF_LENGTH,
    MAX_STATUS_RAW_LENGTH,
    CommissionStatus,
)
from app.services.awin_promotions import PROVIDER, parse_utc_timestamp

__all__ = [
    "HASH_VERSION",
    "PROVIDER",
    "Rejected",
    "ReportRow",
    "SkipReason",
    "parse_transaction",
    "report_hash",
]

HASH_VERSION = 1  # bump when the normalisation changes: every stored row then reads as changed once
MAX_ID = 10**15
MAX_MINOR = 10**11 - 1  # 999,999,999.99 major units
MINOR_PER_MAJOR = 100
CENT = Decimal("0.01")
_DECIMAL_TEXT = re.compile(r"[0-9]{1,12}(?:\.[0-9]{1,12})?", re.ASCII)
_CURRENCY = re.compile(r"[A-Z]{3}", re.ASCII)
# ISO 4217 currencies with two minor-unit digits, the only ones whose amounts are stored x100.
TWO_DECIMAL_CURRENCIES = frozenset(
    {"GBP", "EUR", "USD", "AUD", "CAD", "CHF", "DKK", "NOK", "SEK", "PLN", "NZD"}
)
_STATUS_TEXT = re.compile(rf"[A-Za-z0-9 _-]{{1,{MAX_STATUS_RAW_LENGTH}}}", re.ASCII)
_CLICK_REF = re.compile(rf"[A-Za-z0-9_.-]{{1,{MAX_CLICK_REF_LENGTH}}}", re.ASCII)
_STATUSES = {status.value: status for status in CommissionStatus}


class SkipReason(enum.StrEnum):
    """Why a row was not imported. These codes are all a rejection reveals."""

    INVALID_ITEM = "invalid_item"
    INVALID_ID = "invalid_id"
    INVALID_ADVERTISER = "invalid_advertiser"
    INVALID_DATE = "invalid_date"
    INVALID_STATUS = "invalid_status"
    UNKNOWN_STATUS = "unknown_status"
    INVALID_AMOUNT = "invalid_amount"
    INVALID_CURRENCY = "invalid_currency"
    CURRENCY_MISMATCH = "currency_mismatch"
    UNSUPPORTED_CURRENCY = "unsupported_currency"
    INVALID_CLICK_REF = "invalid_click_ref"
    DUPLICATE_IN_FEED = "duplicate_in_feed"


@dataclass(frozen=True)
class Rejected:
    reason: SkipReason


@dataclass(frozen=True)
class ReportRow:
    """A validated transaction. Money is in minor units; ``transaction_at`` is aware (UTC)."""

    external_id: str
    advertiser_id: int
    transaction_at: datetime
    status: CommissionStatus
    status_raw: str
    commission_amount_minor: int
    sale_amount_minor: int
    currency: str
    click_ref: str | None


class _Reject(Exception):
    def __init__(self, reason: SkipReason) -> None:
        super().__init__(reason.value)
        self.reason = reason


def _positive_id(value: object, reason: SkipReason) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 0 < value <= MAX_ID:
        raise _Reject(reason)
    return value


def _status(value: object) -> tuple[CommissionStatus, str]:
    if not isinstance(value, str):
        raise _Reject(SkipReason.INVALID_STATUS)
    raw = value.strip()
    if _STATUS_TEXT.fullmatch(raw) is None:
        raise _Reject(SkipReason.INVALID_STATUS)
    mapped = _STATUSES.get(raw.lower())
    if mapped is None:
        raise _Reject(SkipReason.UNKNOWN_STATUS)
    return mapped, raw


def _decimal(value: object) -> Decimal:
    """The amount as an exact ``Decimal`` (a float goes through its shortest repr, never arithmetic)."""
    if isinstance(value, bool):
        raise _Reject(SkipReason.INVALID_AMOUNT)
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        if not math.isfinite(value) or value < 0:
            raise _Reject(SkipReason.INVALID_AMOUNT)
        return abs(Decimal(repr(value)))
    if isinstance(value, str) and _DECIMAL_TEXT.fullmatch(value):
        return Decimal(value)
    raise _Reject(SkipReason.INVALID_AMOUNT)


def _minor_units(value: object) -> int:
    try:
        amount = _decimal(value)
        # a negative, or more than two decimals (or too many digits to tell)
        if amount < 0 or amount != amount.quantize(CENT):
            raise _Reject(SkipReason.INVALID_AMOUNT)
        minor = int(amount * MINOR_PER_MAJOR)
    except (InvalidOperation, ValueError, OverflowError):
        raise _Reject(SkipReason.INVALID_AMOUNT) from None
    if minor > MAX_MINOR:
        raise _Reject(SkipReason.INVALID_AMOUNT)
    return minor


def _money(item: dict[str, Any], key: str) -> tuple[int, object]:
    """The minor units and the raw currency of ``item[key]`` (``{"amount": ..., "currency": ...}``)."""
    entry = item.get(key)
    if not isinstance(entry, dict) or "amount" not in entry:
        raise _Reject(SkipReason.INVALID_AMOUNT)
    return _minor_units(entry["amount"]), entry.get("currency")


def _currency(commission: object, sale: object) -> str:
    for value in (commission, sale):
        if not isinstance(value, str):
            raise _Reject(SkipReason.INVALID_CURRENCY)
        if _CURRENCY.fullmatch(value) is None:
            raise _Reject(SkipReason.INVALID_CURRENCY)
    if commission != sale:
        raise _Reject(SkipReason.CURRENCY_MISMATCH)
    assert isinstance(commission, str)
    if commission not in TWO_DECIMAL_CURRENCIES:
        raise _Reject(SkipReason.UNSUPPORTED_CURRENCY)
    return commission


def _click_ref(item: dict[str, Any]) -> str | None:
    refs = item.get("clickRefs")
    if refs is None:
        value = item.get("clickRef")
    elif isinstance(refs, dict):
        value = refs.get("clickRef")
    else:
        raise _Reject(SkipReason.INVALID_CLICK_REF)
    if value is None:
        return None
    if not isinstance(value, str):
        raise _Reject(SkipReason.INVALID_CLICK_REF)
    text = value.strip()
    if not text:
        return None
    if _CLICK_REF.fullmatch(text) is None:
        raise _Reject(SkipReason.INVALID_CLICK_REF)
    return text


def _build(item: dict[str, Any]) -> ReportRow:
    transaction_id = _positive_id(item.get("id"), SkipReason.INVALID_ID)
    advertiser_id = _positive_id(
        item.get("advertiserId"), SkipReason.INVALID_ADVERTISER
    )
    transaction_at = parse_utc_timestamp(item.get("transactionDate"))
    if transaction_at is None:
        raise _Reject(SkipReason.INVALID_DATE)
    status, status_raw = _status(item.get("commissionStatus"))
    commission_minor, commission_currency = _money(item, "commissionAmount")
    sale_minor, sale_currency = _money(item, "saleAmount")
    return ReportRow(
        external_id=str(transaction_id),
        advertiser_id=advertiser_id,
        transaction_at=transaction_at,
        status=status,
        status_raw=status_raw,
        commission_amount_minor=commission_minor,
        sale_amount_minor=sale_minor,
        currency=_currency(commission_currency, sale_currency),
        click_ref=_click_ref(item),
    )


def parse_transaction(item: object) -> ReportRow | Rejected:
    """The validated row, or the reason it was refused. Never raises for bad input."""
    if not isinstance(item, dict):
        return Rejected(SkipReason.INVALID_ITEM)
    try:
        return _build(item)
    except _Reject as rejection:
        return Rejected(rejection.reason)


def report_hash(row: ReportRow) -> str:
    """SHA-256 (hex) of the normalised row: what a change at the network means."""
    canonical = {
        "v": HASH_VERSION,
        "external_id": row.external_id,
        "advertiser_id": row.advertiser_id,
        "transaction_at": row.transaction_at.astimezone(UTC).isoformat(),
        "status": row.status.value,
        "status_raw": row.status_raw,
        "commission_amount_minor": row.commission_amount_minor,
        "sale_amount_minor": row.sale_amount_minor,
        "currency": row.currency,
        "click_ref": row.click_ref,
    }
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
