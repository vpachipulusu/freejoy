from datetime import timedelta

import pytest

from app.models.platform.offer import OfferStatus
from app.services.admin_catalogue_errors import IllegalTransition
from app.services.offer_lifecycle import (
    TRANSITIONS,
    OfferAction,
    PublishFacts,
    PublishReason,
    next_status,
    publish_blockers,
    require_editable,
)
from tests.fakes_admin_catalogue import EARLIER, LATER, NOW

S, A = OfferStatus, OfferAction
ALLOWED = {
    (S.DRAFT, A.PUBLISH, S.PUBLISHED),
    (S.DRAFT, A.ARCHIVE, S.ARCHIVED),
    (S.DRAFT, A.VERIFY, S.DRAFT),
    (S.PUBLISHED, A.UNPUBLISH, S.DRAFT),
    (S.PUBLISHED, A.EXPIRE, S.EXPIRED),
    (S.PUBLISHED, A.ARCHIVE, S.ARCHIVED),
    (S.PUBLISHED, A.VERIFY, S.PUBLISHED),
    (S.EXPIRED, A.PUBLISH, S.PUBLISHED),
    (S.EXPIRED, A.ARCHIVE, S.ARCHIVED),
    (S.EXPIRED, A.VERIFY, S.EXPIRED),
}


@pytest.mark.parametrize(("status", "action", "target"), sorted(ALLOWED))
def test_allowed_transitions_reach_the_documented_status(status, action, target):
    assert next_status(status, action) == target


@pytest.mark.parametrize(
    ("status", "action"),
    [
        (s, a)
        for s in OfferStatus
        for a in OfferAction
        if (s, a) not in {(x, y) for x, y, _ in ALLOWED}
    ],
)
def test_every_other_transition_is_illegal(status, action):
    with pytest.raises(IllegalTransition):
        next_status(status, action)


def test_the_table_has_exactly_the_documented_entries():
    assert {(s, a, t) for (s, a), t in TRANSITIONS.items()} == ALLOWED


def test_archived_is_terminal_and_not_editable():
    assert not any(s is S.ARCHIVED for (s, _a) in TRANSITIONS)
    with pytest.raises(IllegalTransition):
        require_editable(S.ARCHIVED)
    require_editable(S.PUBLISHED)


def ready(**changes):
    facts = {
        "category_enabled": True,
        "merchant_active": True,
        "active_links": 1,
        "has_source_url": False,
        "end_at": LATER,
        "last_verified_at": EARLIER,
    }
    return PublishFacts(**{**facts, **changes})


def test_a_complete_offer_has_no_blockers():
    assert publish_blockers(ready(), NOW) == []


def test_a_source_url_replaces_the_need_for_a_link_and_a_null_end_is_fine():
    facts = ready(active_links=0, has_source_url=True, end_at=None)

    assert publish_blockers(facts, NOW) == []


def test_every_unmet_rule_is_reported_together():
    facts = ready(
        category_enabled=False,
        merchant_active=False,
        active_links=0,
        end_at=NOW,
        last_verified_at=None,
    )

    assert publish_blockers(facts, NOW) == [
        PublishReason.CATEGORY_DISABLED,
        PublishReason.MERCHANT_INACTIVE,
        PublishReason.NO_DESTINATION,
        PublishReason.END_DATE_PASSED,
        PublishReason.NOT_VERIFIED,
    ]


def test_an_end_date_one_second_ahead_still_publishes():
    assert publish_blockers(ready(end_at=NOW + timedelta(seconds=1)), NOW) == []


def test_an_unconfirmed_basis_is_its_own_blocker_listed_last():
    facts = ready(last_verified_at=None, basis_unconfirmed=True)

    assert publish_blockers(facts, NOW) == [
        PublishReason.NOT_VERIFIED,
        PublishReason.COMMERCIAL_BASIS_REQUIRED,
    ]


def test_a_confirmed_basis_blocks_nothing():
    assert publish_blockers(ready(basis_unconfirmed=False), NOW) == []
