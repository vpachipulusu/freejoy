"""AdminOfferService rules with in-memory fakes: no database, no network."""

from dataclasses import replace
from datetime import timedelta

import pytest

from app.core.permissions import AdminContext
from app.models.platform.offer import (
    CommercialBasis,
    OfferStatus,
    OfferType,
    PostageKind,
)
from app.repositories.admin_offers import OfferFilters
from app.services.admin_catalogue_errors import (
    CatalogueNotFound,
    IllegalTransition,
    InvalidInput,
    PublishBlocked,
    SlugTaken,
)
from app.services.admin_offers import AdminOfferService
from app.services.offer_lifecycle import OfferAction
from tests.fakes_admin_catalogue import (
    EARLIER,
    LATER,
    NOW,
    FakeAuditor,
    FakeOfferStore,
    FakeSession,
    link,
    offer,
)

ACTOR = AdminContext("admin1", "admin", frozenset({"offers.write"}))


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
        "description": ["One"],
        "featured": False,
        "image_tint": "sage",
        "image_label": "Free",
        "image_alt": "Alt",
        "source_url": "https://Shop.Example.com/box",
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
    """The review token the admin read would have handed the editor."""
    return (await service.get_offer(offer_id)).review_token


def verified(**changes):
    return offer(
        last_verified_at=EARLIER, source_url="https://shop.example.com", **changes
    )


async def test_create_derives_the_slug_normalises_the_url_and_audits_once():
    service, _store, auditor, session = build()

    detail = await service.create_offer(ACTOR, new_data())

    assert detail.offer.slug == "free-sample-box"
    assert detail.offer.status is OfferStatus.DRAFT
    assert detail.offer.source_url == "https://shop.example.com/box"
    assert detail.offer.last_verified_at is None
    (row,) = auditor.rows
    assert row["action"] == "admin.offers.create" and row["resource"] == "offers"
    assert row["resource_id"] == detail.offer.id and session.commits == 1
    assert row["after"]["slug"] == "free-sample-box"


async def test_create_with_an_explicit_slug_uses_it():
    service, *_ = build()

    detail = await service.create_offer(ACTOR, new_data(slug="my-slug"))

    assert detail.offer.slug == "my-slug"


async def test_create_conflicts_on_an_existing_slug_and_writes_nothing():
    service, store, auditor, session = build(offer("off1", slug="free-sample-box"))

    with pytest.raises(SlugTaken):
        await service.create_offer(ACTOR, new_data())

    assert len(store.offers) == 1 and auditor.rows == [] and session.commits == 0


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"source_url": "http://shop.example.com"}, "invalid_url"),
        ({"source_url": "javascript:alert(1)"}, "invalid_url"),
        ({"slug": "Bad Slug"}, "invalid_slug"),
        ({"title": "!!!"}, "invalid_slug"),
        ({"postage_kind": PostageKind.COST}, "invalid_postage"),
        (
            {
                "postage_kind": PostageKind.COST,
                "postage_amount_minor": 99,
                "postage_currency": "gbp",
            },
            "invalid_postage",
        ),
        ({"postage_amount_minor": 99}, "invalid_postage"),
        ({"start_at": LATER, "end_at": NOW}, "invalid_window"),
        ({"category_id": "ghost"}, "unknown_category"),
        ({"merchant_id": "ghost"}, "unknown_merchant"),
    ],
)
async def test_create_rejects_invalid_input_with_a_stable_code(changes, code):
    service, store, auditor, session = build()

    with pytest.raises(InvalidInput) as raised:
        await service.create_offer(ACTOR, new_data(**changes))

    assert raised.value.code == code
    assert store.offers == {} and auditor.rows == [] and session.commits == 0


async def test_create_accepts_complete_cost_postage():
    service, *_ = build()
    data = new_data(
        postage_kind=PostageKind.COST, postage_amount_minor=299, postage_currency="GBP"
    )

    detail = await service.create_offer(ACTOR, data)

    assert detail.offer.postage_amount_minor == 299


