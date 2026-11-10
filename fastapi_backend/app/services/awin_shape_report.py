"""Describe the shape of what Awin returned: names only, never values (FREEJ-52).

The importer's item-level assumptions (``docs/developer/backend-awin-import.md``) can only be checked against
real items. ``build_shape_report`` takes the pages a dry run fetched and says which keys, hosts and query
parameter NAMES it saw, so the owner can compare them with the assumptions without anything private reaching
the terminal.

The report holds data from an external service and is printed, so:

* it keeps only names: item keys (top level, and one level under ``advertiser``, ``voucher`` and
  ``regions``), the host of ``urlTracking`` and of ``url``, the path and query parameter names of
  ``urlTracking``, and counts. A query value, a voucher code, a title, a description, terms, a full URL, a
  user name or a port is never read into it;
* every name is cut to ``MAX_NAME_LENGTH`` characters and reduced to printable ASCII (anything else becomes
  ``?``), so a hostile key cannot carry a control character, an escape sequence or a line break;
* every set is sorted and holds at most ``MAX_ENTRIES`` names, with the number left out beside it;
* an item that is not an object, or a URL that does not parse, is counted and nothing of it is kept.

Pure functions over the parsed pages: no I/O, no logging.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote_plus, urlsplit

from app.core.awin_client import PromotionsPage

MAX_ENTRIES = 40
MAX_NAME_LENGTH = 60
ELLIPSIS = "..."
REPLACEMENT = "?"


@dataclass(frozen=True)
class NameList:
    """Up to ``MAX_ENTRIES`` sorted names and how many more there were."""

    names: tuple[str, ...] = ()
    more: int = 0


@dataclass(frozen=True)
class ShapeReport:
    pages: int
    pages_bare_list: int
    pages_object: int
    total_pages_present: (
        int  # object-envelope pages whose pagination had a totalPages key
    )
    items: int
    non_object_items: int
    item_keys: NameList
    advertiser_keys: NameList
    voucher_keys: NameList
    regions_keys: NameList
    tracking_hosts: NameList
    tracking_paths: NameList
    tracking_params: NameList
    tracking_unreadable: (
        int  # urlTracking missing, not a string, or not a URL with a host
    )
    url_hosts: NameList
    url_unreadable: int
    joined_true: int
    joined_false: int
    joined_other: int  # advertiser.joined missing or not a boolean


def sanitise_name(value: object) -> str:
    """Printable ASCII only, at most ``MAX_NAME_LENGTH`` characters (cut first, so a huge name is cheap)."""
    text = value if isinstance(value, str) else str(value)
    cut = len(text) > MAX_NAME_LENGTH
    head = text[: MAX_NAME_LENGTH - len(ELLIPSIS)] if cut else text
    cleaned = "".join(char if " " <= char <= "~" else REPLACEMENT for char in head)
    return cleaned + ELLIPSIS if cut else cleaned


def _name_list(names: Iterable[str]) -> NameList:
    ordered = sorted(set(names))
    return NameList(tuple(ordered[:MAX_ENTRIES]), max(0, len(ordered) - MAX_ENTRIES))


def _keys(value: object) -> set[str]:
    return {sanitise_name(key) for key in value} if isinstance(value, dict) else set()


@dataclass(frozen=True)
class _UrlParts:
    host: str
    path: str
    params: frozenset[str]


def _read_url(value: object) -> _UrlParts | None:
    """The host, path and query parameter names of a URL string, or ``None`` when it has no readable host."""
    if not isinstance(value, str):
        return None
    try:
        parts = urlsplit(value.strip())
        host = parts.hostname
    except ValueError:
        return None
    if not host:
        return None
    params = set[str]()
    for pair in parts.query.split("&"):
        name, equals, _value = pair.partition("=")  # the value is dropped here, unread
        if equals and name:
            params.add(sanitise_name(unquote_plus(name)))
    return _UrlParts(sanitise_name(host), sanitise_name(parts.path), frozenset(params))


class _Seen:
    """What one pass over the items collected; private, turned into the frozen report at the end."""

    def __init__(self) -> None:
        self.item_keys, self.advertiser_keys = set[str](), set[str]()
        self.voucher_keys, self.regions_keys = set[str](), set[str]()
        self.tracking_hosts, self.tracking_paths = set[str](), set[str]()
        self.tracking_params, self.url_hosts = set[str](), set[str]()
        self.non_object = self.tracking_unreadable = self.url_unreadable = 0
        self.joined = {True: 0, False: 0, None: 0}

    def add(self, item: Any) -> None:
        if not isinstance(item, dict):
            self.non_object += 1
            return
        self.item_keys |= _keys(item)
        self.advertiser_keys |= _keys(item.get("advertiser"))
        self.voucher_keys |= _keys(item.get("voucher"))
        self.regions_keys |= _keys(item.get("regions"))
        self._add_urls(item)
        advertiser = item.get("advertiser")
        joined = advertiser.get("joined") if isinstance(advertiser, dict) else None
        self.joined[joined if isinstance(joined, bool) else None] += 1

    def _add_urls(self, item: dict[str, Any]) -> None:
        tracking = _read_url(item.get("urlTracking"))
        if tracking is None:
            self.tracking_unreadable += 1
        else:
            self.tracking_hosts.add(tracking.host)
            if tracking.path:
                self.tracking_paths.add(tracking.path)
            self.tracking_params |= tracking.params
        url = _read_url(item.get("url"))
        if url is None:
            self.url_unreadable += 1
        else:
            self.url_hosts.add(url.host)


def build_shape_report(pages: Sequence[PromotionsPage]) -> ShapeReport:
    seen = _Seen()
    objects = [page for page in pages if page.envelope == "object"]
    for page in pages:
        for item in page.items:
            seen.add(item)
    return ShapeReport(
        pages=len(pages),
        pages_bare_list=len(pages) - len(objects),
        pages_object=len(objects),
        total_pages_present=sum(1 for page in objects if page.total_pages_present),
        items=sum(len(page.items) for page in pages),
        non_object_items=seen.non_object,
        item_keys=_name_list(seen.item_keys),
        advertiser_keys=_name_list(seen.advertiser_keys),
        voucher_keys=_name_list(seen.voucher_keys),
        regions_keys=_name_list(seen.regions_keys),
        tracking_hosts=_name_list(seen.tracking_hosts),
        tracking_paths=_name_list(seen.tracking_paths),
        tracking_params=_name_list(seen.tracking_params),
        tracking_unreadable=seen.tracking_unreadable,
        url_hosts=_name_list(seen.url_hosts),
        url_unreadable=seen.url_unreadable,
        joined_true=seen.joined[True],
        joined_false=seen.joined[False],
        joined_other=seen.joined[None],
    )
