"""``parse_promotion``: one Awin item, treated as untrusted input, becomes a ``Promotion`` or a reason code."""

import dataclasses
import json
from datetime import UTC, datetime
from typing import Any

import pytest

from app.services.awin_promotions import (
    MAX_DESCRIPTION_PARAGRAPHS,
    MAX_SUMMARY_LENGTH,
    MAX_TERMS_LENGTH,
    MAX_TITLE_LENGTH,
    Promotion,
    Rejected,
    SkipReason,
    parse_promotion,
    source_hash,
)

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
PUBLISHER_ID = 424242
TRACKING = f"https://www.awin1.com/cread.php?awinmid=77&awinaffid={PUBLISHER_ID}"


def make_item(**changes: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "promotionId": 1001,
        "type": "voucher",
        "advertiser": {"id": 77, "name": "Acme Stores", "joined": True},
        "title": "10% off everything",
        "description": "<p>Save <b>10%</b> on all orders.</p><p>Ends soon.</p>",
        "terms": "New customers only.",
        "startDate": "2026-11-01T00:00:00.000",
        "endDate": "2026-12-01T00:00:00.000",
        "dateAdded": "2026-10-30T00:00:00.000",
        "url": "https://www.acme.example/sale",
        "urlTracking": TRACKING,
        "regions": {
            "all": False,
            "list": [{"name": "United Kingdom", "countryCode": "GB"}],
        },
        "voucher": {"code": "SAVE10", "exclusive": False, "attributable": True},
    }
    return {**item, **changes}


def parsed(**changes: Any) -> Promotion:
    result = parse_promotion(make_item(**changes), NOW, PUBLISHER_ID)
    assert isinstance(result, Promotion), result
    return result


def rejected(item: object) -> SkipReason:
    result = parse_promotion(item, NOW, PUBLISHER_ID)
    assert isinstance(result, Rejected), result
    return result.reason


def without(*keys: str) -> dict[str, Any]:
    return {k: v for k, v in make_item().items() if k not in keys}


# --- a good item ---------------------------------------------------------------------------


def test_a_good_voucher_is_normalised() -> None:
    promotion = parsed()

    assert promotion.promotion_id == 1001
    assert promotion.kind == "voucher"
    assert promotion.advertiser_id == 77
    assert promotion.advertiser_name == "Acme Stores"
    assert promotion.title == "10% off everything"
    assert promotion.description == ("Save 10% on all orders.", "Ends soon.")
    assert promotion.summary == "Save 10% on all orders."
    assert promotion.terms == "New customers only."
    assert promotion.start_at == datetime(2026, 11, 1, tzinfo=UTC)
    assert promotion.end_at == datetime(2026, 12, 1, tzinfo=UTC)
    assert promotion.source_url == "https://www.acme.example/sale"
    assert promotion.tracking_url == TRACKING
    assert promotion.has_voucher_code is True


def test_the_voucher_code_is_never_kept() -> None:
    promotion = parsed()

    assert "SAVE10" not in json.dumps(dataclasses.asdict(promotion), default=str)
    assert "SAVE10" not in repr(promotion)


@pytest.mark.parametrize(
    "voucher", [{"code": None}, {"code": ""}, {"exclusive": True}, None]
)
def test_a_voucher_without_a_code_has_none_to_report(voucher: Any) -> None:
    assert parsed(voucher=voucher).has_voucher_code is False


def test_a_promotion_is_not_a_voucher() -> None:
    promotion = parsed(type="promotion", voucher=None)

    assert promotion.kind == "promotion" and promotion.has_voucher_code is False


def test_the_tracking_url_is_kept_out_of_repr() -> None:
    assert TRACKING not in repr(parsed())


def test_dates_without_a_value_are_open_ended() -> None:
    promotion = parsed(startDate=None, endDate="")

    assert promotion.start_at is None and promotion.end_at is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-12-01T00:00:00.000", datetime(2026, 12, 1, tzinfo=UTC)),
        ("2026-12-01T23:59:59", datetime(2026, 12, 1, 23, 59, 59, tzinfo=UTC)),
        (
            "2026-12-01T23:59:59.5Z",
            datetime(2026, 12, 1, 23, 59, 59, 500000, tzinfo=UTC),
        ),
    ],
)
def test_dates_are_read_strictly_as_utc(raw: str, expected: datetime) -> None:
    assert parsed(endDate=raw).end_at == expected


