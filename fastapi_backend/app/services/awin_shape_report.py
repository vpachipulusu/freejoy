"""Describe the shape of what Awin returned, without its values (FREEJ-52).

The importer's item-level assumptions (``docs/developer/backend-awin-import.md``) can only be checked against
real items. ``ShapeCollector`` takes the pages a dry run fetched, one at a time, and keeps counts and short
labels so the owner can compare them with the assumptions. Pages are never kept: ``add_page`` reads one and
lets it go.

The data is external and the report is printed, so it is built to leak as little as possible. What it can
print, exactly:

* **Counts** (items, pages, URLs by property) and **importer reason codes** (a closed set of constants).
* **Names**: an object key or a URL query parameter name is printed only when it is letters and underscores
  with at most two trailing digits (``NAME_SHAPE``); every other name is only counted (``other``). A key
  that is an id, a code or a sentence is therefore never printed, but a short alphabetic token in a name
  position is (the residual risk of a names-only report).
* **Paths**: the path of ``urlTracking`` is printed as is only when it is one of the click paths the importer
  knows (``awin_tracking.SHAPES``); any other path is printed as an outline: each run of letters is ``a``,
  each run of digits ``9``, ``/ . - _`` stay, anything else is ``?``.
* **Hosts** of ``urlTracking`` and ``url``, as they are (they are what ``PROVIDER_HOSTS`` is compared with),
  but only when the host passes the importer's own hostname grammar; the publisher id is replaced by ``#``
  and any run of five or more digits is masked as ``#`` before the length cut. A host can still name a
  merchant or a network: that is the point of printing it.
* **Schemes** of ``urlTracking``, through the name rule above.
* **Date outlines** (FREEJ-66) of ``startDate`` and ``endDate``, each with a count, in a vocabulary of their
  own that only a date needs: a digit is ``9``; a run of ASCII letters is itself only when it is a single
  ``T``, ``Z``, ``t`` or ``z`` and otherwise ``a``; ``- : . +`` stay; exactly one space is ``_``; every
  other character (a tab, a no-break space, ``/ _ [ ] % < >``, a control character, any non-ASCII
  character) is ``?``; the result is cut to ``MAX_NAME_LENGTH``. ``2026-11-01T00:00:00.000`` prints as
  ``9999-99-99T99:99:99.999``, a space separator as ``9999-99-99_99:99:99`` and an offset as ``+99:99``. A
  value that is not a string prints as ``<null>``, ``<int>``, ``<float>``, ``<bool>``, ``<object>``,
  ``<array>`` (or ``<other>`` for a type JSON cannot produce), an absent key as ``<missing>``, an empty
  string as ``<empty>``; ``<`` and ``>`` never come out of a value, so none of these can be forged. Words,
  codes and URLs collapse to ``a`` and ``?``, so nothing a field says survives except its date punctuation.

Never printed: a query value, a fragment, user info, a port, a voucher code, a title, a description, terms, a
full URL, the publisher id or the token. Every label is built from the characters ``A-Z a-z 0-9 . _ - / [ ]
% :`` (plus ``#`` from the mask; the date outlines above have their own, smaller set), cut to ``MAX_NAME_LENGTH``, so a response cannot put a space, comma,
bracket pair, backtick, ``@``, angle bracket, control character or line break into the output. Each printed
set is sorted and holds at most ``MAX_ENTRIES`` labels; each tracked set stops growing at ``NAME_CAP``.

Nothing here does I/O or logging.
"""

import re
import string
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote_plus, urlsplit

from app.core.awin_client import PromotionsPage
from app.core.catalogue_urls import (
    MAX_URL_LENGTH,
    InvalidUrl,
    is_valid_host,
    validate_https_url,
)
from app.services.awin_promotions import tracking_outcome
from app.services.awin_tracking import SHAPES, query_is_strict

