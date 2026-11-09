"""Turn untrusted text (from a feed, an API, a pasted page) into plain text that is safe to store.

The importers read copy written by other people. Nothing here trusts it: markup is removed, entities
are decoded once, control and formatting characters (zero-width and bidirectional overrides included)
are dropped, whitespace is collapsed and the length is bounded. What comes back is text a page can
render with ordinary escaping, and none of it can start a tag.

* ``plain_line`` gives one trimmed line, cut at ``max_length`` on a word edge with an ellipsis.
* ``plain_paragraphs`` gives up to ``max_paragraphs`` non-empty lines, each cut the same way. Every
  line break in the source (a ``<br>``, a block element, a newline) starts a new paragraph.
* Text longer than ``MAX_RAW_LENGTH`` is refused before it is parsed (``TextTooLong``).

The tag stripper is a small single-pass scanner written for this module, not ``html.parser``: the
standard parser takes quadratic time on input such as ``"<a " * 6000`` (tens of seconds), which would let
one hostile field stall an import. Here every ``str.find`` that fails is remembered, so the work is linear
in the length. It runs again on its own output (``&lt;b&gt;`` decodes to ``<b>``, which is then removed),
invisible characters are dropped before and after (``<`` + zero-width space + ``script`` must not glue
into a tag), and a last step deletes every ``<`` that still starts something tag-like.
``script`` and ``style`` content is dropped, not kept.
"""

import html
import re
import unicodedata

MAX_RAW_LENGTH = 20_000
ELLIPSIS = "\N{HORIZONTAL ELLIPSIS}"
MAX_STRIP_PASSES = 3
_TAG_START_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz/!?")
_TAG_NAME = re.compile(r"<(/?)([A-Za-z][A-Za-z0-9]*)")
_BLOCK_TAGS = frozenset(
    {
        "address",
        "article",
        "blockquote",
        "br",
        "dd",
        "div",
        "dt",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "hr",
        "li",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "td",
        "th",
        "tr",
        "ul",
    }
)
_SKIPPED_TAGS = frozenset({"script", "style", "template"})
_DROPPED_CATEGORIES = frozenset({"Cf", "Cs", "Co", "Cn"})
_LINE_BREAK_CATEGORIES = frozenset({"Zl", "Zp"})


class TextTooLong(ValueError):
    """The raw text is longer than ``MAX_RAW_LENGTH``."""


class _Finder:
    """``str.find`` that remembers a miss, so repeated misses cost one scan in total."""

    def __init__(self, text: str) -> None:
        self._text = text
        self._lower = text.lower()
        self._missing: set[str] = set()

    def find(self, needle: str, start: int, *, lowered: bool = False) -> int:
        if needle in self._missing:
            return -1
        source = self._lower if lowered else self._text
        found = source.find(needle, start)
        if found < 0:
            self._missing.add(needle)
        return found


def _skip_to_end(finder: _Finder, name: str, start: int) -> int:
    """The index just after the closing tag of a skipped element, or -1 when it never closes."""
    closing = finder.find(f"</{name}", start, lowered=True)
    if closing < 0:
        return -1
    end = finder.find(">", closing)
    return end + 1 if end >= 0 else -1


def _tag_span(finder: _Finder, text: str, index: int) -> tuple[int, str | None, bool]:
    """For a ``<`` at ``index``: where the markup ends, the tag name if any, and whether it closes."""
    if text.startswith("<!--", index):
        end = finder.find("-->", index + 4)
        return (end + 3 if end >= 0 else len(text)), None, False
    end = finder.find(">", index + 1)
    if end < 0:
        return len(text), None, False
    named = _TAG_NAME.match(text, index)
    if named is None:
        return end + 1, None, False
    return end + 1, named.group(2).lower(), named.group(1) == "/"


def _strip_tags(text: str, *, decode: bool) -> str:
    """Text without markup; ``decode`` also turns entities into characters (done once only)."""
    finder, parts, index, length = _Finder(text), [], 0, len(text)
    while index < length:
        lt = text.find("<", index)
        chunk = text[index:] if lt < 0 else text[index:lt]
        parts.append(html.unescape(chunk) if decode else chunk)
        if lt < 0:
            break
        if text[lt + 1 : lt + 2] not in _TAG_START_CHARS:
            parts.append("<")
            index = lt + 1
            continue
        end, name, closing = _tag_span(finder, text, lt)
        if name in _SKIPPED_TAGS and not closing:
            end = _skip_to_end(finder, name, end)
            end = length if end < 0 else end
        elif name in _BLOCK_TAGS:
            parts.append("\n")
        index = end
    return "".join(parts)


def _without_markup(raw: str) -> str:
    text = _strip_tags(raw, decode=True)
    for _ in range(MAX_STRIP_PASSES - 1):
        if "<" not in text:
            break
        text = _strip_tags(text, decode=False)
    return _drop_tag_openers(text)


def _drop_tag_openers(text: str) -> str:
    """Delete every ``<`` that is followed, after any further ``<``, by a tag-like character (linear)."""
    kept: list[str] = []
    opens_a_tag = False
    for char in reversed(text):
        if char == "<":
            if opens_a_tag:
                continue
        else:
            opens_a_tag = char in _TAG_START_CHARS
        kept.append(char)
    return "".join(reversed(kept))


def _clean_characters(text: str) -> str:
    """Drop controls and invisible formatting, keep new lines, turn other spaces into one space."""
    kept: list[str] = []
    for char in unicodedata.normalize("NFC", text):
        category = unicodedata.category(char)
        if char == "\n" or category in _LINE_BREAK_CATEGORIES:
            kept.append("\n")
        elif char in "\t\r\x0b\x0c" or category == "Zs":
            kept.append(" ")
        elif category == "Cc" or category in _DROPPED_CATEGORIES:
            continue
        else:
            kept.append(char)
    return "".join(kept)


def _plain(raw: str) -> str:
    if len(raw) > MAX_RAW_LENGTH:
        raise TextTooLong
    cleaned = _clean_characters(raw)
    stripped = _clean_characters(_without_markup(cleaned))
    # Dropping characters above may have joined a ``<`` to a tag name; remove any such ``<`` last.
    return _drop_tag_openers(stripped)


def _fit(line: str, max_length: int) -> str:
    """``line`` cut to ``max_length`` characters, on a word edge where there is one."""
    if len(line) <= max_length:
        return line
    cut = line[: max_length - 1]
    if " " in cut and not line[max_length - 1].isspace():
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip() + ELLIPSIS


def _collapse(text: str) -> str:
    return " ".join(text.split())


def plain_line(raw: str, max_length: int) -> str:
    """One line of plain text, at most ``max_length`` characters; empty when nothing is left."""
    return _fit(_collapse(_plain(raw)), max_length)


def plain_paragraphs(raw: str, max_paragraphs: int, max_length: int) -> tuple[str, ...]:
    """Up to ``max_paragraphs`` non-empty paragraphs of plain text, each at most ``max_length``."""
    lines = (_collapse(line) for line in _plain(raw).split("\n"))
    return tuple(_fit(line, max_length) for line in lines if line)[:max_paragraphs]