async def test_get_unknown_offer_is_not_found():
    service, *_ = build()

    with pytest.raises(CatalogueNotFound):
        await service.get_offer("ghost")


async def test_get_returns_the_offer_with_its_links():
    service, *_ = build(offer("off1"), links=[link("l1"), link("l2", "other")])

    detail = await service.get_offer("off1")

    assert [item.id for item in detail.links] == ["l1"]


async def test_list_clamps_the_page_window():
    service, store, *_ = build()

    await service.list_offers(OfferFilters(), 10_000, -5)

    assert store.filters[0][1:] == (100, 0)


async def test_update_locks_changes_fields_and_audits_names_without_free_text():
    service, store, auditor, session = build(offer("off1"))

    detail = await service.update_offer(
        ACTOR, "off1", {"title": "New title", "summary": "secret copy"}
    )

    assert detail.offer.title == "New title"
    assert store.locked == ["off1"] and session.commits == 1
    (row,) = auditor.rows
    assert row["action"] == "admin.offers.update"
    assert row["before"] == {"title": "Offer off1"} and row["after"] == {
        "title": "New title"
    }
    assert row["details"] == {"fields": ["summary", "title"]}
    assert "secret copy" not in repr(row)


@pytest.mark.parametrize(
    "changes", [{}, {"slug": "x"}, {"status": "PUBLISHED"}, {"id": "x"}]
)
async def test_update_refuses_empty_or_forbidden_fields(changes):
    service, *_ = build(offer("off1"))

    with pytest.raises(ValueError, match="Invalid offer fields"):
        await service.update_offer(ACTOR, "off1", changes)


async def test_update_validates_the_merged_postage_and_window():
    service, *_ = build(
        offer(
            "off1",
            postage_kind=PostageKind.COST,
            postage_amount_minor=1,
            postage_currency="GBP",
        )
    )

    with pytest.raises(InvalidInput):
        await service.update_offer(ACTOR, "off1", {"postage_kind": PostageKind.FREE})
    with pytest.raises(InvalidInput):
        await service.update_offer(ACTOR, "off1", {"start_at": LATER, "end_at": NOW})
    with pytest.raises(InvalidInput):
        await service.update_offer(
            ACTOR, "off1", {"source_url": "http://x.example.com"}
        )
    with pytest.raises(InvalidInput) as unknown:
        await service.update_offer(ACTOR, "off1", {"merchant_id": "ghost"})
    assert unknown.value.code == "unknown_merchant"


async def test_update_of_a_missing_or_archived_offer_is_refused():
    service, *_ = build(offer("gone", status=OfferStatus.ARCHIVED))

    with pytest.raises(CatalogueNotFound):
        await service.update_offer(ACTOR, "ghost", {"title": "x"})
    with pytest.raises(IllegalTransition):
        await service.update_offer(ACTOR, "gone", {"title": "x"})


async def test_update_of_a_published_offer_cannot_break_the_publish_rules():
    service, store, _, session = build(
        verified(id="off1", status=OfferStatus.PUBLISHED, end_at=LATER)
    )

    with pytest.raises(PublishBlocked) as past:
        await service.update_offer(ACTOR, "off1", {"end_at": EARLIER})
    with pytest.raises(PublishBlocked) as nowhere:
        await service.update_offer(ACTOR, "off1", {"source_url": None})
    store.categories["cat2"] = False
    with pytest.raises(PublishBlocked) as disabled:
        await service.update_offer(ACTOR, "off1", {"category_id": "cat2"})

    assert past.value.reasons == ["end_date_passed"]
    assert nowhere.value.reasons == ["no_destination"]
    assert disabled.value.reasons == ["category_disabled"]
    assert session.commits == 0


async def test_a_draft_may_point_at_a_disabled_category():
    service, store, *_ = build(offer("off1"))
    store.categories["cat2"] = False

    detail = await service.update_offer(ACTOR, "off1", {"category_id": "cat2"})

    assert detail.offer.category_id == "cat2"