MAX_ENTRIES = 40
MAX_NAME_LENGTH = 60
NAME_CAP = 1000
MAX_HOST_LENGTH = 253
ELLIPSIS = "..."
REPLACEMENT = "?"
MASK = "#"
NAME_SHAPE = re.compile(r"[A-Za-z][A-Za-z_]{0,31}[0-9]{0,2}")
_LONG_DIGITS = re.compile(r"[0-9]{5,}")
_OUTLINE = re.compile(r"([A-Za-z]+)|([0-9]+)|(.)", re.DOTALL)
_OUTLINE_KEPT = frozenset("/.-_")
_ALLOWED = frozenset(string.ascii_letters + string.digits + "._-/[]%:")
_DATE_TOKEN = re.compile(
    r"([A-Za-z]+)|([0-9])|(.)", re.DOTALL
)  # each alternative is one step: linear
_DATE_LETTERS = frozenset("TZtz")
_DATE_KEPT = frozenset("-:.+")
DATE_KEYS = ("startDate", "endDate")
MISSING, EMPTY = "<missing>", "<empty>"
_JSON_KINDS: tuple[tuple[type, str], ...] = (
    (bool, "<bool>"),  # before int: a bool is an int in Python
    (int, "<int>"),
    (float, "<float>"),
    (dict, "<object>"),
    (list, "<array>"),
)
OTHER_KIND = "<other>"


@dataclass(frozen=True)
class NameList:
    """Up to ``MAX_ENTRIES`` sorted labels, how many more there were, and names that were only counted."""

    names: tuple[str, ...] = ()
    more: int = 0
    other: int = 0  # distinct names that failed the name rule: counted, never printed
    capped: bool = False  # the tracked set hit ``NAME_CAP``: ``more`` and ``other`` are lower bounds


@dataclass(frozen=True)
class OutlineCounts:
    """Up to ``MAX_ENTRIES`` (outline, count) pairs sorted by outline, and how many outlines were left out."""

    entries: tuple[tuple[str, int], ...] = ()
    more: int = 0
    capped: bool = False  # the tracked set hit ``NAME_CAP``: ``more`` is a lower bound and counts may be short


@dataclass(frozen=True)
class ShapeReport:
    pages: int
    pages_bare_list: int
    pages_object: int
    total_pages_present: (
        int  # object-envelope pages whose pagination had a totalPages key
    )
    total_pages_usable: int  # ... and whose value was a non-negative integer
    items: int  # every item in the fetched pages, which can be more than the run read (--limit)
    non_object_items: int
    item_keys: NameList
    advertiser_keys: NameList
    voucher_keys: NameList
    regions_keys: NameList
    tracking_hosts: NameList
    tracking_paths: NameList
    tracking_params: NameList
    tracking_schemes: NameList
    tracking_unreadable: (
        int  # urlTracking missing, not a string, too long, or without a valid host
    )
    tracking_https_valid: int  # accepted by the importer's ``validate_https_url``
    tracking_with_fragment: int
    tracking_query_malformed: int  # not strictly parseable (the importer refuses it)
    tracking_outcomes: tuple[
        tuple[str, int], ...
    ]  # importer reason code (or accepted) and count
    url_hosts: NameList
    url_unreadable: int
    start_date_outlines: OutlineCounts
    end_date_outlines: OutlineCounts
    joined_true: int
    joined_false: int
    joined_other: int  # advertiser.joined missing or not a boolean


def _cut(text: str) -> str:
    if len(text) <= MAX_NAME_LENGTH:
        return text
    return text[: MAX_NAME_LENGTH - len(ELLIPSIS)] + ELLIPSIS


def sanitise_name(value: object) -> str:
    """Only ``A-Z a-z 0-9 . _ - / [ ] % :`` (anything else, ``#`` too, becomes ``?``), cut to the maximum length.

    One character past the limit is looked at, so a name that is too long is cut and a huge one stays cheap.
    """
    text = value if isinstance(value, str) else str(value)
    head = text[: MAX_NAME_LENGTH + 1]
    return _cut("".join(char if char in _ALLOWED else REPLACEMENT for char in head))


def _host_label(host: str, publisher_id: int) -> str:
    """The host (already grammar-checked) with our publisher id and any long digit run masked, then cut."""
    masked = _LONG_DIGITS.sub(MASK, host.replace(str(publisher_id), MASK))
    return _cut(masked)


def _outline(path: str) -> str:
    def collapse(match: re.Match[str]) -> str:
        if match[1]:
            return "a"
        if match[2]:
            return "9"
        return match[3] if match[3] in _OUTLINE_KEPT else REPLACEMENT

    return _cut(_OUTLINE.sub(collapse, path))


def _date_token(match: re.Match[str]) -> str:
    if match[1]:
        return match[1] if match[1] in _DATE_LETTERS else "a"
    if match[2]:
        return "9"
    char = match[3]
    if char in _DATE_KEPT:
        return char
    return "_" if char == " " else REPLACEMENT


