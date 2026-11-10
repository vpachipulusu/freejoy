"""The commercial basis of an offer in the admin service: stored, audited, reviewed and gating publish."""

import pytest

from app.core.permissions import AdminContext
from app.models.platform.offer import (
    CommercialBasis,
    OfferStatus,
    OfferType,
    PostageKind,
)
from app.services.admin_catalogue_errors import OfferChanged, PublishBlocked
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
        ACTOR, "off1", {"commercial_basis": CommercialBasis.SPONSORED}
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


async def test_a_published_offer_can_change_its_basis_without_breaking_publish_rules():
    service, *_ = build(
        ready(
            id="off1",
            status=OfferStatus.PUBLISHED,
            source_url="https://shop.example.com",
        )
    )

    detail = await service.update_offer(
        ACTOR, "off1", {"commercial_basis": CommercialBasis.SPONSORED}
    )

    assert detail.offer.commercial_basis is CommercialBasis.SPONSORED


async def test_the_review_token_changes_when_only_the_basis_changes():
    service, *_ = build(offer("off1"))
    before = await token(service)

    await service.update_offer(
        ACTOR, "off1", {"commercial_basis": CommercialBasis.AFFILIATE}
    )

    assert await token(service) != before


async def test_a_stale_token_after_a_basis_change_is_refused_on_publish():
    service, *_ = build(ready(id="off1", source_url="https://shop.example.com"))
    loaded = await token(service)
    await service.update_offer(
        ACTOR, "off1", {"commercial_basis": CommercialBasis.SPONSORED}
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
        [link("l1", provider="sample")],
    ],
    ids=["network", "network and direct", "only the network is active", "sample"],
)
async def test_publishing_with_a_network_or_sample_link_needs_no_confirmation(links):
    service, *_ = build(ready(id="off1"), links=links)

    detail = await publish(service)

    assert detail.offer.status is OfferStatus.PUBLISHED


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


async def test_deactivating_the_network_link_of_a_published_offer_keeps_its_basis():
    service, store, *_ = build(
        ready(
            id="off1",
            status=OfferStatus.PUBLISHED,
            commercial_basis=CommercialBasis.AFFILIATE,
        ),
        links=[link("l1", provider="awin"), link("l2", provider="direct")],
    )

    await service.update_link(ACTOR, "off1", "l1", {"active": False})

    assert store.offers["off1"].commercial_basis is CommercialBasis.AFFILIATE
    assert store.offers["off1"].status is OfferStatus.PUBLISHED