async def test_publish_requires_verification_and_never_stamps_it():
    service, store, auditor, session = build(
        offer("off1", source_url="https://shop.example.com")
    )

    with pytest.raises(PublishBlocked) as raised:
        await service.transition(
            ACTOR, "off1", OfferAction.PUBLISH, review_token=await token(service)
        )

    assert raised.value.reasons == ["not_verified"]
    assert store.offers["off1"].last_verified_at is None
    assert auditor.rows == [] and session.commits == 0


async def test_publish_succeeds_when_every_rule_holds_and_audits_the_transition():
    service, store, auditor, session = build(verified(id="off1"))

    detail = await service.transition(
        ACTOR, "off1", OfferAction.PUBLISH, review_token=await token(service)
    )

    assert detail.offer.status is OfferStatus.PUBLISHED
    assert detail.offer.last_verified_at == EARLIER
    (row,) = auditor.rows
    assert row["action"] == "admin.offers.publish"
    assert row["before"] == {"status": "DRAFT"} and row["after"] == {
        "status": "PUBLISHED"
    }
    assert session.commits == 1 and store.locked == ["off1"]


async def test_publish_with_an_active_link_instead_of_a_source_url():
    service, *_ = build(offer("off1", last_verified_at=EARLIER), links=[link("l1")])

    detail = await service.transition(
        ACTOR, "off1", OfferAction.PUBLISH, review_token=await token(service)
    )

    assert detail.offer.status is OfferStatus.PUBLISHED


async def test_publish_with_only_inactive_links_and_no_source_is_blocked():
    service, *_ = build(
        offer("off1", last_verified_at=EARLIER), links=[link("l1", active=False)]
    )

    with pytest.raises(PublishBlocked) as raised:
        await service.transition(
            ACTOR, "off1", OfferAction.PUBLISH, review_token=await token(service)
        )

    assert raised.value.reasons == ["no_destination"]


async def test_publish_blocked_by_disabled_category_inactive_merchant_and_past_end():
    service, store, *_ = build(verified(id="off1", end_at=EARLIER))
    store.categories["cat1"] = False
    store.merchants["mer1"] = False

    with pytest.raises(PublishBlocked) as raised:
        await service.transition(
            ACTOR, "off1", OfferAction.PUBLISH, review_token=await token(service)
        )

    assert raised.value.reasons == [
        "category_disabled",
        "merchant_inactive",
        "end_date_passed",
    ]


async def test_expired_offers_republish_after_the_rules_are_met_again():
    service, *_ = build(verified(id="off1", status=OfferStatus.EXPIRED, end_at=LATER))

    detail = await service.transition(
        ACTOR, "off1", OfferAction.PUBLISH, review_token=await token(service)
    )

    assert detail.offer.status is OfferStatus.PUBLISHED


@pytest.mark.parametrize(
    ("status", "action"),
    [
        (OfferStatus.PUBLISHED, OfferAction.PUBLISH),
        (OfferStatus.ARCHIVED, OfferAction.PUBLISH),
        (OfferStatus.DRAFT, OfferAction.UNPUBLISH),
        (OfferStatus.DRAFT, OfferAction.EXPIRE),
        (OfferStatus.EXPIRED, OfferAction.EXPIRE),
        (OfferStatus.ARCHIVED, OfferAction.ARCHIVE),
    ],
)
async def test_illegal_transitions_change_and_audit_nothing(status, action):
    service, store, auditor, session = build(verified(id="off1", status=status))

    with pytest.raises(IllegalTransition):
        await service.transition(
            ACTOR, "off1", action, review_token=await token(service)
        )

    assert store.offers["off1"].status is status
    assert auditor.rows == [] and session.commits == 0


@pytest.mark.parametrize(
    ("start", "action", "end"),
    [
        (OfferStatus.PUBLISHED, OfferAction.UNPUBLISH, OfferStatus.DRAFT),
        (OfferStatus.PUBLISHED, OfferAction.EXPIRE, OfferStatus.EXPIRED),
        (OfferStatus.PUBLISHED, OfferAction.ARCHIVE, OfferStatus.ARCHIVED),
        (OfferStatus.DRAFT, OfferAction.ARCHIVE, OfferStatus.ARCHIVED),
    ],
)
async def test_other_lifecycle_actions_move_the_status(start, action, end):
    service, _, auditor, _ = build(verified(id="off1", status=start))

    detail = await service.transition(
        ACTOR, "off1", action, review_token=await token(service)
    )

    assert detail.offer.status is end
    assert auditor.rows[0]["action"] == f"admin.offers.{action.value}"


