"""The admin's review token, and the importer's edit fingerprint (which lives in ``app.core.offer_fingerprint``).

``fingerprint``, ``FINGERPRINT_FIELDS`` and ``FINGERPRINT_VERSION`` moved to ``app/core/offer_fingerprint.py`` so the
partner API (which may import only ``app.core``) shares the one definition; they are re-exported here, so no
existing caller changes. See that module for what the fingerprint covers and why its bytes are a contract.

``review_token`` is what the admin API hands out so that ``verify`` and ``publish`` apply to the version the
editor read (see ``admin_offers``). It covers more than the fingerprint, because a visitor sees more than the
offer row: the offer content (the fingerprint of the offer alone), its links in click order with their ids,
and the merchant's and category's public fields (the category's name and short name, as the public offer page
shows them), and the commercial basis (a change to how an offer is paid for is a change the editor must see; it is
outside the fingerprint, see ``FINGERPRINT_FIELDS``). It is separate so the importer's hashes stay as they are.
Bump ``REVIEW_TOKEN_VERSION`` when what it covers changes: open tokens go stale and editors reload.

Both hashes refuse a naive datetime (``TypeError``): the admin API computes the token inside the write's
transaction, so such a failure would roll the write back, never follow a commit.
"""

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from app.core.offer_fingerprint import (
    FINGERPRINT_FIELDS,
    FINGERPRINT_VERSION,
    canonical,
    fingerprint,
)
from app.repositories.admin_offers import (
    AffiliateLinkRecord,
    OfferRecord,
    ReviewRefs,
)

__all__ = [
    "FINGERPRINT_FIELDS",
    "FINGERPRINT_VERSION",
    "REVIEW_TOKEN_VERSION",
    "fingerprint",
    "review_token",
]

REVIEW_TOKEN_VERSION = 3


def review_token(
    offer: OfferRecord, links: Sequence[AffiliateLinkRecord], refs: ReviewRefs
) -> str:
    """The opaque value an editor sends back to say "I reviewed this exact offer".

    Links are hashed in click order, ``(created_at, id)`` (the first usable one decides where a click
    goes), so swapping two destinations changes it. Not a secret, and it reveals nothing the admin cannot
    already read: it is a one-way hash of content the admin API returns beside it.
    """
    state: Mapping[str, Any] = {
        "v": REVIEW_TOKEN_VERSION,
        "offer_id": offer.id,
        "content": fingerprint(offer, ()),
        "commercial_basis": offer.commercial_basis.value,
        "links": [
            [
                link.id,
                link.provider,
                link.destination_url,
                link.tracking_id,
                link.active,
            ]
            for link in sorted(links, key=lambda item: (item.created_at, item.id))
        ],
        "merchant": [
            refs.merchant_name,
            refs.merchant_website,
            refs.merchant_logo_url,
            refs.merchant_active,
        ],
        "category": [
            refs.category_name,
            refs.category_short_name,
            refs.category_enabled,
        ],
    }
    payload = json.dumps(
        state, sort_keys=True, separators=(",", ":"), default=canonical
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
