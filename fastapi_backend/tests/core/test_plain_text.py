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


ZWSP, NUL, SHY = chr(0x200B), chr(0), chr(0xAD)
SURROGATE = chr(0xD800)


@pytest.mark.parametrize(
    "raw",
    [
        f"<{ZWSP}script>alert(1)</script>",
        f"<{NUL}img src=x onerror=alert(1)>",
        f"<{ZWSP}/script>",
        f"x<{SURROGATE}script>",
        f"<{SHY}b>hi",
        "<<img src=x onerror=alert(1)",
        "<<a",
        "<<<<<<<<img src=x",
        "&lt;<!-->script>",
        f"&lt;{ZWSP}script&gt;",
        "&#60;&#8203;script&#62;",
    ],
)
def test_invisible_characters_cannot_glue_a_tag_together_after_the_strip(
    raw: str,
) -> None:
    assert not TAG_LIKE.search(plain_line(raw, 500))
    assert not any(TAG_LIKE.search(p) for p in plain_paragraphs(raw, 5, 500))


@pytest.mark.parametrize(
    "raw",
    [
        "<a " * 6666,
        "<!--" * 5000,
        "<script>" * 2500,
        "<" * 19_000,
        "<a>" * 6000 + "<",
        "<p " * 3000 + ">" + "<b " * 3000,
        "&lt;a " * 3000,
        "<style>" + "</style" * 2500,
    ],
)
def test_hostile_input_takes_linear_time(raw: str) -> None:
    import time

    started = time.perf_counter()
    plain_line(raw, 300)
    plain_paragraphs(raw, 20, 2000)

    assert time.perf_counter() - started < 1.0


DOTTED_I = chr(0x130)  # "I" with a dot: .lower() turns it into two characters


def test_a_script_element_is_dropped_whole_even_after_characters_that_change_length_when_lowered() -> (
    None
):
    raw = "<script>" + DOTTED_I * 10 + "</script>shown?"

    assert plain_line(raw, 200) == "shown?"


def test_lowering_for_tag_search_never_changes_the_length() -> None:
    from app.core.plain_text import _ascii_lower

    text = "AbC" + DOTTED_I * 5 + "SCRIPT"

    lowered = _ascii_lower(text)

    assert len(lowered) == len(text) and lowered.endswith("script")
    assert lowered[3:8] == DOTTED_I * 5  # non-ASCII is left alone


def test_a_lone_less_than_before_a_letter_loses_only_the_sign_not_the_rest() -> None:
    assert plain_line("Spend a<b and save", 200) == "Spend ab and save"
    assert plain_line("1 <a2 and <!x", 200) == "1 a2 and !x"


def test_many_unterminated_openers_still_finish_at_once() -> None:
    import time

    started = time.perf_counter()
    result = plain_line("<a " * 6000, 300)

    assert time.perf_counter() - started < 0.5
    assert "<" not in result


def test_fit_line_collapses_and_cuts_without_cleaning() -> None:
    from app.core.plain_text import fit_line

    assert fit_line("  a   b  ", 50) == "a b"
    assert fit_line("alpha beta gamma delta", 14) == "alpha beta" + ELLIPSIS
    assert (
        fit_line("&amp;lt;b&amp;gt;", 50) == "&amp;lt;b&amp;gt;"
    )  # no second decoding
    assert fit_line(fit_line("x " * 40, 20), 20) == fit_line("x " * 40, 20)


def test_an_unclosed_script_or_style_runs_to_the_end_of_the_text() -> None:
    assert plain_line("<script alert(1)", 200) == ""
    assert plain_line("a <style x", 200) == "a"
    assert plain_line("a <SCRIPT src=x", 200) == "a"
    assert plain_line("a <b unclosed text", 200) == "a b unclosed text"
    assert plain_paragraphs("one\n<script alert(1)", 5, 100) == ("one",)
