"""The shared rule for "an untouched draft": the Awin importer and the partner API both ask it."""

import pytest

from app.core.offer_fingerprint import is_untouched_draft

SAME = "a" * 64
OTHER = "b" * 64


def untouched(
    status: str = "DRAFT",
    links: int = 1,
    now: str = SAME,
    applied: str = SAME,
) -> bool:
    return is_untouched_draft(
        status=status,
        link_count=links,
        current_fingerprint=now,
        applied_fingerprint=applied,
    )


def test_a_draft_with_its_one_link_and_an_unchanged_fingerprint_is_untouched() -> None:
    assert untouched() is True


@pytest.mark.parametrize("status", ["PUBLISHED", "EXPIRED", "ARCHIVED"])
def test_only_a_draft_can_be_untouched(status: str) -> None:
    assert untouched(status=status) is False


@pytest.mark.parametrize("links", [0, 2, 3])
def test_a_draft_with_a_different_number_of_links_than_the_writer_made_is_touched(
    links: int,
) -> None:
    assert untouched(links=links) is False


def test_a_changed_fingerprint_means_a_person_changed_it() -> None:
    assert untouched(now=OTHER) is False
