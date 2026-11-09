"""Two hashes of an offer: the importer's edit fingerprint and the admin's review token.

``fingerprint`` hashes every field an editor can change on an offer, its status and verification date,
and its affiliate links (provider, destination, tracking id, active), and nothing else (not ids, slugs,
timestamps of the row or click counters). The Awin importer stores it as ``applied_content_hash`` each
time it writes an offer; a later mismatch means a person changed the offer, so it is no longer an untouched
import. Its bytes are a contract (stored hashes depend on them): ``tests/services/test_offer_fingerprint.py``
pins the digest of a known offer. Bump ``FINGERPRINT_VERSION`` together with ``FINGERPRINT_FIELDS``: stored
fingerprints then stop matching, which fails safe (nothing is refreshed).

Both hashes refuse a naive datetime (``TypeError``) instead of reading it as the server's local time. That
refusal lives in ``_canonical``, which ``fingerprint()`` shares, so for that one input ``fingerprint()`` is no
longer what it was before; it cannot happen with stored data (every date column is ``timestamptz``, so the
database always returns aware values) and it leaves every aware input's digest unchanged. The admin API computes
the token inside the write's transaction, so such a failure would roll the write back, never follow a commit.

``review_token`` is what the admin API hands out so that ``verify`` and ``publish`` apply to the version the
editor read (see ``admin_offers``). It covers more than the fingerprint, because a visitor sees more than the
offer row: the offer content (the fingerprint of the offer alone), its links in click order with their ids,
and the merchant's and category's public fields (the category's name and short name, as the public offer page
shows them). It is separate so the importer's hashes stay as they are.
Bump ``REVIEW_TOKEN_VERSION`` when what it covers changes: open tokens go stale and editors reload.
"""

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from app.repositories.admin_offers import (
    AffiliateLinkRecord,
    OfferRecord,
    ReviewRefs,
)
from app.services.offer_rules import EDITABLE_FIELDS

FINGERPRINT_VERSION = 1
REVIEW_TOKEN_VERSION = 2
FINGERPRINT_FIELDS = tuple(sorted(EDITABLE_FIELDS | {"status", "last_verified_at"}))


def _canonical(value: Any) -> Any:
    if isinstance(value, datetime):
        if (
            value.utcoffset() is None
        ):  # a naive time would be read as the server's local time
            raise TypeError("a naive datetime in a fingerprint")
        return value.astimezone(UTC).isoformat()
    raise TypeError(f"unexpected {type(value).__name__} in a fingerprint")


def fingerprint(offer: OfferRecord, links: Sequence[AffiliateLinkRecord]) -> str:
    """SHA-256 (hex) of the offer's editable state and its links, as an editor would see them."""
    state: Mapping[str, Any] = {
        "v": FINGERPRINT_VERSION,
        "offer": {name: getattr(offer, name) for name in FINGERPRINT_FIELDS},
        "links": sorted(
            (
                [link.provider, link.destination_url, link.tracking_id, link.active]
                for link in links
            ),
            key=repr,
        ),
    }
    payload = json.dumps(
        state, sort_keys=True, separators=(",", ":"), default=_canonical
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


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
        state, sort_keys=True, separators=(",", ":"), default=_canonical
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
