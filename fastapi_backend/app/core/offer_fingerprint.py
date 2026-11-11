"""The importer's edit fingerprint of an offer, and the rule that decides whether a draft is still untouched.

Shared by the Awin importer and the partner API, so "an untouched draft may be refreshed, anything else only gets
a marker" has one definition. Pure functions over duck-typed rows (an ORM record or a Core row both work), no I/O
and no ORM models, so ``app/partner`` may import it.

``fingerprint`` hashes every field an editor can change on an offer except ``commercial_basis`` (the importer
writes that once and never refreshes it; see ``FINGERPRINT_FIELDS``), its status and verification date, and its
affiliate links (provider, destination, tracking id, active), and nothing else (not ids, slugs, timestamps of the
row or click counters). The writer stores it as ``applied_content_hash`` each time it writes an offer; a later
mismatch means a person changed the offer, so it is no longer an untouched import. Its bytes are a contract
(stored hashes depend on them): ``tests/services/test_offer_fingerprint.py`` pins the digest of a known offer. Bump
``FINGERPRINT_VERSION`` together with ``FINGERPRINT_FIELDS``: stored fingerprints then stop matching, which fails
safe (nothing is refreshed).

It refuses a naive datetime (``TypeError``) instead of reading it as the server's local time; every date column is
``timestamptz``, so the database always returns aware values.
"""

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Final, Protocol

from app.core.offer_content import EDITABLE_FIELDS
from app.core.offer_vocabulary import STATUS_DRAFT

FINGERPRINT_VERSION: Final = 1
# ``commercial_basis`` is editable but deliberately NOT in the fingerprint: the importer writes it once, when it
# creates a draft, and never refreshes it, so an editor's change to it needs no "this is no longer an untouched
# import" signal. Adding it here would also change the digest of every stored offer and mark every existing import
# as edited. The admin review token covers it instead.
FINGERPRINT_FIELDS: Final = tuple(
    sorted((EDITABLE_FIELDS - {"commercial_basis"}) | {"status", "last_verified_at"})
)


class LinkFields(Protocol):
    """The four fields of an affiliate link the fingerprint reads (read-only, so frozen records fit)."""

    @property
    def provider(self) -> str: ...
    @property
    def destination_url(self) -> str: ...
    @property
    def tracking_id(self) -> str | None: ...
    @property
    def active(self) -> bool: ...


def canonical(value: Any) -> Any:
    """``json.dumps`` ``default=``: the one non-JSON value an offer holds is an aware datetime."""
    if isinstance(value, datetime):
        if (
            value.utcoffset() is None
        ):  # a naive time would be read as the server's local time
            raise TypeError("a naive datetime in a fingerprint")
        return value.astimezone(UTC).isoformat()
    raise TypeError(f"unexpected {type(value).__name__} in a fingerprint")


def fingerprint(offer: object, links: Sequence[LinkFields]) -> str:
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
        state, sort_keys=True, separators=(",", ":"), default=canonical
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def is_untouched_draft(
    *, status: str, link_count: int, current_fingerprint: str, applied_fingerprint: str
) -> bool:
    """True only for a draft nobody has changed since the writer left it, with exactly the one link it wrote.

    The status must be ``DRAFT`` (a published, expired or archived offer is never refreshed) and the stored
    fingerprint must still match the offer and its links as they are now, so an editor's change to any field, the
    link, or the verification date makes the offer "touched". Writers add their own conditions on top (the Awin
    importer also compares the source URL and the merchant).
    """
    return (
        status == STATUS_DRAFT
        and link_count == 1
        and current_fingerprint == applied_fingerprint
    )
