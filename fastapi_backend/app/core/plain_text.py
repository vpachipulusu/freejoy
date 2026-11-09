"""Turn untrusted text (from a feed, an API, a pasted page) into plain text that is safe to store.

The importers read copy written by other people. Nothing here trusts it: markup is removed, entities
are decoded once, control and formatting characters (zero-width and bidirectional overrides included)
are dropped, whitespace is collapsed and the length is bounded. What comes back is text a page can
render with ordinary escaping, and none of it can start a tag.

* ``plain_line`` gives one trimmed line, cut at ``max_length`` on a word edge with an ellipsis.
* ``plain_paragraphs`` gives up to ``max_paragraphs`` non-empty lines, each cut the same way. Every
  line break in the source (a ``<br>``, a block element, a newline) starts a new paragraph.
* Text longer than ``MAX_RAW_LENGTH`` is refused before it is parsed (``TextTooLong``), so a hostile
  megabyte cannot cost CPU.

The tag stripper is ``html.parser`` (standard library), run again on its own output until no tag-like
text is left (``&lt;b&gt;`` decodes to ``<b>``, which is then removed), and any ``<`` that still
starts something tag-like afterwards is deleted. ``script`` and ``style`` content is dropped, not kept.
"""

import re
import unicodedata
from html.parser import HTMLParser

MAX_RAW_LENGTH = 20_000
ELLIPSIS = "…"
MAX_STRIP_PASSES = 3
_TAG_START = re.compile(r"<(?=[A-Za-z/!?])")
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


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skipping = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIPPED_TAGS:
            self._skipping += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIPPED_TAGS:
            self._skipping = max(self._skipping - 1, 0)
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skipping:
            self.parts.append(data)


def _strip_tags(text: str) -> str:
    parser = _TextExtractor()
    parser.feed(text)
    parser.close()
    return "".join(parser.parts)


def _without_markup(raw: str) -> str:
    text = raw
    for _ in range(MAX_STRIP_PASSES):
        if "<" not in text and "&" not in text:
            break
        text = _strip_tags(text)
    return _TAG_START.sub("", text)


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
    return _clean_characters(_without_markup(raw))


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
