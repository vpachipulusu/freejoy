"""Is this Awin tracking URL ours, for this advertiser, and going where the promotion says?

``validate_https_url`` and the ``awin`` host allowlist only prove a link is on an Awin host. A tampered or
odd item could still carry a link that pays another publisher or lands on another advertiser's site. So
the importer also pins the link itself, and anything it does not recognise is skipped (fail closed):

* no fragment, and the path is one of the two Awin click shapes this importer knows:
  ``/cread.php`` (``awinmid`` = advertiser id, ``awinaffid`` = publisher id, optional deep link ``ued``) or
  ``/awclick.php`` (``mid`` = advertiser id, ``id`` = publisher id, optional deep link ``p``);
* the query is parsed strictly (no ``;`` separators, no malformed pairs) and none of the id or deep-link
  parameters appears twice (a duplicate is how a second owner is smuggled past a first-value check);
* the publisher parameter equals the configured publisher id and the advertiser parameter equals the
  item's advertiser id;
* a deep link, when present, is a clean https URL, is not on any network's tracking host and, when the
  item has a ``url``, is on the same host (``www.`` may differ).

**Unverified.** These two shapes and parameter names come from how Awin links are commonly written, not from
the promotions API documentation, which says nothing about the URL. If real links differ, every item is
skipped (``tracking_shape_unrecognised``) and the first dry-run shows it.
"""

from dataclasses import dataclass
from urllib.parse import parse_qsl, urlsplit

from app.core.affiliate_hosts import PROVIDER_HOSTS, host_matches
from app.core.catalogue_urls import InvalidUrl, validate_https_url

NETWORK_HOSTS = frozenset().union(*PROVIDER_HOSTS.values())


@dataclass(frozen=True)
class _Shape:
    advertiser: str
    publisher: str
    deep_link: str


SHAPES = {
    "/cread.php": _Shape(advertiser="awinmid", publisher="awinaffid", deep_link="ued"),
    "/awclick.php": _Shape(advertiser="mid", publisher="id", deep_link="p"),
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


def _deep_link_problem(values: list[str], source_url: str | None) -> str | None:
    if len(values) != 1:
        return SHAPE
    try:
        link = validate_https_url(values[0])
    except InvalidUrl:
        return DESTINATION
    host = urlsplit(link).hostname or ""
    if host_matches(host, NETWORK_HOSTS):
        return DESTINATION
    if source_url is not None and _bare_host(link) != _bare_host(source_url):
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
    owners = (shape.advertiser, shape.publisher)
    if any(len(params.get(key, [])) != 1 for key in owners):
        return SHAPE  # missing, or given twice
    if params[shape.publisher][0] != str(publisher_id):
        return OWNER
    if params[shape.advertiser][0] != str(advertiser_id):
        return OWNER
    if shape.deep_link in params:
        return _deep_link_problem(params[shape.deep_link], source_url)
    return None
