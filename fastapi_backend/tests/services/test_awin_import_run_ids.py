"""A promotion id is logged only when it is a sane integer (FREEJ-52)."""

import logging

import pytest

from app.services.awin_import_run import RunOptions, _raw_id, run_import
from app.services.awin_promotions import MAX_ID
from tests.awin_samples import NOW, PUBLISHER_ID, raw_item
from tests.fakes_awin import FakeAwinClient


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1, 1),
        (MAX_ID, MAX_ID),
        (0, None),
        (-5, None),
        (MAX_ID + 1, None),
        (10**400, None),
        (True, None),
        ("7", None),
        (None, None),
    ],
)
def test_only_a_positive_id_in_range_is_returned(
    value: object, expected: int | None
) -> None:
    assert _raw_id({"promotionId": value}) == expected


def test_a_non_object_has_no_id() -> None:
    assert _raw_id("x") is None and _raw_id(None) is None


async def test_an_enormous_id_is_rejected_as_invalid_and_never_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = FakeAwinClient([[raw_item(1, promotionId=10**400), raw_item(2)]])
    options = RunOptions(publisher_id=PUBLISHER_ID, dry_run=True)

    with caplog.at_level(logging.WARNING, logger="freejoy.awin_import"):
        report = await run_import(client, None, options, NOW)

    assert report.valid == 1 and report.tally.skipped == {"invalid_id": 1}
    assert "promotion_id=- reason=invalid_id" in caplog.text
    assert "Logging error" not in caplog.text
