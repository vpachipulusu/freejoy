"""The pure content rules shared by the admin side and the partner API."""

from datetime import UTC, datetime, timedelta

import pytest

from app.core.offer_content import (
    AUDITED_FIELDS,
    EDITABLE_FIELDS,
    postage_problem,
    window_problem,
)
from app.services.admin_catalogue_errors import InvalidInput
from app.services.offer_rules import check_postage, check_window

NOW = datetime(2026, 6, 1, tzinfo=UTC)


@pytest.mark.parametrize(
    ("values", "problem"),
    [
        ({"postage_kind": "FREE"}, None),
        ({"postage_kind": "CHECK_TERMS", "postage_amount_minor": None}, None),
        (
            {
                "postage_kind": "COST",
                "postage_amount_minor": 199,
                "postage_currency": "GBP",
            },
            None,
        ),
        (
            {
                "postage_kind": "COST",
                "postage_amount_minor": 0,
                "postage_currency": "EUR",
            },
            None,
        ),
        ({"postage_kind": "COST"}, "invalid_postage"),
        ({"postage_kind": "COST", "postage_amount_minor": 1}, "invalid_postage"),
        (
            {
                "postage_kind": "COST",
                "postage_amount_minor": -1,
                "postage_currency": "GBP",
            },
            "invalid_postage",
        ),
        (
            {
                "postage_kind": "COST",
                "postage_amount_minor": 1,
                "postage_currency": "gbp",
            },
            "invalid_postage",
        ),
        (
            {
                "postage_kind": "COST",
                "postage_amount_minor": 1,
                "postage_currency": "GB1",
            },
            "invalid_postage",
        ),
        (
            {
                "postage_kind": "COST",
                "postage_amount_minor": 1,
                "postage_currency": "GBPX",
            },
            "invalid_postage",
        ),
        ({"postage_kind": "FREE", "postage_amount_minor": 5}, "invalid_postage"),
        ({"postage_kind": "FREE", "postage_currency": "GBP"}, "invalid_postage"),
    ],
)
def test_postage_has_an_amount_and_currency_exactly_when_it_costs(
    values: dict[str, object], problem: str | None
) -> None:
    assert postage_problem(values) == problem


def test_the_service_wrapper_raises_invalid_input_with_the_same_code() -> None:
    with pytest.raises(InvalidInput) as raised:
        check_postage({"postage_kind": "COST"})
    assert raised.value.code == "invalid_postage"
    check_postage({"postage_kind": "FREE"})


@pytest.mark.parametrize(
    ("start", "end", "problem"),
    [
        (None, None, None),
        (NOW, None, None),
        (None, NOW, None),
        (NOW, NOW + timedelta(days=1), None),
        (NOW, NOW, "invalid_window"),
        (NOW, NOW - timedelta(days=1), "invalid_window"),
    ],
)
def test_the_window_ends_after_it_starts(
    start: datetime | None, end: datetime | None, problem: str | None
) -> None:
    assert window_problem(start, end) == problem
    if problem:
        with pytest.raises(InvalidInput):
            check_window(start, end)
    else:
        check_window(start, end)


def test_the_audited_fields_are_a_subset_of_the_editable_ones() -> None:
    assert AUDITED_FIELDS <= EDITABLE_FIELDS
