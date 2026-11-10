"""One Awin transaction row as untrusted input: every rejection, amount edge cases, hash stability."""

from datetime import UTC, datetime
from typing import Any

import pytest

from app.models.platform.network_report import CommissionStatus
from app.services.awin_transactions import (
    Rejected,
    ReportRow,
    SkipReason,
    parse_transaction,
    report_hash,
)
from tests.awin_report_samples import raw_row, row

# Pinned: bumping HASH_VERSION or reshaping the canonical form changes this digest on purpose, because
# every stored row then reads as changed once. Change both together.
PINNED_DIGEST = "316bc89ea7c6b299ccc4fd11f41eaa6e21d77d94a2071725db5b445da02de73b"


def amount(value: Any, currency: Any = "GBP") -> dict[str, Any]:
    return {"amount": value, "currency": currency}


def reason_of(item: object) -> SkipReason:
    result = parse_transaction(item)
    assert isinstance(result, Rejected), result
    return result.reason


# --- a valid row ---------------------------------------------------------------------------


def test_a_valid_row_becomes_a_frozen_report_row_in_minor_units() -> None:
    parsed = row(5001)

    assert parsed == ReportRow(
        external_id="5001",
        advertiser_id=77,
        transaction_at=datetime(2026, 11, 3, 10, 15, tzinfo=UTC),
        status=CommissionStatus.APPROVED,
        status_raw="approved",
        commission_amount_minor=150,
        sale_amount_minor=3000,
        currency="GBP",
        click_ref="ref-5001",
    )
    with pytest.raises(AttributeError):
        parsed.currency = "USD"  # type: ignore[misc]


def test_a_row_without_a_click_reference_keeps_none() -> None:
    assert row(1, clickRefs=None).click_ref is None
    assert row(1, clickRefs={}).click_ref is None
    assert row(1, clickRefs={"clickRef": ""}).click_ref is None
    item = raw_row(1)
    del item["clickRefs"]
    parsed = parse_transaction(item)
    assert isinstance(parsed, ReportRow) and parsed.click_ref is None


def test_a_top_level_click_reference_is_read_when_there_is_no_clickrefs_object() -> (
    None
):
    item = raw_row(1)
    del item["clickRefs"]

    parsed = parse_transaction({**item, "clickRef": "top-level"})

    assert isinstance(parsed, ReportRow) and parsed.click_ref == "top-level"


def test_text_fields_are_stripped_and_the_raw_status_is_kept_as_written() -> None:
    parsed = row(1, commissionStatus="  Approved ", clickRefs={"clickRef": " abc "})

    assert parsed.status is CommissionStatus.APPROVED
    assert parsed.status_raw == "Approved" and parsed.click_ref == "abc"


@pytest.mark.parametrize(
    ("raw", "mapped"),
    [
        ("pending", CommissionStatus.PENDING),
        ("approved", CommissionStatus.APPROVED),
        ("declined", CommissionStatus.DECLINED),
        ("paid", CommissionStatus.PAID),
        ("deleted", CommissionStatus.DELETED),
        ("PENDING", CommissionStatus.PENDING),
    ],
)
def test_the_known_statuses_map_onto_the_four(
    raw: str, mapped: CommissionStatus
) -> None:
    assert row(1, commissionStatus=raw).status is mapped


# --- rejections ----------------------------------------------------------------------------


@pytest.mark.parametrize("item", [None, [], "x", 5, 1.5, True])
def test_anything_that_is_not_an_object_is_invalid_item(item: object) -> None:
    assert reason_of(item) is SkipReason.INVALID_ITEM


@pytest.mark.parametrize("value", [None, "5001", 5001.0, True, 0, -1, 10**15 + 1, []])
def test_an_id_must_be_a_positive_integer(value: object) -> None:
    assert reason_of(raw_row(1, id=value)) is SkipReason.INVALID_ID


def test_a_missing_id_is_invalid() -> None:
    item = raw_row(1)
    del item["id"]

    assert reason_of(item) is SkipReason.INVALID_ID