def test_a_missing_url_leaves_no_source_url() -> None:
    assert parsed(url=None).source_url is None
    assert parse_promotion(without("url"), NOW, PUBLISHER_ID) == parsed(url=None)


def test_regions_that_include_the_uk_or_everywhere_are_accepted() -> None:
    assert parsed(regions={"all": True, "list": []})
    assert parse_promotion(without("regions"), NOW, PUBLISHER_ID)


# --- length and text -----------------------------------------------------------------------


def test_an_over_long_title_is_cut_not_stored_whole() -> None:
    promotion = parsed(title="word " * 400)

    assert len(promotion.title) <= MAX_TITLE_LENGTH
    assert promotion.title.endswith("…")


def test_long_terms_and_many_paragraphs_are_bounded() -> None:
    promotion = parsed(
        terms="t" * 5000,
        description="\n".join(f"paragraph {n}" for n in range(100)),
    )

    assert promotion.terms is not None and len(promotion.terms) <= MAX_TERMS_LENGTH
    assert len(promotion.description) == MAX_DESCRIPTION_PARAGRAPHS


def test_markup_in_every_text_field_is_removed() -> None:
    promotion = parsed(
        title="<script>alert(1)</script>Deal <b>now</b>",
        description="<img src=x onerror=alert(1)>Hello&nbsp;there",
        terms="<a href='https://evil.example'>click</a> terms",
        advertiser={"id": 77, "name": "<i>Acme</i>", "joined": True},
    )

    flat = json.dumps(dataclasses.asdict(promotion), default=str)
    assert "<" not in flat and "onerror" not in flat
    assert promotion.title == "Deal now"
    assert promotion.advertiser_name == "Acme"


def test_a_missing_description_falls_back_to_the_title_for_the_summary() -> None:
    promotion = parsed(description=None)

    assert promotion.description == () and promotion.summary == promotion.title


def test_missing_terms_are_none() -> None:
    assert parsed(terms=None).terms is None


# --- rejections ----------------------------------------------------------------------------


@pytest.mark.parametrize("item", ["text", None, 5, [], [make_item()]])
def test_something_that_is_not_an_object_is_an_invalid_item(item: object) -> None:
    assert rejected(item) is SkipReason.INVALID_ITEM


@pytest.mark.parametrize("value", [None, "1001", 0, -1, True, 1.5, 10**13, []])
def test_a_promotion_id_must_be_a_positive_integer(value: Any) -> None:
    assert rejected(make_item(promotionId=value)) is SkipReason.INVALID_ID
    assert rejected(without("promotionId")) is SkipReason.INVALID_ID


@pytest.mark.parametrize("value", ["cashback", "", None, 3, "VOUCHER"])
def test_only_promotions_and_vouchers_are_imported(value: Any) -> None:
    assert rejected(make_item(type=value)) is SkipReason.UNSUPPORTED_TYPE


@pytest.mark.parametrize(
    "advertiser",
    [
        None,
        "Acme",
        {},
        {"id": 0, "name": "x", "joined": True},
        {"id": "77", "name": "x", "joined": True},
        {"id": 77, "name": "", "joined": True},
        {"id": 77, "name": "<br>", "joined": True},
        {"id": 77, "name": 5, "joined": True},
        {"id": 77, "name": "x"},
        {"id": 77, "name": "x", "joined": "yes"},
    ],
)
def test_an_advertiser_that_cannot_be_read_is_invalid(advertiser: Any) -> None:
    assert rejected(make_item(advertiser=advertiser)) is SkipReason.INVALID_ADVERTISER


def test_an_advertiser_the_publisher_has_not_joined_is_skipped() -> None:
    item = make_item(advertiser={"id": 77, "name": "Acme", "joined": False})

    assert rejected(item) is SkipReason.NOT_JOINED


