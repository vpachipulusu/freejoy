"""The words an offer is described with, as plain strings, for code that may not import the ORM models.

``app/partner`` reads and writes offers through SQLAlchemy Core and may import only ``app.core``, so it cannot use
the enums in ``app/models/platform/offer.py``. These tuples are the same members by name;
``tests/core/test_offer_vocabulary.py`` fails if either side drifts.
"""

from typing import Final

OFFER_TYPES: Final = ("FREE_SAMPLE", "FREE_TRIAL", "CASHBACK", "VOUCHER", "COMPETITION")
OFFER_STATUSES: Final = ("DRAFT", "PUBLISHED", "EXPIRED", "ARCHIVED")
POSTAGE_KINDS: Final = ("FREE", "COST", "CHECK_TERMS")
OFFER_FRESHNESS: Final = (
    "NEW_TODAY",
    "JUST_ADDED",
    "POPULAR",
    "ENDS_SOON",
    "LIMITED",
)

STATUS_DRAFT: Final = "DRAFT"
STATUS_PUBLISHED: Final = "PUBLISHED"
STATUS_EXPIRED: Final = "EXPIRED"
STATUS_ARCHIVED: Final = "ARCHIVED"
POSTAGE_COST: Final = "COST"

# What a retailer may submit through the partner API: a sample or a trial of its own product, nothing else.
PARTNER_OFFER_TYPES: Final = ("FREE_SAMPLE", "FREE_TRIAL")
