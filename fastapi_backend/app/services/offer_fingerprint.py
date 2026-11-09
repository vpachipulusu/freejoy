"""The fingerprint of an offer: what a visitor reads and where a click goes, as one SHA-256 value.

``fingerprint`` hashes every field an editor can change on an offer, its status and verification date,
and its affiliate links (provider, destination, tracking id, active), and nothing else (not ids, slugs,
timestamps of the row or click counters). Two users of it:

* The Awin importer stores it as ``applied_content_hash`` each time it writes an offer. A later mismatch
  means a person changed the offer, so it is no longer an untouched import.
* The admin API hands it out as the offer's ``review_token`` (``review_token``) and ``verify`` and
  ``publish`` refuse a token that no longer matches, so an editor never approves content they did not see
  (see ``admin_offers``).

Bump ``FINGERPRINT_VERSION`` together with ``FINGERPRINT_FIELDS``: stored fingerprints then stop matching,
which fails safe (nothing is refreshed) and an open review token goes stale (the editor reloads).
"""

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from app.repositories.admin_offers import AffiliateLinkRecord, OfferRecord
from app.services.offer_rules import EDITABLE_FIELDS

FINGERPRINT_VERSION = 1
FINGERPRINT_FIELDS = tuple(sorted(EDITABLE_FIELDS | {"status", "last_verified_at"}))


def _canonical(value: Any) -> Any:
    if isinstance(value, datetime):
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


def review_token(offer: OfferRecord, links: Sequence[AffiliateLinkRecord]) -> str:
    """The opaque value an editor sends back to say "I reviewed this exact offer".

    Not a secret and it reveals nothing the admin cannot already read: it is a one-way hash of
    content the admin API returns beside it.
    """
    return fingerprint(offer, links)
