"""Content rules for offers that the schema cannot express on its own, shared by the admin side and the partner API.

Pure functions and constants, no I/O and no ORM models, so ``app/partner`` (which may import only ``app.core``)
and ``app/services`` apply one definition. ``app/services/offer_rules.py`` re-exports them and raises its own
``InvalidInput`` from the codes returned here.
"""

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Final

from app.core.offer_vocabulary import POSTAGE_COST

CURRENCY_LENGTH: Final = 3
URL_FIELDS: Final = ("source_url",)
EDITABLE_FIELDS: Final = frozenset(
    {
        "title",
        "summary",
        "description",
        "category_id",
        "merchant_id",
        "offer_type",
        "commercial_basis",
        "featured",
        "image_tint",
        "image_label",
        "image_alt",
        "source_url",
        "postage_kind",
        "postage_amount_minor",
        "postage_currency",
        "requires_card",
        "subscription_terms",
        "eligibility",
        "availability",
        "steps",
        "start_at",
        "end_at",
        "freshness",
        "publisher_notes",
    }
)
# Fields whose before and after values go into the audit row (never free text).
AUDITED_FIELDS: Final = frozenset(
    {
        "title",
        "category_id",
        "merchant_id",
        "offer_type",
        "commercial_basis",
        "featured",
        "postage_kind",
        "postage_amount_minor",
        "postage_currency",
        "requires_card",
        "start_at",
        "end_at",
        "freshness",
    }
)

# The illustrated placeholder every imported or submitted offer starts with (no image is ever uploaded for them).
IMAGE_TINT: Final = "cream"
IMAGE_ALT: Final = "Illustration of an offer"
IMAGE_LABEL_LENGTH: Final = 40

INVALID_POSTAGE: Final = "invalid_postage"
INVALID_WINDOW: Final = "invalid_window"


def payload_hash(payload: Mapping[str, Any]) -> str:
    """SHA-256 (hex) of a sanitised submission payload, over its canonical JSON.

    Shared by the partner API (which stores it with the proposal) and the admin apply path (which recomputes it
    under the lock, so a payload that was changed under its hash is refused). The bytes are a contract: stored hashes
    depend on them (``tests/api/partner/test_partner_content.py`` pins one).
    """
    text = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def postage_problem(values: Mapping[str, Any]) -> str | None:
    """``invalid_postage`` unless the amount and a 3-letter upper-case currency exist exactly when postage is COST."""
    kind = values["postage_kind"]
    amount = values.get("postage_amount_minor")
    currency = values.get("postage_currency")
    if kind == POSTAGE_COST:
        valid = (
            amount is not None
            and amount >= 0
            and isinstance(currency, str)
            and len(currency) == CURRENCY_LENGTH
            and currency.isascii()
            and currency.isalpha()
            and currency.isupper()
        )
    else:
        valid = amount is None and currency is None
    return None if valid else INVALID_POSTAGE


def window_problem(start_at: datetime | None, end_at: datetime | None) -> str | None:
    """``invalid_window`` when both dates are given and the end is not after the start."""
    if start_at is not None and end_at is not None and end_at <= start_at:
        return INVALID_WINDOW
    return None