async def test_transition_of_a_missing_offer_is_not_found_and_verify_is_not_a_transition():
    service, *_ = build()

    with pytest.raises(CatalogueNotFound):
        await service.transition(ACTOR, "ghost", OfferAction.PUBLISH, review_token="x")
    with pytest.raises(ValueError, match="verify"):
        await service.transition(ACTOR, "ghost", OfferAction.VERIFY)


async def test_verify_stamps_the_clock_and_audits_the_note():
    service, _, auditor, session = build(offer("off1"))

    detail = await service.verify(
        ACTOR,
        "off1",
        "Checked terms on the merchant site",
        review_token=await token(service),
    )

    assert detail.offer.last_verified_at == NOW
    (row,) = auditor.rows
    assert row["action"] == "admin.offers.verify"
    assert row["details"] == {"note": "Checked terms on the merchant site"}
    assert row["before"] == {"last_verified_at": None}
    assert session.commits == 1


async def test_verify_without_a_note_records_no_details():
    service, _, auditor, _ = build(offer("off1"))

    await service.verify(ACTOR, "off1", None, review_token=await token(service))

    assert auditor.rows[0]["details"] is None


async def test_verify_refuses_archived_and_missing_offers():
    service, *_ = build(offer("gone", status=OfferStatus.ARCHIVED))

    with pytest.raises(IllegalTransition):
        await service.verify(
            ACTOR, "gone", None, review_token=await token(service, "gone")
        )
    with pytest.raises(CatalogueNotFound):
        await service.verify(ACTOR, "ghost", None, review_token="x")


async def test_create_link_validates_the_url_and_audits_only_the_host():
    service, _, auditor, session = build(offer("off1"))

    created = await service.create_link(
        ACTOR,
        "off1",
        {
            "provider": "awin",
            "destination_url": "https://Www.Awin1.com/c?id=SECRET-TRACKING",
            "tracking_id": "t-1",
            "active": True,
        },
    )

    assert created.destination_url == "https://www.awin1.com/c?id=SECRET-TRACKING"
    (row,) = auditor.rows
    assert row["action"] == "admin.offer_links.create"
    assert row["resource"] == "affiliate_links" and row["resource_id"] == created.id
    assert row["details"] == {
        "offer_id": "off1",
        "destination_host": "www.awin1.com",
    }
    assert "SECRET-TRACKING" not in repr(row) and session.commits == 1


@pytest.mark.parametrize(
    "url",
    [
        "http://www.awin1.com",
        "javascript:alert(1)",
        "https://good.example.com@evil.example.com",
        "//evil.example.com",
    ],
)
async def test_create_link_rejects_unsafe_destinations(url):
    service, store, auditor, _ = build(offer("off1"))
    data = {
        "provider": "awin",
        "destination_url": url,
        "tracking_id": None,
        "active": True,
    }

    with pytest.raises(InvalidInput) as raised:
        await service.create_link(ACTOR, "off1", data)

    assert raised.value.code == "invalid_url"
    assert store.links == {} and auditor.rows == []


async def test_links_cannot_be_added_to_missing_or_archived_offers():
    service, *_ = build(offer("gone", status=OfferStatus.ARCHIVED))
    data = {
        "provider": "awin",
        "destination_url": "https://www.awin1.com",
        "tracking_id": None,
        "active": True,
    }

    with pytest.raises(CatalogueNotFound):
        await service.create_link(ACTOR, "ghost", data)
    with pytest.raises(IllegalTransition):
        await service.create_link(ACTOR, "gone", data)


