"""``app.core.plain_text``: untrusted text in, plain text out. Nothing returned may render as markup."""

import re

import pytest

from app.core.plain_text import (
    ELLIPSIS,
    MAX_RAW_LENGTH,
    TextTooLong,
    plain_line,
    plain_paragraphs,
)

TAG_LIKE = re.compile(r"<[A-Za-z/!?]")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Plain title", "Plain title"),
        ("  spaced \t out \n title  ", "spaced out title"),
        ("<b>Bold</b> offer", "Bold offer"),
        ("20% off <a href='https://x.example/?a=1&b=2'>shoes</a>", "20% off shoes"),
        ("Tom &amp; Jerry &pound;5", "Tom & Jerry £5"),
        ("&lt;script&gt;alert(1)&lt;/script&gt;", ""),
        ("<script>alert(1)</script>Safe", "Safe"),
        ("<style>p{color:red}</style>Safe", "Safe"),
        ("<!-- hidden -->Shown", "Shown"),
        ("<img src=x onerror=alert(1)>Caption", "Caption"),
        ("a<br>b<br/>c", "a b c"),
        ("<p>one</p><p>two</p>", "one two"),
        ("5 < 10 and 10 > 5", "5 < 10 and 10 > 5"),
        ("zero\u200bwidth", "zerowidth"),
        ("bidi \u202egnp.exe", "bidi gnp.exe"),
        ("nul\x00byte and \x07bell", "nulbyte and bell"),
        ("line\u2028separator", "line separator"),
        ("e\u0301 composed", "é composed"),
    ],
)
def test_a_line_comes_back_as_plain_single_line_text(raw: str, expected: str) -> None:
    assert plain_line(raw, 200) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "<<script>script>alert(1)<</script>/script>",
        "&amp;lt;b&amp;gt;x&amp;lt;/b&amp;gt;",
        "<a <b>x</b>",
        "<svg/onload=alert(1)>",
        "text <iframe src=//evil.example",
        "<?php echo 1 ?>",
        "<!DOCTYPE html><html>x</html>",
        "&#60;img src=x&#62;",
    ],
)
def test_nothing_that_looks_like_a_tag_survives_however_it_is_nested_or_encoded(
    raw: str,
) -> None:
    assert not TAG_LIKE.search(plain_line(raw, 500))
    assert not any(TAG_LIKE.search(p) for p in plain_paragraphs(raw, 5, 500))


def test_a_line_over_the_limit_is_cut_on_a_word_with_an_ellipsis() -> None:
    result = plain_line("alpha beta gamma delta", 14)

    assert result == f"alpha beta{ELLIPSIS}"
    assert len(result) <= 14


def test_a_single_long_word_is_cut_hard() -> None:
    result = plain_line("x" * 50, 10)

    assert result == "x" * 9 + ELLIPSIS


def test_nothing_left_after_cleaning_is_an_empty_string() -> None:
    assert plain_line("<br><p> </p>\u200b", 50) == ""


def test_text_past_the_raw_limit_is_refused_before_any_parsing() -> None:
    with pytest.raises(TextTooLong):
        plain_line("a" * (MAX_RAW_LENGTH + 1), 50)


def test_paragraphs_split_on_blocks_breaks_and_new_lines_and_drop_empties() -> None:
    raw = "<p>First para</p><p>Second<br>line</p>\n\n\nThird\n  \nFourth"

    assert plain_paragraphs(raw, 10, 100) == (
        "First para",
        "Second",
        "line",
        "Third",
        "Fourth",
    )


def test_paragraphs_are_capped_in_number_and_length() -> None:
    raw = "\n".join(f"paragraph number {n}" for n in range(30))

    result = plain_paragraphs(raw, 5, 12)

    assert len(result) == 5
    assert all(len(p) <= 12 for p in result)


def test_paragraphs_of_nothing_is_an_empty_tuple() -> None:
    assert plain_paragraphs("<p></p>", 5, 50) == ()
