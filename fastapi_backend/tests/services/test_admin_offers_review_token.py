"""The review token: verify and publish fail when the offer changed after the editor loaded it."""

from dataclasses import replace
from datetime import timedelta

import pytest

from app.core.permissions import AdminContext
from app.models.platform.offer import OfferStatus
from app.repositories.admin_offers import ReviewRefs
from app.services.admin_catalogue_errors import (
    CatalogueNotFound,
    InvalidInput,
    OfferChanged,
)
from app.services.admin_offers import AdminOfferService
from app.services.offer_fingerprint import fingerprint, review_token
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
REFS = ReviewRefs(
    category_name="Home",
    category_enabled=True,
    merchant_name="Acme",
    merchant_website="https://www.acme.example",
    merchant_logo_url=None,
    merchant_active=True,
)


class OrderRecordingStore(FakeOfferStore):
    """Remembers the order of the reads, to show everything is read after the row lock."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[str] = []

    async def get(self, offer_id: str, *, lock: bool = False):
        self.calls.append("lock_offer" if lock else "get_offer")
        return await super().get(offer_id, lock=lock)

    async def list_links(self, offer_id: str):
        self.calls.append("list_links")
        return await super().list_links(offer_id)

    async def review_refs(self, category_id, merchant_id, *, share=False):
        self.calls.append("review_refs_share" if share else "review_refs")
        return await super().review_refs(category_id, merchant_id, share=share)


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


async def approve(service, step, token):
    if step == "publish":
        return await service.transition(
            ACTOR, "off1", OfferAction.PUBLISH, review_token=token
        )
    return await service.verify(ACTOR, "off1", None, review_token=token)


# --- the token as the admin read returns it ------------------------------------------------


async def test_the_detail_read_carries_the_token_of_the_offer_its_links_and_references():
    service, store, *_ = build(reviewable(), links=[link("l1")])

    detail = await service.get_offer("off1")

    assert detail.review_token == review_token(
        store.offers["off1"],
        [link("l1")],
        await store.review_refs("cat1", "mer1"),
    )


# --- publish and verify ----------------------------------------------------------------------


async def test_publish_with_the_token_it_loaded_succeeds():
    service, _, auditor, session = build(reviewable())
    token = await loaded_token(service)

    detail = await approve(service, "publish", token)

    assert detail.offer.status is OfferStatus.PUBLISHED
    assert [row["action"] for row in auditor.rows] == ["admin.offers.publish"]
    assert session.commits == 1


async def test_verify_with_the_token_it_loaded_succeeds():
    service, store, auditor, _ = build(offer("off1"))
    token = await loaded_token(service)

    detail = await approve(service, "verify", token)

    assert detail.offer.last_verified_at == NOW
    assert [row["action"] for row in auditor.rows] == ["admin.offers.verify"]
    assert store.locked == ["off1"]


@pytest.mark.parametrize("step", ["publish", "verify"])
async def test_a_stale_token_changes_nothing_and_writes_no_audit_row(step):
    service, store, auditor, session = build(reviewable())
    token = await loaded_token(service)
    store.offers["off1"] = replace(store.offers["off1"], title="Changed behind you")
    before = store.offers["off1"]

    with pytest.raises(OfferChanged) as raised:
        await approve(service, step, token)

    assert raised.value.code == "offer_changed"
    assert store.offers["off1"] == before
    assert auditor.rows == [] and session.commits == 0


@pytest.mark.parametrize("step", ["publish", "verify"])
async def test_a_missing_or_empty_token_is_refused_not_passed(step):
    service, store, auditor, session = build(reviewable())

    for token in (None, ""):
        with pytest.raises(InvalidInput) as raised:
            await approve(service, step, token)
        assert raised.value.code == "review_token_required"

    assert store.offers["off1"].status is OfferStatus.DRAFT
    assert store.offers["off1"].last_verified_at == EARLIER
    assert auditor.rows == [] and session.commits == 0


async def test_a_token_of_the_wrong_shape_is_just_stale():
    service, *_ = build(reviewable())

    with pytest.raises(OfferChanged):
        await approve(service, "publish", "not-a-token")


async def test_publish_of_a_missing_offer_is_not_found_before_the_token_matters():
    service, *_ = build()

    with pytest.raises(CatalogueNotFound):
        await service.transition(ACTOR, "ghost", OfferAction.PUBLISH, review_token="x")


@pytest.mark.parametrize("step", ["publish", "verify"])
async def test_a_link_changed_after_loading_makes_the_token_stale(step):
    service, store, auditor, _ = build(reviewable(), links=[link("l1")])
    token = await loaded_token(service)
    store.links["l1"] = replace(
        store.links["l1"], destination_url="https://shop.example.com/elsewhere"
    )

    with pytest.raises(OfferChanged):
        await approve(service, step, token)

    assert auditor.rows == []


async def test_a_link_added_after_loading_makes_the_token_stale():
    service, store, *_ = build(reviewable(), links=[link("l1")])
    token = await loaded_token(service)
    store.links["l2"] = link("l2")

    with pytest.raises(OfferChanged):
        await approve(service, "publish", token)


@pytest.mark.parametrize("step", ["publish", "verify"])
@pytest.mark.parametrize(
    "change",
    [
        ("websites", "mer1", "https://www.elsewhere.example"),
        ("merchant_names", "mer1", "Renamed Ltd"),
        ("merchant_logos", "mer1", "https://cdn.example.com/new.png"),
        ("merchants", "mer1", False),
        ("category_names", "cat1", "Renamed category"),
        ("categories", "cat1", False),
    ],
    ids=lambda c: c[0],
)
async def test_a_change_to_the_merchant_or_category_after_loading_makes_it_stale(
    step, change
):
    service, store, auditor, session = build(reviewable())
    token = await loaded_token(service)
    attribute, key, value = change
    getattr(store, attribute)[key] = value

    with pytest.raises(OfferChanged):
        await approve(service, step, token)

    assert auditor.rows == [] and session.commits == 0


@pytest.mark.parametrize("step", ["publish", "verify"])
async def test_the_token_is_compared_under_the_row_lock_after_it_is_taken(step):
    store = OrderRecordingStore()
    service, store, *_ = build(reviewable(), links=[link("l1")], store=store)
    token = await loaded_token(service)
    store.calls.clear()

    await approve(service, step, token)

    assert store.calls[:3] == ["lock_offer", "list_links", "review_refs_share"]


async def test_the_token_of_a_published_result_matches_what_the_next_read_returns():
    service, *_ = build(reviewable())
    token = await loaded_token(service)

    detail = await approve(service, "publish", token)

    assert detail.review_token == await loaded_token(service)
    assert detail.review_token != token


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


def token_of(offer_record=None, links=None, refs=REFS):
    return review_token(
        offer_record or reviewable(), [link("l1")] if links is None else links, refs
    )


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
    assert token_of(reviewable(**changes)) != token_of()


@pytest.mark.parametrize(
    "changes",
    [
        {"destination_url": "https://shop.example.com/other"},
        {"provider": "direct"},
        {"tracking_id": "t-9"},
        {"active": False},
    ],
)
def test_the_token_changes_when_any_link_detail_changes(changes):
    base = link("l1")

    assert token_of(links=[replace(base, **changes)]) != token_of(links=[base])


@pytest.mark.parametrize(
    "changes",
    [
        {"merchant_name": "Renamed"},
        {"merchant_website": "https://www.other.example"},
        {"merchant_website": None},
        {"merchant_logo_url": "https://cdn.example.com/logo.png"},
        {"merchant_active": False},
        {"category_name": "Renamed"},
        {"category_enabled": False},
    ],
)
def test_the_token_changes_when_the_merchant_or_the_category_changes(changes):
    assert token_of(refs=replace(REFS, **changes)) != token_of()


def test_swapping_the_destinations_of_two_links_changes_the_token():
    first = replace(link("l1"), destination_url="https://shop.example.com/a")
    second = replace(
        link("l2"),
        destination_url="https://shop.example.com/b",
        created_at=NOW + timedelta(minutes=1),
    )
    swapped = [
        replace(first, destination_url=second.destination_url),
        replace(second, destination_url=first.destination_url),
    ]

    assert token_of(links=swapped) != token_of(links=[first, second])


def test_the_order_links_are_listed_in_does_not_matter_only_their_click_order():
    first = replace(link("l1"), destination_url="https://shop.example.com/a")
    second = replace(
        link("l2"),
        destination_url="https://shop.example.com/b",
        created_at=NOW + timedelta(minutes=1),
    )

    assert token_of(links=[second, first]) == token_of(links=[first, second])


def test_reordering_links_by_creation_time_changes_the_token():
    first = link("l1")
    second = replace(link("l2"), created_at=NOW + timedelta(minutes=1))
    reordered = [replace(first, created_at=NOW + timedelta(minutes=2)), second]

    assert token_of(links=reordered) != token_of(links=[first, second])


def test_the_token_ignores_bookkeeping_that_a_reviewer_does_not_review():
    base = token_of()

    touched = token_of(
        reviewable(updated_at=NOW + timedelta(days=9), created_at=NOW),
        [replace(link("l1"), updated_at=NOW + timedelta(days=9))],
    )

    assert touched == base


def test_the_token_is_not_the_importers_stored_fingerprint():
    assert token_of() != fingerprint(reviewable(), [link("l1")])


def test_the_token_is_opaque_hex_and_reveals_no_text_of_the_offer():
    token = token_of(reviewable(title="Secretive title"))

    assert len(token) == 64 and int(token, 16) >= 0
    assert "Secretive" not in token
