"""Cleaning and validating a submission: plain text only, https links on the merchant's own host, shared rules (FREEJ-58)."""

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.core.plain_text import MAX_RAW_LENGTH
from app.partner.content import (
    CleanContent,
    clean_destination,
    clean_submission,
    hash_payload,
)
from app.partner.errors import SubmissionRefused
from app.partner.schemas import OfferSubmission

NOW = datetime(2026, 6, 1, tzinfo=UTC)
SITE = "https://shop.example.com"


def submission(**changes: Any) -> OfferSubmission:
    values: dict[str, Any] = {
        "title": "Free sample",
        "summary": "A sample",
        "description": [],
        "offer_type": "FREE_SAMPLE",
        "category": "home",
        "url": f"{SITE}/x",
        "postage_kind": "FREE",
        "requires_card": False,
        "subscription_terms": None,
        "eligibility": "UK",
        "availability": "Stocks",
        "steps": [],
        "start_at": None,
        "end_at": None,
    }
    return OfferSubmission.model_validate({**values, **changes})


def clean(**changes: Any) -> CleanContent:
    return clean_submission(
        submission(**changes), category_id="cat1", merchant_website=SITE, now=NOW
    )


def refusal(**changes: Any) -> str:
    with pytest.raises(SubmissionRefused) as caught:
        clean(**changes)
    return caught.value.code


HOSTILE = [
    ("<script>alert(1)</script>Free", "Free"),
    ("<img src=x onerror=alert(1)>Free", "Free"),
    ("Free &lt;script&gt;alert(1)&lt;/script&gt; box", "Free box"),
    ("Free\u200b" + chr(0x202E) + " box", "Free box"),
    ("Free\x00\x07\x1b box", "Free box"),
    ("<<script>script>x</script>Free", "Free"),
    ("<a href='javascript:x'>Free</a>", "Free"),
    ("Free\n\n\tbox", "Free box"),
    ("&amp;lt;b&amp;gt;", "&lt;b&gt;"),
    ("<!-- c -->Free<!-- d -->", "Free"),
    ("Café", "Café"),
]


@pytest.mark.parametrize(("raw", "expected"), HOSTILE)
def test_a_title_is_plain_text_whatever_markup_or_control_characters_it_carries(
    raw: str, expected: str
) -> None:
    assert clean(title=raw).title == expected


@pytest.mark.parametrize(
    "raw", ["<script>x()</script>", "\u200b\u200b", "<br>", "   ", "<!-- c -->"]
)
def test_a_text_with_nothing_left_after_cleaning_is_refused(raw: str) -> None:
    assert refusal(summary=raw) == "empty_text"
    assert refusal(eligibility=raw) == "empty_text"
    assert refusal(availability=raw) == "empty_text"


def test_a_paragraph_that_is_only_markup_is_dropped_but_the_others_stay() -> None:
    content = clean(
        description=["<b>One</b>", "<script>x</script>", "Two <i>words</i>"]
    )

    assert content.description == ("One", "Two words")


def test_steps_and_terms_are_cleaned_and_a_markup_only_step_is_refused() -> None:
    content = clean(
        offer_type="FREE_TRIAL",
        subscription_terms="<b>Cancel</b> any time",
        steps=[{"title": "<em>Go</em>", "detail": "Open\x00 it"}],
    )

    assert content.subscription_terms == "Cancel any time"
    assert content.steps == (("Go", "Open it"),)
    assert refusal(steps=[{"title": "<b></b>", "detail": "x"}]) == "empty_text"
    assert refusal(subscription_terms="<br>") == "empty_text"


def test_a_hostile_text_at_the_cap_comes_out_without_a_tag_opener() -> None:
    payload = "<a " * 36 + "x" * 11  # exactly the title cap

    assert len(payload) == 119
    assert "<" not in clean(title=payload).title
    assert clean(summary=("<b>" + "word " * 30 + "</b>")[:200]).summary.startswith(
        "word"
    )


def test_a_raw_text_the_cleaner_refuses_is_the_same_fixed_refusal_not_a_crash() -> None:
    from app.partner import content

    huge = "x" * (MAX_RAW_LENGTH + 1)
    model = submission().model_copy(update={"title": huge})

    with pytest.raises(SubmissionRefused) as caught:
        content.clean_submission(model, category_id="c", merchant_website=SITE, now=NOW)

    assert caught.value.code == "empty_text"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://shop.example.com/x", "https://shop.example.com/x"),
        ("https://www.shop.example.com/x", "https://www.shop.example.com/x"),
        ("https://SHOP.EXAMPLE.COM/Path?q=1#f", "https://shop.example.com/Path?q=1#f"),
    ],
)
def test_a_link_on_the_merchants_host_or_its_www_form_is_accepted_and_normalised(
    url: str, expected: str
) -> None:
    assert clean_destination(url, SITE) == expected