@pytest.mark.parametrize("value", [None, "77", 77.0, True, 0, -5, [], {}])
def test_an_advertiser_id_must_be_a_positive_integer(value: object) -> None:
    assert reason_of(raw_row(1, advertiserId=value)) is SkipReason.INVALID_ADVERTISER


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "2026-11-03",
        "2026-11-03 10:15:00",
        "2026-11-03T10:15:00+01:00",
        "2026-13-03T10:15:00",
        "2026-11-31T10:15:00",
        "1999-11-03T10:15:00",
        "2101-11-03T10:15:00",
        1762164900,
        True,
    ],
)
def test_a_transaction_date_must_be_a_utc_timestamp(value: object) -> None:
    assert reason_of(raw_row(1, transactionDate=value)) is SkipReason.INVALID_DATE


@pytest.mark.parametrize(
    "value", ["2026-11-03T10:15:00", "2026-11-03T10:15:00Z", "2026-11-03T10:15:00.123"]
)
def test_the_accepted_timestamp_shapes_are_read_as_utc(value: str) -> None:
    parsed = row(1, transactionDate=value)

    assert parsed.transaction_at.tzinfo is UTC and parsed.transaction_at.hour == 10


@pytest.mark.parametrize("value", [None, 1, True, [], {}, "", "   ", "x" * 33, "a\nb"])
def test_a_status_must_be_a_short_plain_string(value: object) -> None:
    assert reason_of(raw_row(1, commissionStatus=value)) is SkipReason.INVALID_STATUS


@pytest.mark.parametrize("value", ["validated", "refunded", "paid out"])
def test_a_status_outside_the_vocabulary_is_unknown_not_guessed(value: str) -> None:
    assert reason_of(raw_row(1, commissionStatus=value)) is SkipReason.UNKNOWN_STATUS


@pytest.mark.parametrize("key", ["commissionAmount", "saleAmount"])
@pytest.mark.parametrize(
    "value",
    [
        None,
        "1.50",
        5,
        [],
        {},
        {"currency": "GBP"},
        {"amount": None, "currency": "GBP"},
    ],
)
def test_an_amount_object_must_carry_an_amount(key: str, value: object) -> None:
    assert reason_of(raw_row(1, **{key: value})) is SkipReason.INVALID_AMOUNT


@pytest.mark.parametrize(
    "value",
    [
        -1,
        -0.01,
        "-1.00",
        1.234,
        "1.234",
        "1.2345",
        float("nan"),
        float("inf"),
        float("-inf"),
        "NaN",
        "Infinity",
        "1e2",
        "1_000",
        " 1.00",
        "1,50",
        "£1.50",
        "",
        "0x10",
        True,
        False,
        [],
        {},
        10**12,
        1e22,
        1e-05,
        "9999999999",
    ],
)
def test_an_amount_that_is_negative_non_finite_over_precise_or_odd_is_rejected(
    value: object,
) -> None:
    item = raw_row(1, commissionAmount=amount(value))

    assert reason_of(item) is SkipReason.INVALID_AMOUNT


@pytest.mark.parametrize(
    ("value", "minor"),
    [
        (0, 0),
        (0.0, 0),
        (-0.0, 0),
        (1, 100),
        (1.5, 150),
        (0.1, 10),
        (0.29, 29),  # 0.29 * 100 is 28.999999999999996 in floats
        (1.15, 115),
        (19.99, 1999),
        (4.35, 435),
        (1.5e1, 1500),
        ("12", 1200),
        ("12.3", 1230),
        ("12.34", 1234),
        ("12.340", 1234),
        ("0.01", 1),
        ("999999999.99", 99_999_999_999),
    ],
)
def test_amounts_become_minor_units_without_float_rounding(
    value: object, minor: int
) -> None:
    parsed = row(1, commissionAmount=amount(value), saleAmount=amount(value))

    assert parsed.commission_amount_minor == minor
    assert parsed.sale_amount_minor == minor