def date_outline(value: object) -> str:
    """What a date field looks like, never what it says: the vocabulary is in the module docstring.

    The string is cut to one character past the limit before anything is read, so a huge one stays cheap.
    """
    if value == "":
        return EMPTY
    if value is None:
        return "<null>"
    if isinstance(value, str):
        return _cut(_DATE_TOKEN.sub(_date_token, value[: MAX_NAME_LENGTH + 1]))
    return next(
        (label for kind, label in _JSON_KINDS if isinstance(value, kind)), OTHER_KIND
    )


def _path_label(path: str) -> str:
    return path if path in SHAPES else _outline(path)


class _Names:
    """A bounded set of printable labels, and a bounded count of names that were not printable."""

    def __init__(self) -> None:
        self._names = set[str]()
        self._other = set[int]()
        self._capped = False

    def add_name(self, raw: object) -> None:
        """A key or parameter name: kept when it fits ``NAME_SHAPE``, else only counted."""
        if isinstance(raw, str) and NAME_SHAPE.fullmatch(raw):
            self._put(self._names, raw)
        else:
            self._put(self._other, hash(raw))

    def add_label(self, label: str) -> None:
        """A label that was already made safe (a host, a path or an outline)."""
        self._put(self._names, label)

    def add_all(self, raws: Iterable[object]) -> None:
        for raw in raws:
            self.add_name(raw)

    def _put(self, bucket: set[Any], value: Any) -> None:
        if value in bucket:
            return
        if len(bucket) >= NAME_CAP:
            self._capped = True
            return
        bucket.add(value)

    def result(self) -> NameList:
        ordered = sorted(self._names)
        return NameList(
            tuple(ordered[:MAX_ENTRIES]),
            max(0, len(ordered) - MAX_ENTRIES),
            len(self._other),
            self._capped,
        )


class _Outlines:
    """A bounded count of outlines: at most ``NAME_CAP`` distinct ones are tracked."""

    def __init__(self) -> None:
        self._counts: Counter[str] = Counter()
        self._capped = False

    def add(self, outline: str) -> None:
        if outline not in self._counts and len(self._counts) >= NAME_CAP:
            self._capped = True
            return
        self._counts[outline] += 1

    def result(self) -> OutlineCounts:
        ordered = sorted(self._counts.items())
        return OutlineCounts(
            tuple(ordered[:MAX_ENTRIES]),
            max(0, len(ordered) - MAX_ENTRIES),
            self._capped,
        )


@dataclass(frozen=True)
class _Link:
    """What is read from a URL string: readable parts, never a value."""

    host: str | None  # only a host that passes the importer's hostname grammar
    path: str
    param_names: tuple[str, ...]
    scheme: str
    has_fragment: bool
    query_strict: bool
    https_valid: bool


def _read_link(value: object) -> _Link | None:
    """The parts of a URL string, or ``None`` when it is not a string, is too long, or does not split."""
    if not isinstance(value, str) or len(value) > MAX_URL_LENGTH:
        return None
    text = value.strip()
    try:
        parts = urlsplit(text)
        host = (parts.hostname or "").lower()
    except ValueError:
        return None
    names = []
    for pair in parts.query.split("&"):
        name, equals, _value = pair.partition("=")  # the value is dropped here, unread
        if equals and name:
            names.append(unquote_plus(name))
    try:
        validate_https_url(text)
        https_valid = True
    except InvalidUrl:
        https_valid = False
    usable = bool(host) and len(host) <= MAX_HOST_LENGTH and is_valid_host(host)
    return _Link(
        host if usable else None,
        parts.path,
        tuple(names),
        parts.scheme.lower(),
        bool(parts.fragment),
        query_is_strict(parts.query),
        https_valid,
    )


