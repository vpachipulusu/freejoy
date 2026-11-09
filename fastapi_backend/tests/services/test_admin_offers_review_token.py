"""The review token: verify and publish fail when the offer changed after the editor loaded it."""

from dataclasses import replace
from datetime import timedelta

import pytest

from app.core.permissions import AdminContext
from app.models.platform.offer import OfferStatus
from app.repositories.admin_offers import OfferFilters
from app.services.admin_catalogue_errors import (
    CatalogueNotFound,
    InvalidInput,
    OfferChanged,
)
from app.services.admin_offers import AdminOfferService
from app.services.offer_fingerprint import review_token
from app.services.offer_lifecycle import OfferAction
from tests.fakes_admin_catalogue import (
    EARLIER,
    NOW,
    FakeAuditor,
    FakeOfferStore,
    FakeSession,
    link,
    offer,
)

ACTOR = AdminContext("admin1", "admin", frozenset({"offers.publish"}))


class OrderRecordingStore(FakeOfferStore):
    """Remembers the order of the reads, to show the links are read after the row lock."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[str] = []

    async def get(self, offer_id: str, *, lock: bool = False):
        self.calls.append("lock_offer" if lock else "get_offer")
        return await super().get(offer_id, lock=lock)

    async def list_links(self, offer_id: str):
        self.calls.append("list_links")
        return await super().list_links(offer_id)


def build(*offers, links=(), store=None):
    store, auditor, session = store or FakeOfferStore(), FakeAuditor(), FakeSession()
    for item in offers:
        store.offers[item.id] = item
    for item in links:
        store.links[item.id] = item
    service = AdminOfferService(store, session, auditor, clock=lambda: NOW)
    return service, store, auditor, session


def reviewable(**changes):
    base = {"last_verified_at": EARLIER, "source_url": "https://shop.example.com"}
    return offer("off1", **{**base, **changes})


async def loaded_token(service, offer_id="off1"):
    return (await service.get_offer(offer_id)).review_token


# --- the token as the admin read returns it ------------------------------------------------


async def test_the_detail_read_carries_the_token_of_the_offer_and_its_links():
    service, store, *_ = build(reviewable(), links=[link("l1")])

    detail = await service.get_offer("off1")

    assert detail.review_token == review_token(store.offers["off1"], [link("l1")])


async def test_every_offer_in_a_list_carries_its_own_token_with_its_own_links():
    service, _, *_ = build(
        reviewable(),
        offer("off2", title="Second"),
        links=[link("l1"), link("l2", "off2")],
    )

    listing = await service.list_offers(OfferFilters(), 20, 0)

    tokens = {item.offer.id: item.review_token for item in listing.offers}
    assert tokens["off1"] == await loaded_token(service, "off1")
    assert tokens["off2"] == await loaded_token(service, "off2")
    assert tokens["off1"] != tokens["off2"]
    assert listing.total_count == 2


# --- publish ---------------------------------------------------------------------------------


async def test_publish_with_the_token_it_loaded_succeeds():
    service, _, auditor, session = build(reviewable())
    token = await loaded_token(service)

    detail = await service.transition(
        ACTOR, "off1", OfferAction.PUBLISH, review_token=token
    )

    assert detail.offer.status is OfferStatus.PUBLISHED
    assert [row["action"] for row in auditor.rows] == ["admin.offers.publish"]
    assert session.commits == 1


async def test_publish_with_a_stale_token_changes_nothing_and_writes_no_audit_row():
    service, store, auditor, session = build(reviewable())
    token = await loaded_token(service)
    store.offers["off1"] = replace(store.offers["off1"], title="Changed behind you")
    before = store.offers["off1"]

    with pytest.raises(OfferChanged) as raised:
        await service.transition(ACTOR, "off1", OfferAction.PUBLISH, review_token=token)

    assert raised.value.code == "offer_changed"
    assert store.offers["off1"] == before
    assert before.status is OfferStatus.DRAFT
    assert auditor.rows == [] and session.commits == 0


async def test_publish_without_a_token_is_refused_not_passed():
    service, store, auditor, session = build(reviewable())

    with pytest.raises(InvalidInput) as raised:
        await service.transition(ACTOR, "off1", OfferAction.PUBLISH)

    assert raised.value.code == "review_token_required"
    assert store.offers["off1"].status is OfferStatus.DRAFT
    assert auditor.rows == [] and session.commits == 0


async def test_an_empty_token_counts_as_missing():
    service, *_ = build(reviewable())

    with pytest.raises(InvalidInput):
        await service.transition(ACTOR, "off1", OfferAction.PUBLISH, review_token="")


async def test_a_token_of_the_wrong_shape_is_just_stale():
    service, *_ = build(reviewable())

    with pytest.raises(OfferChanged):
        await service.transition(
            ACTOR, "off1", OfferAction.PUBLISH, review_token="not-a-token"
        )


async def test_publish_of_a_missing_offer_is_not_found_before_the_token_matters():
    service, *_ = build()

    with pytest.raises(CatalogueNotFound):
        await service.transition(ACTOR, "ghost", OfferAction.PUBLISH, review_token="x")


async def test_a_link_changed_after_loading_makes_the_token_stale():
    service, store, auditor, _ = build(reviewable(), links=[link("l1")])
    token = await loaded_token(service)
    store.links["l1"] = replace(
        store.links["l1"], destination_url="https://shop.example.com/elsewhere"
    )

    with pytest.raises(OfferChanged):
        await service.transition(ACTOR, "off1", OfferAction.PUBLISH, review_token=token)

    assert auditor.rows == []


async def test_a_link_added_after_loading_makes_the_token_stale():
    service, store, *_ = build(reviewable(), links=[link("l1")])
    token = await loaded_token(service)
    store.links["l2"] = link("l2")

    with pytest.raises(OfferChanged):
        await service.transition(ACTOR, "off1", OfferAction.PUBLISH, review_token=token)


async def test_the_token_is_computed_under_the_row_lock_after_it_is_taken():
    store = OrderRecordingStore()
    service, store, *_ = build(reviewable(), links=[link("l1")], store=store)
    token = await loaded_token(service)
    store.calls.clear()

    await service.transition(ACTOR, "off1", OfferAction.PUBLISH, review_token=token)

    assert store.calls[:2] == ["lock_offer", "list_links"]


async def test_the_token_of_a_published_result_matches_what_the_next_read_returns():
    service, *_ = build(reviewable())
    token = await loaded_token(service)

    detail = await service.transition(
        ACTOR, "off1", OfferAction.PUBLISH, review_token=token
    )

    assert detail.review_token == await loaded_token(service)
    assert detail.review_token != token


# --- verify ----------------------------------------------------------------------------------


async def test_verify_with_the_token_it_loaded_succeeds():
    service, _, auditor, _ = build(offer("off1"))
    token = await loaded_token(service)

    detail = await service.verify(ACTOR, "off1", None, review_token=token)

    assert detail.offer.last_verified_at == NOW
    assert [row["action"] for row in auditor.rows] == ["admin.offers.verify"]


async def test_verify_with_a_stale_token_changes_nothing_and_writes_no_audit_row():
    service, store, auditor, session = build(offer("off1"))
    token = await loaded_token(service)
    store.offers["off1"] = replace(store.offers["off1"], summary="Rewritten")

    with pytest.raises(OfferChanged):
        await service.verify(ACTOR, "off1", "note", review_token=token)

    assert store.offers["off1"].last_verified_at is None
    assert auditor.rows == [] and session.commits == 0


async def test_verify_without_a_token_is_refused():
    service, store, auditor, _ = build(offer("off1"))

    with pytest.raises(InvalidInput) as raised:
        await service.verify(ACTOR, "off1", None, review_token=None)

    assert raised.value.code == "review_token_required"
    assert store.offers["off1"].last_verified_at is None and auditor.rows == []


# --- the transitions that take content off the public site do not need it ---------------------


@pytest.mark.parametrize(
    ("status", "action"),
    [
        (OfferStatus.PUBLISHED, OfferAction.UNPUBLISH),
        (OfferStatus.PUBLISHED, OfferAction.EXPIRE),
        (OfferStatus.PUBLISHED, OfferAction.ARCHIVE),
        (OfferStatus.DRAFT, OfferAction.ARCHIVE),
    ],
)
async def test_unpublish_expire_and_archive_work_without_a_token(status, action):
    service, store, *_ = build(reviewable(status=status))

    await service.transition(ACTOR, "off1", action)

    assert store.offers["off1"].status is not status


# --- what the token does and does not depend on ----------------------------------------------


@pytest.mark.parametrize(
    "changes",
    [
        {"title": "New title"},
        {"summary": "New summary"},
        {"description": ["New body"]},
        {"eligibility": "Over 18 only"},
        {"subscription_terms": "Then 9.99 a month"},
        {"end_at": NOW + timedelta(days=3)},
        {"start_at": NOW - timedelta(days=3)},
        {"source_url": "https://shop.example.com/else"},
        {"requires_card": True},
        {"last_verified_at": NOW},
        {"status": OfferStatus.PUBLISHED},
    ],
)
def test_the_token_changes_when_reviewable_content_changes(changes):
    before = review_token(reviewable(), [link("l1")])

    after = review_token(reviewable(**changes), [link("l1")])

    assert after != before


@pytest.mark.parametrize(
    "changes",
    [
        {"destination_url": "https://shop.example.com/other"},
        {"provider": "direct"},
        {"tracking_id": "t-9"},
        {"active": False},
    ],
)
def test_the_token_changes_when_any_link_destination_detail_changes(changes):
    base = [link("l1")]

    assert review_token(reviewable(), [replace(base[0], **changes)]) != review_token(
        reviewable(), base
    )


def test_the_token_ignores_bookkeeping_that_a_reviewer_does_not_review():
    base = review_token(reviewable(), [link("l1")])

    touched = review_token(
        reviewable(updated_at=NOW + timedelta(days=9), created_at=NOW),
        [replace(link("l1"), updated_at=NOW + timedelta(days=9))],
    )

    assert touched == base


def test_the_token_is_opaque_hex_and_reveals_no_text_of_the_offer():
    token = review_token(reviewable(title="Secretive title"), [link("l1")])

    assert len(token) == 64 and int(token, 16) >= 0
    assert "Secretive" not in token
