"""Dismissing a source change: the review queue's one write, with the same token rule as verify."""

from dataclasses import replace
from datetime import timedelta

import pytest

from app.core.permissions import AdminContext
from app.models.platform.offer import OfferStatus
from app.repositories.admin_offers import OfferFilters
from app.services.admin_catalogue_errors import (
    CatalogueNotFound,
    IllegalTransition,
    InvalidInput,
    OfferChanged,
)
from app.services.admin_offers import AdminOfferService
from tests.fakes_admin_catalogue import (
    NOW,
    FakeAuditor,
    FakeOfferStore,
    FakeSession,
    offer,
    provenance,
)

ACTOR = AdminContext("admin1", "admin", frozenset({"offers.publish"}))
MARKED_AT = NOW - timedelta(hours=3)


def build(item=None, marker=MARKED_AT, *, imported=True):
    store, auditor, session = FakeOfferStore(), FakeAuditor(), FakeSession()
    item = item or offer("off1")
    store.offers[item.id] = item
    if imported:
        store.provenance[item.id] = provenance(source_changed_at=marker)
    service = AdminOfferService(store, session, auditor, clock=lambda: NOW)
    return service, store, auditor, session


async def loaded_token(service):
    return (await service.get_offer("off1")).review_token


async def test_dismissing_clears_the_marker_and_audits_it_once():
    service, store, auditor, session = build()
    token = await loaded_token(service)

    detail = await service.dismiss_source_change(ACTOR, "off1", token)

    assert store.provenance["off1"].source_changed_at is None
    assert detail.provenance is not None and detail.provenance.source_changed_at is None
    assert auditor.rows == [
        {
            "actor": "admin1",
            "action": "admin.offers.source_change_dismissed",
            "resource": "offers",
            "resource_id": "off1",
            "before": {"source_changed_at": MARKED_AT},
            "after": {"source_changed_at": None},
            "details": {"provider": "awin", "external_id": "1001"},
        }
    ]
    assert session.commits == 1


async def test_dismissing_locks_the_offer_then_its_provenance_and_leaves_the_offer_alone():
    service, store, *_ = build()
    before = store.offers["off1"]
    token = await loaded_token(service)

    await service.dismiss_source_change(ACTOR, "off1", token)

    assert store.locked == ["off1"] and store.provenance_locked == ["off1"]
    assert store.offers["off1"] == before


async def test_the_response_carries_the_token_it_replaced_and_the_same_new_one():
    service, *_ = build()
    token = await loaded_token(service)

    detail = await service.dismiss_source_change(ACTOR, "off1", token)

    # The offer's content did not change, so the token stays what the editor holds.
    assert detail.previous_review_token == token
    assert detail.review_token == token


async def test_a_stale_token_changes_nothing_and_writes_no_audit_row():
    service, store, auditor, session = build()
    token = await loaded_token(service)
    store.offers["off1"] = replace(store.offers["off1"], title="Changed behind you")

    with pytest.raises(OfferChanged) as raised:
        await service.dismiss_source_change(ACTOR, "off1", token)

    assert raised.value.code == "offer_changed"
    assert store.provenance["off1"].source_changed_at == MARKED_AT
    assert auditor.rows == [] and session.commits == 0


@pytest.mark.parametrize("token", [None, ""])
async def test_a_missing_token_is_refused_before_anything_is_written(token):
    service, store, auditor, session = build()

    with pytest.raises(InvalidInput) as raised:
        await service.dismiss_source_change(ACTOR, "off1", token)

    assert raised.value.code == "review_token_required"
    assert store.provenance["off1"].source_changed_at == MARKED_AT
    assert auditor.rows == [] and session.commits == 0


async def test_an_offer_with_no_provenance_has_nothing_to_dismiss():
    service, _, auditor, session = build(imported=False)
    token = await loaded_token(service)

    with pytest.raises(IllegalTransition) as raised:
        await service.dismiss_source_change(ACTOR, "off1", token)

    assert raised.value.code == "illegal_transition"
    assert auditor.rows == [] and session.commits == 0


async def test_a_marker_that_is_already_clear_has_nothing_to_dismiss():
    service, _, auditor, session = build(marker=None)
    token = await loaded_token(service)

    with pytest.raises(IllegalTransition):
        await service.dismiss_source_change(ACTOR, "off1", token)

    assert auditor.rows == [] and session.commits == 0


async def test_dismissing_twice_is_refused_the_second_time():
    service, _, auditor, _ = build()
    token = await loaded_token(service)
    await service.dismiss_source_change(ACTOR, "off1", token)

    with pytest.raises(IllegalTransition):
        await service.dismiss_source_change(ACTOR, "off1", token)

    assert len(auditor.rows) == 1


async def test_an_archived_offer_cannot_be_acted_on():
    service, store, auditor, session = build(offer("off1", status=OfferStatus.ARCHIVED))
    token = await loaded_token(service)

    with pytest.raises(IllegalTransition):
        await service.dismiss_source_change(ACTOR, "off1", token)

    assert store.provenance["off1"].source_changed_at == MARKED_AT
    assert auditor.rows == [] and session.commits == 0


async def test_an_unknown_offer_is_not_found():
    service, *_ = build()

    with pytest.raises(CatalogueNotFound):
        await service.dismiss_source_change(ACTOR, "ghost", "token")


@pytest.mark.parametrize("status", [OfferStatus.DRAFT, OfferStatus.PUBLISHED])
async def test_a_published_or_draft_offer_can_be_dismissed(status):
    service, store, *_ = build(offer("off1", status=status, last_verified_at=NOW))
    token = await loaded_token(service)

    await service.dismiss_source_change(ACTOR, "off1", token)

    assert store.provenance["off1"].source_changed_at is None
    assert store.offers["off1"].status is status


# --- what the reads carry -------------------------------------------------------------------


async def test_the_detail_read_carries_the_provenance_of_an_imported_offer():
    service, *_ = build()

    detail = await service.get_offer("off1")

    assert detail.provenance == provenance(source_changed_at=MARKED_AT)


async def test_the_detail_read_of_a_hand_made_offer_has_no_provenance():
    service, *_ = build(imported=False)

    assert (await service.get_offer("off1")).provenance is None


async def test_the_list_carries_provenance_for_imported_rows_only():
    service, store, *_ = build()
    store.offers["off2"] = offer("off2")

    page = await service.list_offers(OfferFilters(), 20, 0)

    assert set(page.provenance) == {"off1"}