async def test_update_link_changes_fields_and_audits():
    service, _, auditor, _ = build(offer("off1"), links=[link("l1")])

    updated = await service.update_link(
        ACTOR,
        "off1",
        "l1",
        {"destination_url": "https://www.awin1.com/x", "active": False},
    )

    assert updated.destination_url == "https://www.awin1.com/x" and not updated.active
    (row,) = auditor.rows
    assert row["action"] == "admin.offer_links.update"
    assert row["before"] == {"active": True, "destination_host": "shop.example.com"}
    assert row["after"] == {"active": False, "destination_host": "www.awin1.com"}
    assert row["details"]["fields"] == ["active", "destination_url"]
    assert "awin1.com/x" not in repr(row)


async def test_update_link_errors():
    service, *_ = build(
        offer("off1"), offer("gone", status=OfferStatus.ARCHIVED), links=[link("l1")]
    )

    with pytest.raises(ValueError, match="Invalid link fields"):
        await service.update_link(ACTOR, "off1", "l1", {"offer_id": "x"})
    with pytest.raises(CatalogueNotFound):
        await service.update_link(ACTOR, "off1", "missing", {"active": False})
    with pytest.raises(CatalogueNotFound):
        await service.update_link(ACTOR, "ghost", "l1", {"active": False})
    with pytest.raises(IllegalTransition):
        await service.update_link(ACTOR, "gone", "l1", {"active": False})
    with pytest.raises(InvalidInput):
        await service.update_link(
            ACTOR, "off1", "l1", {"destination_url": "http://www.awin1.com"}
        )


async def test_deactivating_the_last_link_of_a_published_offer_is_blocked():
    published = offer("off1", status=OfferStatus.PUBLISHED, last_verified_at=EARLIER)
    service, store, auditor, _ = build(published, links=[link("l1")])

    with pytest.raises(PublishBlocked) as raised:
        await service.update_link(ACTOR, "off1", "l1", {"active": False})

    assert raised.value.reasons == ["no_destination"]
    assert store.links["l1"].active and auditor.rows == []


async def test_deactivating_a_link_is_fine_when_another_or_a_source_url_remains():
    published = offer("off1", status=OfferStatus.PUBLISHED, last_verified_at=EARLIER)
    service, store, *_ = build(published, links=[link("l1"), link("l2")])

    await service.update_link(ACTOR, "off1", "l1", {"active": False})
    store.offers["off1"] = offer(
        "off1",
        status=OfferStatus.PUBLISHED,
        last_verified_at=EARLIER,
        source_url="https://shop.example.com",
    )
    await service.update_link(ACTOR, "off1", "l2", {"active": False})

    assert not store.links["l1"].active and not store.links["l2"].active


async def test_an_expired_but_published_offer_stays_editable():
    service, store, *_ = build(
        verified(id="off1", status=OfferStatus.PUBLISHED, end_at=EARLIER)
    )

    renamed = await service.update_offer(ACTOR, "off1", {"title": "x"})

    assert renamed.offer.title == "x" and renamed.offer.end_at == EARLIER
    with pytest.raises(PublishBlocked) as still_past:
        await service.update_offer(
            ACTOR, "off1", {"end_at": EARLIER - timedelta(days=1)}
        )
    assert still_past.value.reasons == ["end_date_passed"]
    moved = await service.update_offer(ACTOR, "off1", {"end_at": LATER})
    assert moved.offer.end_at == LATER
    assert store.offers["off1"].status is OfferStatus.PUBLISHED


async def test_an_expired_published_offer_still_obeys_the_other_publish_rules():
    service, *_ = build(
        verified(id="off1", status=OfferStatus.PUBLISHED, end_at=EARLIER)
    )

    with pytest.raises(PublishBlocked) as raised:
        await service.update_offer(ACTOR, "off1", {"source_url": None})

    assert raised.value.reasons == ["no_destination"]


async def test_update_link_audits_the_destination_host_before_and_after_never_the_url():
    service, _, auditor, _ = build(offer("off1"), links=[link("l1")])

    await service.update_link(
        ACTOR,
        "off1",
        "l1",
        {"destination_url": "https://www.awin1.com/p?aff=SECRET"},
    )

    (row,) = auditor.rows
    assert row["before"] == {"destination_host": "shop.example.com"}
    assert row["after"] == {"destination_host": "www.awin1.com"}
    assert "SECRET" not in repr(row) and "/p" not in repr(row)