class ShapeCollector:
    """Reads pages one by one and keeps only counts and bounded labels; ``report()`` freezes them."""

    def __init__(self, publisher_id: int) -> None:
        self._publisher_id = publisher_id
        self._item_keys, self._advertiser_keys = _Names(), _Names()
        self._voucher_keys, self._regions_keys = _Names(), _Names()
        self._tracking_hosts, self._tracking_paths = _Names(), _Names()
        self._tracking_params, self._tracking_schemes = _Names(), _Names()
        self._url_hosts = _Names()
        self._date_outlines = {key: _Outlines() for key in DATE_KEYS}
        self._outcomes: Counter[str] = Counter()
        self._joined: dict[bool | None, int] = {True: 0, False: 0, None: 0}
        self._counts: Counter[str] = Counter()

    @property
    def pages(self) -> int:
        return self._counts["pages"]

    def add_page(self, page: PromotionsPage) -> None:
        counts = self._counts
        counts["pages"] += 1
        if page.envelope == "object":
            counts["pages_object"] += 1
            counts["total_present"] += int(page.total_pages_present)
            counts["total_usable"] += int(page.total_pages is not None)
        counts["items"] += len(page.items)
        for item in page.items:
            self._add_item(item)

    def _add_item(self, item: Any) -> None:
        if not isinstance(item, dict):
            self._counts["non_object"] += 1
            return
        advertiser = item.get("advertiser")
        self._item_keys.add_all(item)
        for names, value in (
            (self._advertiser_keys, advertiser),
            (self._voucher_keys, item.get("voucher")),
            (self._regions_keys, item.get("regions")),
        ):
            if isinstance(value, dict):
                names.add_all(value)
        joined = advertiser.get("joined") if isinstance(advertiser, dict) else None
        self._joined[joined if isinstance(joined, bool) else None] += 1
        self._add_tracking(item)
        self._add_url(item.get("url"))
        for key, outlines in self._date_outlines.items():
            outlines.add(date_outline(item[key]) if key in item else MISSING)

    def _host(self, link: _Link) -> str | None:
        return None if link.host is None else _host_label(link.host, self._publisher_id)

    def _add_tracking(self, item: dict[str, Any]) -> None:
        self._outcomes[tracking_outcome(item, self._publisher_id)] += 1
        link = _read_link(item.get("urlTracking"))
        if link is None:
            self._counts["tracking_unreadable"] += 1
            return
        self._counts["tracking_https_valid"] += int(link.https_valid)
        self._counts["tracking_fragment"] += int(link.has_fragment)
        self._counts["tracking_query_malformed"] += int(not link.query_strict)
        self._tracking_schemes.add_name(link.scheme)
        host = self._host(link)
        if host is None:
            self._counts["tracking_unreadable"] += 1
            return
        self._tracking_hosts.add_label(host)
        if link.path:
            self._tracking_paths.add_label(_path_label(link.path))
        self._tracking_params.add_all(link.param_names)

    def _add_url(self, value: object) -> None:
        link = _read_link(value)
        host = None if link is None else self._host(link)
        if host is None:
            self._counts["url_unreadable"] += 1
        else:
            self._url_hosts.add_label(host)

    def report(self) -> ShapeReport:
        counts = self._counts
        pages_object = counts["pages_object"]
        return ShapeReport(
            pages=counts["pages"],
            pages_bare_list=counts["pages"] - pages_object,
            pages_object=pages_object,
            total_pages_present=counts["total_present"],
            total_pages_usable=counts["total_usable"],
            items=counts["items"],
            non_object_items=counts["non_object"],
            item_keys=self._item_keys.result(),
            advertiser_keys=self._advertiser_keys.result(),
            voucher_keys=self._voucher_keys.result(),
            regions_keys=self._regions_keys.result(),
            tracking_hosts=self._tracking_hosts.result(),
            tracking_paths=self._tracking_paths.result(),
            tracking_params=self._tracking_params.result(),
            tracking_schemes=self._tracking_schemes.result(),
            tracking_unreadable=counts["tracking_unreadable"],
            tracking_https_valid=counts["tracking_https_valid"],
            tracking_with_fragment=counts["tracking_fragment"],
            tracking_query_malformed=counts["tracking_query_malformed"],
            tracking_outcomes=tuple(sorted(self._outcomes.items())),
            url_hosts=self._url_hosts.result(),
            url_unreadable=counts["url_unreadable"],
            start_date_outlines=self._date_outlines["startDate"].result(),
            end_date_outlines=self._date_outlines["endDate"].result(),
            joined_true=self._joined[True],
            joined_false=self._joined[False],
            joined_other=self._joined[None],
        )


def build_shape_report(
    pages: Sequence[PromotionsPage], publisher_id: int
) -> ShapeReport:
    """The report for pages already in hand (tests); a run feeds a ``ShapeCollector`` page by page."""
    collector = ShapeCollector(publisher_id)
    for page in pages:
        collector.add_page(page)
    return collector.report()
