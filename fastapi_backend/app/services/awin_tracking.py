"""Is this Awin tracking URL ours, for this advertiser, and going where the promotion says?

``validate_https_url`` and the ``awin`` host allowlist only prove a link is on an Awin host. A tampered or
odd item could still carry a link that pays another publisher or lands on another advertiser's site. So
the importer also pins the link itself, and anything it does not recognise is skipped (fail closed):

* no fragment, and the path is one of the two Awin click shapes this importer knows:
  ``/cread.php`` (``awinmid`` = advertiser id, ``awinaffid`` = publisher id) or ``/awclick.php``
  (``mid`` = advertiser id, ``id`` = publisher id);
* the query is parsed strictly (no ``;`` separators, no malformed pairs) and **every parameter name must be on
  an allowlist** (exact, case-sensitive, after decoding): the shape's two id names, the deep-link names
  ``ued`` and ``p`` (the same for both shapes), and the passive names ``clickref`` and ``clickref2`` to
  ``clickref6``. Anything else (``UED``, ``ued[]``, ``AwinAffId``, ``awinaffid[]``, a name with a space, a
  tracking parameter nobody listed) is ``tracking_shape_unrecognised``, so no look-alike of an id or deep-link
  parameter can sit beside a valid one. Passive values must be ``[A-Za-z0-9_.-]{0,64}``;
* each id parameter appears exactly once, the publisher one equals the configured publisher id and the
  advertiser one equals the item's advertiser id;
* a deep link is read from **whichever** deep-link name carries it (``ued`` or ``p``, on either shape): more
  than one in total is refused, and the one there is must be a clean https URL, not on any network's
  tracking host, and on the same host as the item's ``url`` (``www.`` may differ). An item **without** a ``url``
  cannot vouch for a deep link, so a deep link on such an item is refused; no ``url`` and no deep link is fine.

**Unverified.** These two shapes and parameter names come from how Awin links are commonly written, not from
the promotions API documentation, which says nothing about the URL. If real links differ, or carry a
parameter that is not listed, the item is skipped with ``tracking_shape_unrecognised`` and shows in the
dry-run report; the allowlist is extended in a reviewed change, never relaxed.
"""

import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlsplit

from app.core.affiliate_hosts import PROVIDER_HOSTS, host_matches
from app.core.catalogue_urls import InvalidUrl, validate_https_url

NETWORK_HOSTS = frozenset().union(*PROVIDER_HOSTS.values())
DEEP_LINK_NAMES = frozenset({"ued", "p"})
PASSIVE_NAMES = frozenset({"clickref", *(f"clickref{n}" for n in range(2, 7))})
_PASSIVE_VALUE = re.compile(r"[A-Za-z0-9_.-]{0,64}", re.ASCII)


@dataclass(frozen=True)
class _Shape:
    advertiser: str
    publisher: str


SHAPES = {
    "/cread.php": _Shape(advertiser="awinmid", publisher="awinaffid"),
    "/awclick.php": _Shape(advertiser="mid", publisher="id"),
}

SHAPE = "tracking_shape_unrecognised"
OWNER = "tracking_owner_mismatch"
DESTINATION = "tracking_destination_mismatch"


def _bare_host(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host.removeprefix("www.")


def _pairs(query: str) -> dict[str, list[str]] | None:
    """The query as a mapping of key to every value, or ``None`` when it is not strictly well formed."""
    if ";" in query:
        return None
    try:
        pairs = parse_qsl(query, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        return None
    found: dict[str, list[str]] = {}
    for key, value in pairs:
        found.setdefault(key, []).append(value)
    return found


def _names_and_passives_ok(params: dict[str, list[str]], shape: _Shape) -> bool:
    allowed = {shape.advertiser, shape.publisher} | DEEP_LINK_NAMES | PASSIVE_NAMES
    if not params.keys() <= allowed:
        return False
    return all(
        _PASSIVE_VALUE.fullmatch(value)
        for name in PASSIVE_NAMES & params.keys()
        for value in params[name]
    )


def _deep_link_problem(link: str, source_url: str | None) -> str | None:
    if source_url is None:  # nothing to compare it with, so it cannot be vouched for
        return DESTINATION
    try:
        checked = validate_https_url(link)
    except InvalidUrl:
        return DESTINATION
    host = urlsplit(checked).hostname or ""
    if host_matches(host, NETWORK_HOSTS) or _bare_host(checked) != _bare_host(
        source_url
    ):
        return DESTINATION
    return None


def tracking_problem(
    url: str, *, publisher_id: int, advertiser_id: int, source_url: str | None
) -> str | None:
    """``None`` when the (already validated, Awin-host) URL is acceptable, else a skip reason code."""
    parts = urlsplit(url)
    shape = SHAPES.get(parts.path)
    params = _pairs(parts.query)
    if parts.fragment or shape is None or params is None:
        return SHAPE
    if not _names_and_passives_ok(params, shape):
        return SHAPE
    owners = (shape.advertiser, shape.publisher)
    if any(len(params.get(key, [])) != 1 for key in owners):
        return SHAPE  # missing, or given twice
    if params[shape.publisher][0] != str(publisher_id):
        return OWNER
    if params[shape.advertiser][0] != str(advertiser_id):
        return OWNER
    deep_links = [v for name in DEEP_LINK_NAMES for v in params.get(name, [])]
    if len(deep_links) > 1:
        return SHAPE
    return _deep_link_problem(deep_links[0], source_url) if deep_links else None