def link_data(provider="awin", url="https://www.awin1.com/c?id=1"):
    return {
        "provider": provider,
        "destination_url": url,
        "tracking_id": None,
        "active": True,
    }


@pytest.mark.parametrize(
    ("provider", "url", "code"),
    [
        ("awin", "https://www.tradedoubler.com/x", "destination_not_allowed"),
        ("awin", "https://awin1.com.evil.example/x", "destination_not_allowed"),
        ("direct", "https://evil.example/x", "destination_not_allowed"),
        ("direct", "https://deals.acme.example/x", "destination_not_allowed"),
        ("mystery", "https://www.awin1.com/x", "unknown_provider"),
    ],
)
async def test_create_link_refuses_a_destination_the_provider_may_not_use(
    provider, url, code
):
    service, store, auditor, session = build(offer("off1"))

    with pytest.raises(InvalidInput) as raised:
        await service.create_link(ACTOR, "off1", link_data(provider, url))

    assert raised.value.code == code
    assert store.links == {} and auditor.rows == [] and session.commits == 0


@pytest.mark.parametrize(
    "url", ["https://acme.example/x", "https://www.acme.example/x"]
)
async def test_a_direct_link_on_the_merchant_website_is_accepted(url):
    service, store, *_ = build(offer("off1"))

    created = await service.create_link(ACTOR, "off1", link_data("direct", url))

    assert created.id in store.links


async def test_a_direct_link_needs_a_merchant_website():
    service, store, *_ = build(offer("off1"))
    store.websites["mer1"] = None

    with pytest.raises(InvalidInput) as raised:
        await service.create_link(
            ACTOR, "off1", link_data("direct", "https://acme.example/x")
        )

    assert raised.value.code == "destination_not_allowed"


async def test_update_link_checks_the_host_when_the_url_or_provider_changes():
    service, store, auditor, _ = build(offer("off1"), links=[link("l1")])

    with pytest.raises(InvalidInput) as moved:
        await service.update_link(
            ACTOR, "off1", "l1", {"destination_url": "https://www.tradedoubler.com/x"}
        )
    with pytest.raises(InvalidInput) as renamed:
        await service.update_link(ACTOR, "off1", "l1", {"provider": "mystery"})

    assert moved.value.code == "destination_not_allowed"
    assert renamed.value.code == "unknown_provider"
    assert store.links["l1"].destination_url == "https://shop.example.com/go"
    assert auditor.rows == []


async def test_update_link_can_still_switch_off_a_link_that_predates_the_allowlist():
    service, store, *_ = build(offer("off1"), links=[link("l1")])

    updated = await service.update_link(ACTOR, "off1", "l1", {"active": False})

    assert updated.active is False
    assert store.links["l1"].destination_url == "https://shop.example.com/go"


async def test_reactivating_a_link_runs_the_host_check_again():
    legacy = link(
        "l1", active=False
    )  # awin on shop.example.com: predates the allowlist
    service, store, auditor, _ = build(offer("off1"), links=[legacy])

    with pytest.raises(InvalidInput) as raised:
        await service.update_link(ACTOR, "off1", "l1", {"active": True})

    assert raised.value.code == "destination_not_allowed"
    assert store.links["l1"].active is False and auditor.rows == []


async def test_reactivating_a_link_with_an_allowed_host_works():
    good = replace(
        link("l1", active=False), destination_url="https://www.awin1.com/c?id=1"
    )
    service, store, *_ = build(offer("off1"), links=[good])

    updated = await service.update_link(ACTOR, "off1", "l1", {"active": True})

    assert updated.active is True and store.links["l1"].active is True


async def test_a_patch_that_keeps_an_active_link_active_is_not_rechecked():
    service, *_ = build(offer("off1"), links=[link("l1")])

    updated = await service.update_link(ACTOR, "off1", "l1", {"tracking_id": "t-2"})

    assert updated.tracking_id == "t-2"