@pytest.mark.parametrize(
    "value", [None, "gbp", "GB", "GBPP", "£", "G B", " GBP", 826, True, [], "GBP\n"]
)
def test_a_currency_must_be_three_upper_case_ascii_letters(value: object) -> None:
    item = raw_row(1, commissionAmount=amount(1.5, value), saleAmount=amount(3, value))

    assert reason_of(item) is SkipReason.INVALID_CURRENCY


def test_the_commission_and_sale_currencies_must_agree() -> None:
    item = raw_row(1, saleAmount=amount(30, "EUR"))

    assert reason_of(item) is SkipReason.CURRENCY_MISMATCH


@pytest.mark.parametrize(
    "refs",
    [
        {"clickRef": 5},
        {"clickRef": ["a"]},
        {"clickRef": "has space"},
        {"clickRef": "x" * 101},
        {"clickRef": "a/b"},
        {"clickRef": "café"},
        {"clickRef": "a\nb"},
        "string",
        ["a"],
    ],
)
def test_a_click_reference_must_be_a_short_plain_token(refs: object) -> None:
    assert reason_of(raw_row(1, clickRefs=refs)) is SkipReason.INVALID_CLICK_REF


@pytest.mark.parametrize(
    "code", ["GBP", "EUR", "USD", "AUD", "CAD", "CHF", "DKK", "NOK", "SEK", "PLN"]
)
def test_a_two_decimal_currency_is_accepted(code: str) -> None:
    parsed = row(1, commissionAmount=amount(1.5, code), saleAmount=amount(3, code))

    assert parsed.currency == code and parsed.commission_amount_minor == 150


@pytest.mark.parametrize("code", ["JPY", "KWD", "BHD", "XXX", "ZZZ"])
def test_a_currency_that_is_not_two_decimal_is_unsupported_not_scaled_wrong(
    code: str,
) -> None:
    item = raw_row(1, commissionAmount=amount(1, code), saleAmount=amount(3, code))

    assert reason_of(item) is SkipReason.UNSUPPORTED_CURRENCY


def test_a_rejection_never_carries_the_row() -> None:
    result = parse_transaction(raw_row(1, commissionStatus="secret-status-text"))

    assert isinstance(result, Rejected)
    assert "secret-status-text" not in repr(result)


# --- the hash ------------------------------------------------------------------------------


def test_the_hash_is_a_sha256_hex_digest() -> None:
    digest = report_hash(row(1))

    assert len(digest) == 64 and set(digest) <= set("0123456789abcdef")


def test_the_hash_ignores_field_order_whitespace_and_unused_keys() -> None:
    plain = report_hash(row(1))
    shuffled = dict(reversed(list(raw_row(1).items())))
    spaced = raw_row(
        1,
        commissionStatus=" approved ",
        clickRefs={"clickRef": " ref-1 "},
    )

    as_parsed = parse_transaction({**shuffled, "extra": {"anything": 1}})
    as_spaced = parse_transaction(spaced)

    assert isinstance(as_parsed, ReportRow) and isinstance(as_spaced, ReportRow)
    assert report_hash(as_parsed) == plain
    assert report_hash(as_spaced) == plain


def test_the_hash_ignores_how_a_number_was_written() -> None:
    as_float = report_hash(row(1, commissionAmount=amount(1.5)))
    as_text = report_hash(row(1, commissionAmount=amount("1.50")))

    assert as_float == as_text


@pytest.mark.parametrize(
    "changes",
    [
        {"commissionStatus": "declined"},
        {"commissionAmount": amount(1.51)},
        {"saleAmount": amount(31)},
        {"commissionAmount": amount(1.5, "EUR"), "saleAmount": amount(30, "EUR")},
        {"transactionDate": "2026-11-03T10:16:00"},
        {"advertiserId": 78},
        {"clickRefs": {"clickRef": "other"}},
        {"clickRefs": None},
        {"commissionStatus": "Approved"},
    ],
)
def test_the_hash_changes_when_any_reported_fact_changes(
    changes: dict[str, Any],
) -> None:
    assert report_hash(row(1, **changes)) != report_hash(row(1))


def test_the_hash_of_a_known_row_is_pinned() -> None:
    assert report_hash(row(5001)) == PINNED_DIGEST
