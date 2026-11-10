"""Content rules for offers that the schema cannot express on its own."""

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from app.models.platform.offer import PostageKind
from app.services.admin_catalogue_errors import InvalidInput

CURRENCY_LENGTH = 3
URL_FIELDS = ("source_url",)
EDITABLE_FIELDS = frozenset(
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
AUDITED_FIELDS = frozenset(
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


def check_postage(values: Mapping[str, Any]) -> None:
    """The amount and a 3-letter upper-case currency exist exactly when postage is COST."""
    kind = values["postage_kind"]
    amount = values.get("postage_amount_minor")
    currency = values.get("postage_currency")
    if kind == PostageKind.COST:
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
    if not valid:
        raise InvalidInput("invalid_postage")


def check_window(start_at: datetime | None, end_at: datetime | None) -> None:
    if start_at is not None and end_at is not None and end_at <= start_at:
        raise InvalidInput("invalid_window")
