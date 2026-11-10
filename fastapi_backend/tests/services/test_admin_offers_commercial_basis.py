"""The commercial basis of an offer in the admin service: stored, audited, reviewed and gating publish."""

import pytest

from app.core.permissions import AdminContext
from app.models.platform.offer import (
    CommercialBasis,
    OfferStatus,
    OfferType,
    PostageKind,
)
from app.services.admin_catalogue_errors import (
    OfferChanged,
    PermissionDenied,
    PublishBlocked,
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
from tests.services.test_admin_offers_review_token import REFS

ACTOR = AdminContext("admin1", "admin", frozenset({"offers.write"}))
PUBLISHER = AdminContext(
    "admin2", "admin", frozenset({"offers.write", "offers.publish"})
)
ALL_BASES = list(CommercialBasis)


def build(*offers, links=()):
    store, auditor, session = FakeOfferStore(), FakeAuditor(), FakeSession()
    for item in offers:
        store.offers[item.id] = item
    for item in links:
        store.links[item.id] = item
    service = AdminOfferService(store, session, auditor, clock=lambda: NOW)
    return service, store, auditor, session


def new_data(**changes):
    data = {
        "title": "Free Sample Box",
        "category_id": "cat1",
        "merchant_id": "mer1",
        "offer_type": OfferType.FREE_SAMPLE,
        "commercial_basis": CommercialBasis.EDITORIAL,
        "summary": "A box",
        "description": [],
        "featured": False,
        "image_tint": "sage",
        "image_label": "Free",
        "image_alt": "Alt",
        "source_url": None,
        "postage_kind": PostageKind.FREE,
        "postage_amount_minor": None,
        "postage_currency": None,
        "requires_card": False,
        "subscription_terms": None,
        "eligibility": "UK",
        "availability": "Today",
        "steps": [],
        "start_at": None,
        "end_at": None,
        "freshness": None,
        "publisher_notes": None,
    }
    return {**data, **changes}


async def token(service, offer_id="off1"):
    return (await service.get_offer(offer_id)).review_token


def ready(**changes):
    return offer(last_verified_at=EARLIER, **changes)


async def publish(service, **extra):
    return await service.transition(
        ACTOR, "off1", OfferAction.PUBLISH, await token(service), **extra
    )


@pytest.mark.parametrize("basis", ALL_BASES)
async def test_create_stores_the_basis_and_audits_it(basis):
    service, _store, auditor, _session = build()

    detail = await service.create_offer(ACTOR, new_data(commercial_basis=basis))

    assert detail.offer.commercial_basis is basis
    (row,) = auditor.rows
    assert row["after"]["commercial_basis"] == basis.value


async def test_update_changes_the_basis_and_audits_before_and_after():
    service, _store, auditor, _session = build(offer("off1"))

    detail = await service.update_offer(
        ACTOR,
        "off1",
        {"commercial_basis": CommercialBasis.SPONSORED},
        await token(service),
    )

    assert detail.offer.commercial_basis is CommercialBasis.SPONSORED
    (row,) = auditor.rows
    assert row["before"] == {"commercial_basis": "EDITORIAL"}
    assert row["after"] == {"commercial_basis": "SPONSORED"}
    assert row["details"] == {"fields": ["commercial_basis"]}


async def test_a_sponsored_offer_can_be_featured_and_stays_sponsored():
    service, *_ = build(offer("off1"))

    detail = await service.update_offer(
        ACTOR,
        "off1",
        {"commercial_basis": CommercialBasis.SPONSORED, "featured": True},
        await token(service),
    )

    assert detail.offer.featured is True
    assert detail.offer.commercial_basis is CommercialBasis.SPONSORED


async def test_create_accepts_a_sponsored_featured_offer():
    service, *_ = build()

    detail = await service.create_offer(
        ACTOR, new_data(commercial_basis=CommercialBasis.SPONSORED, featured=True)
    )

    assert (detail.offer.featured, detail.offer.commercial_basis) == (
        True,
        CommercialBasis.SPONSORED,
    )


async def test_a_published_offer_can_change_its_basis_for_an_editor_who_may_publish():
    service, *_ = build(
        ready(
            id="off1",
            status=OfferStatus.PUBLISHED,
            source_url="https://shop.example.com",
        )
    )

    detail = await service.update_offer(
        PUBLISHER,
        "off1",
        {"commercial_basis": CommercialBasis.SPONSORED},
        await token(service),
    )

    assert detail.offer.commercial_basis is CommercialBasis.SPONSORED


async def test_a_writer_without_publish_cannot_change_the_basis_of_a_published_offer():
    service, store, auditor, session = build(
        ready(
            id="off1",
            status=OfferStatus.PUBLISHED,
            source_url="https://shop.example.com",
            commercial_basis=CommercialBasis.SPONSORED,
        )
    )

    with pytest.raises(PermissionDenied):
        await service.update_offer(
            ACTOR,
            "off1",
            {"commercial_basis": CommercialBasis.EDITORIAL},
            await token(service),
        )

    assert store.offers["off1"].commercial_basis is CommercialBasis.SPONSORED
    assert auditor.rows == [] and session.commits == 0


async def test_the_same_change_on_a_draft_needs_only_the_write_permission():
    service, *_ = build(offer("off1", commercial_basis=CommercialBasis.SPONSORED))

    detail = await service.update_offer(
        ACTOR,
        "off1",
        {"commercial_basis": CommercialBasis.EDITORIAL},
        await token(service),
    )

    assert detail.offer.commercial_basis is CommercialBasis.EDITORIAL


async def test_a_writer_may_still_edit_other_fields_of_a_published_offer():
    service, *_ = build(
        ready(
            id="off1",
            status=OfferStatus.PUBLISHED,
            source_url="https://shop.example.com",
        )
    )

    detail = await service.update_offer(ACTOR, "off1", {"summary": "New"})

    assert detail.offer.summary == "New"


@pytest.mark.parametrize("sent", ["missing", "stale"])
async def test_a_basis_change_without_the_current_token_is_a_changed_offer(sent):
    service, store, auditor, session = build(
        offer("off1", commercial_basis=CommercialBasis.SPONSORED)
    )
    stale = await token(service)
    await service.update_offer(ACTOR, "off1", {"summary": "Edited meanwhile"})
    auditor.rows.clear()
    session.commits = 0

    with pytest.raises(OfferChanged):
        await service.update_offer(
            ACTOR,
            "off1",
            {"commercial_basis": CommercialBasis.EDITORIAL},
            None if sent == "missing" else stale,
        )

    assert store.offers["off1"].commercial_basis is CommercialBasis.SPONSORED
    assert auditor.rows == [] and session.commits == 0


async def test_an_edit_that_does_not_change_the_basis_needs_no_token():
    service, *_ = build(offer("off1", commercial_basis=CommercialBasis.SPONSORED))

    detail = await service.update_offer(
        ACTOR,
        "off1",
        {"summary": "Edited", "commercial_basis": CommercialBasis.SPONSORED},
    )

    assert detail.offer.summary == "Edited"
    assert detail.offer.commercial_basis is CommercialBasis.SPONSORED


async def test_the_review_token_changes_when_only_the_basis_changes():
    service, *_ = build(offer("off1"))
    before = await token(service)

    await service.update_offer(
        ACTOR, "off1", {"commercial_basis": CommercialBasis.AFFILIATE}, before
    )

    assert await token(service) != before


async def test_a_stale_token_after_a_basis_change_is_refused_on_publish():
    service, *_ = build(ready(id="off1", source_url="https://shop.example.com"))
    loaded = await token(service)
    await service.update_offer(
        ACTOR, "off1", {"commercial_basis": CommercialBasis.SPONSORED}, loaded
    )

    with pytest.raises(OfferChanged):
        await service.transition(ACTOR, "off1", OfferAction.PUBLISH, loaded)


def test_review_token_differs_for_each_basis():
    tokens = {
        review_token(offer(commercial_basis=basis), [], REFS) for basis in ALL_BASES
    }

    assert len(tokens) == len(ALL_BASES)


async def test_publishing_with_only_direct_links_needs_an_explicit_basis():
    service, store, auditor, session = build(
        ready(id="off1"), links=[link("l1", provider="direct")]
    )

    with pytest.raises(PublishBlocked) as raised:
        await publish(service)

    assert raised.value.reasons == ["commercial_basis_required"]
    assert store.offers["off1"].status is OfferStatus.DRAFT
    assert auditor.rows == [] and session.commits == 0


@pytest.mark.parametrize("basis", ALL_BASES)
async def test_publishing_with_only_direct_links_passes_once_the_basis_is_confirmed(
    basis,
):
    service, *_ = build(
        ready(id="off1", commercial_basis=basis),
        links=[link("l1", provider="direct"), link("l2", provider="direct")],
    )

    detail = await publish(service, commercial_basis=basis)

    assert detail.offer.status is OfferStatus.PUBLISHED
    assert detail.offer.commercial_basis is basis


async def test_a_confirmed_basis_other_than_the_stored_one_is_a_changed_offer():
    service, store, auditor, _session = build(
        ready(id="off1", commercial_basis=CommercialBasis.EDITORIAL),
        links=[link("l1", provider="direct")],
    )

    with pytest.raises(OfferChanged):
        await publish(service, commercial_basis=CommercialBasis.SPONSORED)

    assert store.offers["off1"].status is OfferStatus.DRAFT
    assert auditor.rows == []


async def test_inactive_direct_links_do_not_count_towards_the_rule():
    service, *_ = build(
        ready(id="off1"),
        links=[
            link("l1", provider="direct"),
            link("l2", provider="awin"),
            link("l3", provider="direct", active=False),
        ],
    )

    detail = await publish(service)

    assert detail.offer.status is OfferStatus.PUBLISHED


@pytest.mark.parametrize(
    "links",
    [
        [link("l1", provider="awin")],
        [link("l1", provider="awin"), link("l2", provider="direct")],
        [link("l1", provider="direct", active=False), link("l2", provider="awin")],
        [link("l1", provider="cj"), link("l2", provider="sample")],
    ],
    ids=[
        "network",
        "network and direct",
        "only the network is active",
        "network and sample",
    ],
)
async def test_publishing_with_an_active_network_link_needs_no_confirmation(links):
    service, *_ = build(ready(id="off1"), links=links)

    detail = await publish(service)

    assert detail.offer.status is OfferStatus.PUBLISHED


@pytest.mark.parametrize(
    "links",
    [
        [link("l1", provider="direct")],
        [link("l1", provider="sample")],
        [link("l1", provider="direct"), link("l2", provider="sample")],
        [link("l1", provider="direct"), link("l2", provider="somenewnet")],
        [link("l1", provider="awin", active=False), link("l2", provider="direct")],
    ],
    ids=[
        "direct",
        "sample",
        "direct and sample",
        "direct and unknown",
        "network inactive",
    ],
)
async def test_publishing_with_no_active_network_link_needs_the_confirmation(links):
    service, *_ = build(ready(id="off1"), links=links)

    with pytest.raises(PublishBlocked) as raised:
        await publish(service)

    assert raised.value.reasons == ["commercial_basis_required"]


async def test_publishing_on_a_source_url_alone_needs_no_confirmation():
    service, *_ = build(ready(id="off1", source_url="https://shop.example.com"))

    detail = await publish(service)

    assert detail.offer.status is OfferStatus.PUBLISHED


async def test_republishing_an_expired_direct_only_offer_needs_the_basis_again():
    service, *_ = build(
        ready(id="off1", status=OfferStatus.EXPIRED),
        links=[link("l1", provider="direct")],
    )

    with pytest.raises(PublishBlocked) as raised:
        await publish(service)

    assert raised.value.reasons == ["commercial_basis_required"]


async def test_the_confirmation_is_reported_with_the_other_publish_blockers():
    service, store, *_ = build(offer("off1"), links=[link("l1", provider="direct")])
    store.categories["cat1"] = False

    with pytest.raises(PublishBlocked) as raised:
        await publish(service)

    assert raised.value.reasons == [
        "category_disabled",
        "not_verified",
        "commercial_basis_required",
    ]


async def test_a_basis_sent_with_an_offer_that_needs_none_is_still_checked():
    service, *_ = build(ready(id="off1", source_url="https://shop.example.com"))

    with pytest.raises(OfferChanged):
        await publish(service, commercial_basis=CommercialBasis.SPONSORED)


def published(**changes):
    return ready(
        id="off1",
        status=OfferStatus.PUBLISHED,
        commercial_basis=CommercialBasis.AFFILIATE,
        **changes,
    )


async def test_deactivating_the_network_link_of_a_published_offer_is_refused():
    service, store, auditor, session = build(
        published(), links=[link("l1", provider="awin"), link("l2", provider="direct")]
    )

    with pytest.raises(PublishBlocked) as raised:
        await service.update_link(ACTOR, "off1", "l1", {"active": False})

    assert raised.value.reasons == ["commercial_basis_required"]
    assert store.links["l1"].active is True
    assert auditor.rows == [] and session.commits == 0
    assert store.offers["off1"].status is OfferStatus.PUBLISHED


async def test_switching_the_network_link_of_a_published_offer_to_direct_is_refused():
    service, store, *_ = build(published(), links=[link("l1", provider="awin")])

    with pytest.raises(PublishBlocked):
        await service.update_link(
            ACTOR,
            "off1",
            "l1",
            {"provider": "direct", "destination_url": "https://www.acme.example/x"},
        )

    assert store.links["l1"].provider == "awin"


async def test_adding_the_first_direct_link_to_a_published_source_url_only_offer_is_refused():
    service, store, auditor, _session = build(
        published(source_url="https://shop.example.com")
    )

    with pytest.raises(PublishBlocked) as raised:
        await service.create_link(
            ACTOR,
            "off1",
            {"provider": "direct", "destination_url": "https://www.acme.example/x"},
        )

    assert raised.value.reasons == ["commercial_basis_required"]
    assert store.links == {} and auditor.rows == []


async def test_adding_a_second_network_link_to_a_published_offer_stays_allowed():
    service, store, *_ = build(published(), links=[link("l1", provider="awin")])

    await service.create_link(
        ACTOR,
        "off1",
        {"provider": "awin", "destination_url": "https://www.awin1.com/c?x=2"},
    )

    assert len(store.links) == 2


async def test_adding_a_direct_link_beside_a_network_link_stays_allowed():
    service, store, *_ = build(published(), links=[link("l1", provider="awin")])

    await service.create_link(
        ACTOR,
        "off1",
        {"provider": "direct", "destination_url": "https://www.acme.example/x"},
    )

    assert len(store.links) == 2


async def test_deactivating_a_direct_link_of_a_published_offer_stays_allowed():
    service, store, *_ = build(
        published(),
        links=[link("l1", provider="awin"), link("l2", provider="direct")],
    )

    await service.update_link(ACTOR, "off1", "l2", {"active": False})

    assert store.links["l2"].active is False


@pytest.mark.parametrize("status", [OfferStatus.DRAFT, OfferStatus.EXPIRED])
async def test_the_same_link_changes_on_an_unpublished_offer_pass(status):
    service, store, *_ = build(
        ready(id="off1", status=status),
        links=[link("l1", provider="awin")],
    )

    await service.update_link(ACTOR, "off1", "l1", {"active": False})
    await service.create_link(
        ACTOR,
        "off1",
        {"provider": "direct", "destination_url": "https://www.acme.example/x"},
    )

    assert store.links["l1"].active is False and len(store.links) == 2


async def test_an_unpublish_then_link_change_then_confirmed_publish_is_the_way_through():
    service, _store, *_ = build(published(), links=[link("l1", provider="awin")])

    await service.transition(ACTOR, "off1", OfferAction.UNPUBLISH)
    await service.update_link(ACTOR, "off1", "l1", {"active": False})
    await service.create_link(
        ACTOR,
        "off1",
        {"provider": "direct", "destination_url": "https://www.acme.example/x"},
    )
    detail = await publish(service, commercial_basis=CommercialBasis.AFFILIATE)

    assert detail.offer.status is OfferStatus.PUBLISHED


async def test_a_publish_audits_the_basis_it_went_live_with_and_whether_it_was_confirmed():
    service, _store, auditor, _session = build(
        ready(id="off1", commercial_basis=CommercialBasis.SPONSORED),
        links=[link("l1", provider="direct")],
    )

    await publish(service, commercial_basis=CommercialBasis.SPONSORED)

    (row,) = auditor.rows
    assert row["action"] == "admin.offers.publish"
    assert row["before"] == {"status": "DRAFT"}
    assert row["after"] == {"status": "PUBLISHED", "commercial_basis": "SPONSORED"}
    assert row["details"] == {"basis_confirmed": True}


async def test_a_publish_that_needed_no_confirmation_audits_basis_confirmed_false():
    service, _store, auditor, _session = build(
        ready(id="off1", commercial_basis=CommercialBasis.AFFILIATE),
        links=[link("l1", provider="awin")],
    )

    await publish(service)

    (row,) = auditor.rows
    assert row["after"] == {"status": "PUBLISHED", "commercial_basis": "AFFILIATE"}
    assert row["details"] == {"basis_confirmed": False}


async def test_other_transitions_audit_the_status_only():
    service, _store, auditor, _session = build(published())

    await service.transition(ACTOR, "off1", OfferAction.UNPUBLISH)

    (row,) = auditor.rows
    assert row["after"] == {"status": "DRAFT"} and row["details"] is None