@pytest.mark.parametrize("title", [None, "", "   ", "<br>", "\u200b", 5, ["x"]])
def test_a_title_must_survive_cleaning(title: Any) -> None:
    assert rejected(make_item(title=title)) is SkipReason.INVALID_TITLE
    assert rejected(without("title")) is SkipReason.INVALID_TITLE


@pytest.mark.parametrize(
    "value",
    [
        "2026-11-01",
        "11/01/2026",
        "2026-13-01T00:00:00.000",
        "2026-02-30T00:00:00.000",
        "2026-11-01T00:00:00+02:00",
        "2026-11-01 00:00:00",
        "1999-01-01T00:00:00.000",
        "2101-01-01T00:00:00.000",
        20261101,
        True,
        "x" * 100,
    ],
)
@pytest.mark.parametrize("field", ["startDate", "endDate"])
def test_a_date_that_is_not_the_documented_shape_is_refused(
    field: str, value: Any
) -> None:
    assert rejected(make_item(**{field: value})) is SkipReason.INVALID_DATE


def test_an_end_before_the_start_is_refused() -> None:
    item = make_item(
        startDate="2026-12-01T00:00:00.000", endDate="2026-11-20T00:00:00.000"
    )

    assert rejected(item) is SkipReason.INVALID_WINDOW


def test_an_offer_that_has_already_ended_is_skipped() -> None:
    item = make_item(startDate=None, endDate="2026-11-09T12:00:00.000")

    assert rejected(item) is SkipReason.ENDED


@pytest.mark.parametrize(
    "url", ["http://www.acme.example/", "javascript:alert(1)", "https://127.0.0.1/", 5]
)
def test_a_source_url_must_be_a_clean_https_url(url: Any) -> None:
    assert rejected(make_item(url=url)) is SkipReason.INVALID_URL


def test_a_source_url_on_a_tracking_host_is_refused() -> None:
    item = make_item(url="https://www.awin1.com/cread.php?x=1")

    assert rejected(item) is SkipReason.SOURCE_URL_ON_TRACKING_HOST


@pytest.mark.parametrize("value", [None, "", "   "])
def test_a_tracking_url_is_required(value: Any) -> None:
    assert rejected(make_item(urlTracking=value)) is SkipReason.MISSING_TRACKING_URL
    assert rejected(without("urlTracking")) is SkipReason.MISSING_TRACKING_URL


@pytest.mark.parametrize(
    "url",
    [
        "http://www.awin1.com/cread.php",
        "https://127.0.0.1/cread.php",
        "https://www.awin1.com@evil.example/x",
        "https://awin1.com:8443/x",
        "https://www.awin1.com/x\r\nSet-Cookie: a=b",
        "//www.awin1.com/x",
        5,
    ],
)
def test_a_tracking_url_must_be_a_clean_https_url(url: Any) -> None:
    assert rejected(make_item(urlTracking=url)) is SkipReason.INVALID_TRACKING_URL


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/cread.php",
        "https://awin1.com.evil.example/cread.php",
        "https://evilawin1.com/cread.php",
        "https://notawin1.com/cread.php",
        "https://www.awin1.co/cread.php",
        "https://cj.anrdoezrs.net/click-1",
        "https://www.acme.example/sale",
    ],
)
def test_a_tracking_url_must_be_on_an_awin_host(url: str) -> None:
    assert rejected(make_item(urlTracking=url)) is SkipReason.TRACKING_HOST_NOT_ALLOWED


@pytest.mark.parametrize(
    "url",
    [
        f"https://www.awin1.com/cread.php?awinmid=77&awinaffid={PUBLISHER_ID}",
        f"https://AWIN1.COM/cread.php?awinmid=77&awinaffid={PUBLISHER_ID}",
        f"https://a.b.awin1.com/cread.php?awinmid=77&awinaffid={PUBLISHER_ID}",
    ],
)
def test_awin_hosts_and_their_subdomains_are_accepted(url: str) -> None:
    assert parsed(urlTracking=url).tracking_url.startswith("https://")