@pytest.mark.parametrize(
    ("url", "code"),
    [
        ("https://other.example.com/x", "link_host_not_allowed"),
        ("https://shop.example.com.evil.net/x", "link_host_not_allowed"),
        ("https://evil.net/shop.example.com", "link_host_not_allowed"),
        ("https://sub.shop.example.com/x", "link_host_not_allowed"),
        ("https://awin1.com/x", "link_host_not_allowed"),
        ("http://shop.example.com/x", "invalid_url"),
        ("https://shop.example.com:8443/x", "invalid_url"),
        ("https://user:pw@shop.example.com/x", "invalid_url"),
        ("https://127.0.0.1/x", "invalid_url"),
        ("https://shop.example.com/\r\nX: y", "invalid_url"),
        ("https://shop.example.com/%0d%0a", "invalid_url"),
        ("data:text/html,x", "invalid_url"),
        ("", "invalid_url"),
    ],
)
def test_any_other_link_is_refused_with_a_fixed_code(url: str, code: str) -> None:
    with pytest.raises(SubmissionRefused) as caught:
        clean_destination(url, SITE)

    assert caught.value.code == code


@pytest.mark.parametrize("website", [None, "", "not a url", "http://shop.example.com"])
def test_a_merchant_without_a_usable_website_can_link_nowhere(
    website: str | None,
) -> None:
    with pytest.raises(SubmissionRefused) as caught:
        clean_destination(f"{SITE}/x", website)

    assert caught.value.code == "link_host_not_allowed"


def test_the_stored_website_is_trusted_with_or_without_www() -> None:
    assert clean_destination(f"{SITE}/x", "https://www.shop.example.com") == f"{SITE}/x"


def test_postage_follows_the_shared_rule() -> None:
    assert refusal(postage_kind="COST") == "invalid_postage"
    assert refusal(postage_kind="FREE", postage_amount_minor=1) == "invalid_postage"
    assert refusal(postage_kind="COST", postage_amount_minor=5) == "invalid_postage"
    ok = clean(postage_kind="COST", postage_amount_minor=299, postage_currency="GBP")
    assert (ok.postage_amount_minor, ok.postage_currency) == (299, "GBP")


def test_a_window_must_end_after_it_starts_and_in_the_future() -> None:
    later = (NOW + timedelta(days=2)).isoformat()
    soon = (NOW + timedelta(days=1)).isoformat()
    assert refusal(start_at=later, end_at=soon) == "invalid_window"
    assert refusal(end_at=(NOW - timedelta(days=1)).isoformat()) == "invalid_window"
    assert refusal(end_at=NOW.isoformat()) == "invalid_window"
    assert clean(start_at=soon, end_at=later).end_at is not None


def test_dates_are_stored_in_utc() -> None:
    content = clean(end_at="2099-01-01T12:00:00+02:00")

    assert content.end_at == datetime(2099, 1, 1, 10, 0, tzinfo=UTC)
    assert content.payload()["end_at"] == "2099-01-01T10:00:00+00:00"


def test_a_trial_needs_subscription_terms_a_sample_does_not() -> None:
    assert refusal(offer_type="FREE_TRIAL") == "subscription_terms_required"
    assert clean(offer_type="FREE_SAMPLE").subscription_terms is None


def test_the_payload_is_json_safe_and_complete_and_its_hash_is_stable() -> None:
    content = clean(description=["One"], steps=[{"title": "T", "detail": "D"}])

    payload = content.payload()

    assert json.loads(json.dumps(payload)) == payload
    assert set(payload) == {
        "title",
        "summary",
        "description",
        "category_id",
        "offer_type",
        "postage_kind",
        "postage_amount_minor",
        "postage_currency",
        "requires_card",
        "subscription_terms",
        "eligibility",
        "availability",
        "steps",
        "start_at",
        "end_at",
        "destination_url",
    }
    assert (
        content.hash
        == hash_payload(payload)
        == clean(description=["One"], steps=[{"title": "T", "detail": "D"}]).hash
    )
    assert content.payload() is not content.payload()  # a fresh dict each time


def test_a_different_content_has_a_different_hash_and_markup_noise_does_not() -> None:
    assert clean(title="One").hash != clean(title="Two").hash
    assert clean(title="One").hash == clean(title="<b>One</b>").hash


def test_the_offer_values_are_the_columns_without_the_link_and_with_real_dates() -> (
    None
):
    content = clean(end_at="2099-01-01T00:00:00Z")

    values = content.offer_values()

    assert "destination_url" not in values
    assert values["end_at"] == datetime(2099, 1, 1, tzinfo=UTC)
    assert values["category_id"] == "cat1"
    assert (
        "status" not in values
        and "featured" not in values
        and "commercial_basis" not in values
    )


@pytest.mark.parametrize(
    "when",
    ["0001-01-01T00:00:00+05:00", "2101-01-01T00:00:00Z", "9999-12-31T23:59:59Z"],
)
def test_a_date_that_cannot_be_stored_or_is_absurdly_far_off_is_refused_not_a_crash(
    when: str,
) -> None:
    assert refusal(end_at=when) == "invalid_window"
    assert refusal(start_at=when, end_at="2099-01-01T00:00:00Z") == "invalid_window"
