"""The partner-facing status is derived from the offer, one pure function, every combination written out (FREEJ-58)."""

import itertools

import pytest

from app.core.offer_vocabulary import OFFER_STATUSES
from app.partner.statuses import (
    DECLINED,
    ENDED,
    LIVE,
    PARTNER_STATUSES,
    PENDING_REVIEW,
    partner_status,
)

# (offer status, past its end date, ever published) -> what the retailer sees
TABLE = {
    ("DRAFT", False, False): PENDING_REVIEW,
    ("DRAFT", False, True): PENDING_REVIEW,  # unpublished by an editor: back with them
    ("DRAFT", True, False): ENDED,  # the retailer ended a draft
    ("DRAFT", True, True): ENDED,
    ("PUBLISHED", False, False): LIVE,
    ("PUBLISHED", False, True): LIVE,
    (
        "PUBLISHED",
        True,
        False,
    ): ENDED,  # the public site already treats a past end date as ended
    ("PUBLISHED", True, True): ENDED,
    ("EXPIRED", False, False): ENDED,
    ("EXPIRED", False, True): ENDED,
    ("EXPIRED", True, False): ENDED,
    ("EXPIRED", True, True): ENDED,
    ("ARCHIVED", False, False): DECLINED,  # an editor turned it down
    ("ARCHIVED", False, True): ENDED,  # an editor retired a live offer
    ("ARCHIVED", True, False): DECLINED,
    ("ARCHIVED", True, True): ENDED,
}


def test_the_table_covers_every_offer_status_and_both_flags() -> None:
    assert set(TABLE) == set(
        itertools.product(OFFER_STATUSES, (False, True), (False, True))
    )


@pytest.mark.parametrize(("key", "expected"), sorted(TABLE.items()))
def test_every_combination_maps_to_the_documented_status(
    key: tuple[str, bool, bool], expected: str
) -> None:
    status, past_end, ever_published = key

    assert (
        partner_status(
            offer_status=status, past_end=past_end, ever_published=ever_published
        )
        == expected
    )


def test_the_four_statuses_are_the_only_ones_ever_produced() -> None:
    assert set(TABLE.values()) == set(PARTNER_STATUSES)


def test_an_unknown_offer_status_is_a_bug_and_raises() -> None:
    with pytest.raises(ValueError, match="unknown offer status"):
        partner_status(offer_status="DELETED", past_end=False, ever_published=False)