@pytest.mark.parametrize(
    "regions",
    [
        {"all": False, "list": [{"countryCode": "FR"}]},
        {"all": False, "list": []},
        {"all": False},
        {},
        "GB",
        ["GB"],
    ],
)
def test_an_item_that_is_not_for_the_uk_is_skipped(regions: Any) -> None:
    assert rejected(make_item(regions=regions)) is SkipReason.REGION_MISMATCH


@pytest.mark.parametrize(
    "voucher", ["SAVE10", 5, ["x"], {"code": 5}, {"code": "x" * 600}]
)
def test_a_voucher_object_that_is_malformed_is_refused(voucher: Any) -> None:
    assert rejected(make_item(voucher=voucher)) is SkipReason.INVALID_VOUCHER


def test_text_far_beyond_any_sane_length_is_refused_whole() -> None:
    assert rejected(make_item(description="x" * 50_000)) is SkipReason.FIELD_TOO_LONG
    assert rejected(make_item(title="x" * 50_000)) is SkipReason.FIELD_TOO_LONG


def test_a_rejection_never_carries_the_item() -> None:
    result = parse_promotion(
        make_item(title=None, description="SECRET-COPY"), NOW, PUBLISHER_ID
    )

    assert isinstance(result, Rejected)
    assert "SECRET-COPY" not in repr(result)


# --- the source hash -----------------------------------------------------------------------


def test_the_hash_ignores_key_order_and_fields_the_importer_does_not_use() -> None:
    reordered = dict(
        reversed(list(make_item(dateAdded="2020-01-01T00:00:00.000").items()))
    )
    reordered["somethingNew"] = {"added": "later"}

    assert source_hash(parsed()) == source_hash(
        parse_promotion(reordered, NOW, PUBLISHER_ID)  # type: ignore[arg-type]
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"title": "20% off everything"},
        {"description": "Different words."},
        {"terms": "Other terms."},
        {"endDate": "2026-12-02T00:00:00.000"},
        {"url": "https://www.acme.example/other"},
        {"urlTracking": TRACKING + "&clickref=other"},
        {"type": "promotion"},
        {"voucher": {"code": None}},
    ],
)
def test_the_hash_changes_when_anything_that_is_stored_changes(
    changes: dict[str, Any],
) -> None:
    assert source_hash(parsed(**changes)) != source_hash(parsed())


def test_the_hash_is_a_sha256_hex_digest() -> None:
    digest = source_hash(parsed())

    assert len(digest) == 64 and int(digest, 16) >= 0


def test_a_changed_voucher_code_does_not_change_the_hash_because_codes_are_not_stored() -> (
    None
):
    first = parsed(voucher={"code": "AAA"})
    second = parsed(voucher={"code": "BBB"})

    assert source_hash(first) == source_hash(second)


def test_the_hash_ignores_the_advertiser_name_because_nothing_is_written_from_it() -> (
    None
):
    renamed = parsed(advertiser={"id": 77, "name": "Acme Renamed", "joined": True})

    assert source_hash(renamed) == source_hash(parsed())


def test_the_hash_covers_the_advertiser_id() -> None:
    other = parsed(
        advertiser={"id": 78, "name": "Acme Stores", "joined": True},
        urlTracking=f"https://www.awin1.com/cread.php?awinmid=78&awinaffid={PUBLISHER_ID}",
    )

    assert source_hash(other) != source_hash(parsed())


def test_the_summary_is_the_first_paragraph_not_a_second_decoding_of_it() -> None:
    promotion = parsed(description="AT&amp;amp;T deal &amp;lt;b&amp;gt; x\nSecond")

    assert promotion.description[0] == "AT&amp;T deal &lt;b&gt; x"
    assert promotion.summary == promotion.description[0]


def test_a_long_first_paragraph_is_cut_to_the_summary_limit() -> None:
    promotion = parsed(description="word " * 200)

    assert len(promotion.summary) <= MAX_SUMMARY_LENGTH
    assert promotion.summary.endswith("\N{HORIZONTAL ELLIPSIS}")
