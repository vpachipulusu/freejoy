"""Applying or dismissing the update a retailer sent: the editor's side, run without a database (FREEJ-58)."""

from dataclasses import replace
from datetime import timedelta

import pytest

from app.core.permissions import AdminContext
from app.models.platform.offer import OfferStatus, OfferType, PostageKind
from app.repositories.admin_offers import PartnerSubmissionRecord
from app.services.admin_catalogue_errors import (
    IllegalTransition,
    InvalidInput,
    OfferChanged,
    PermissionDenied,
)
from app.services.admin_partner_updates import (
    AdminPartnerUpdateService,
    keep_an_ended_offer_ended,
    proposed_changes,
)
from app.services.offer_lifecycle import OfferAction
from tests.fakes_admin_catalogue import (
    EARLIER,
    NOW,
    FakeAuditor,
    FakeOfferStore,
    FakeSession,
    link,
    offer,
    provenance,
)

PUBLISHER = AdminContext("ed1", "admin", frozenset({"offers.write", "offers.publish"}))
WRITER = AdminContext("ed2", "admin", frozenset({"offers.write"}))
HASH = "a" * 64
PAYLOAD = {
    "title": "Retailer title",
    "summary": "Retailer summary",
    "description": ["Para"],
    "category_id": "cat1",
    "offer_type": "FREE_TRIAL",
    "postage_kind": "FREE",
    "postage_amount_minor": None,
    "postage_currency": None,
    "requires_card": True,
    "subscription_terms": "Cancel any time",
    "eligibility": "Everyone",
    "availability": "Now",
    "steps": [{"title": "Go", "detail": "Do"}],
    "start_at": None,
    "end_at": "2099-01-01T00:00:00+00:00",
    "destination_url": "https://www.acme.example/new",
}


def build(*, status=OfferStatus.DRAFT, payload=PAYLOAD, provider="partner", **changes):
    store, auditor, session = FakeOfferStore(), FakeAuditor(), FakeSession()
    store.offers["off1"] = offer("off1", status=status, **changes)
    store.links["l1"] = link("l1", provider="direct")
    store.provenance["off1"] = provenance(provider=provider, source_changed_at=NOW)
    if payload is not None:
        store.partners["off1"] = PartnerSubmissionRecord(
            "s1", "sku-1", "mer1", "Acme", "key1", EARLIER, payload, HASH, NOW
        )
    service = AdminPartnerUpdateService(store, session, auditor, clock=lambda: NOW)
    return service, store, auditor, session


async def token(service):
    return (await service.get_offer("off1")).review_token


async def test_applying_edits_through_the_normal_path_updates_the_link_and_clears_the_proposal():
    service, store, auditor, session = build()

    detail = await service.apply_partner_update(
        PUBLISHER, "off1", await token(service), HASH
    )

    stored = store.offers["off1"]
    assert (stored.title, stored.offer_type, stored.postage_kind) == (
        "Retailer title",
        OfferType.FREE_TRIAL,
        PostageKind.FREE,
    )
    assert store.links["l1"].destination_url == "https://www.acme.example/new"
    assert store.partners["off1"].pending_payload is None
    assert store.provenance["off1"].source_changed_at is None
    assert store.applied_hashes == [HASH]
    assert [r["action"] for r in auditor.rows] == [
        "admin.offers.update",
        "admin.offer_links.update",
        "admin.offers.partner_update_applied",
    ]
    assert auditor.rows[-1]["details"] == {
        "ref": "sku-1",
        "merchant_id": "mer1",
        "pending_hash": HASH,
        "link_updated": True,
    }
    assert session.commits == 1
    assert detail.partner is not None and detail.partner.pending_payload is None


async def test_applying_never_touches_the_status_the_verification_the_basis_or_the_flag():
    service, store, *_ = build(
        last_verified_at=EARLIER, featured=False, status=OfferStatus.DRAFT
    )
    before = store.offers["off1"]

    await service.apply_partner_update(PUBLISHER, "off1", await token(service), HASH)

    after = store.offers["off1"]
    for field in (
        "status",
        "last_verified_at",
        "commercial_basis",
        "featured",
        "slug",
        "merchant_id",
    ):
        assert getattr(after, field) == getattr(before, field), field


async def test_applying_locks_the_offer_then_provenance_then_submission():
    service, store, *_ = build()

    await service.apply_partner_update(PUBLISHER, "off1", await token(service), HASH)

    assert store.locked[0] == "off1"
    assert store.provenance_locked == ["off1"] and store.partner_locked == ["off1"]


async def test_a_stale_review_token_is_refused_before_anything_is_read_or_written():
    service, store, auditor, session = build()
    stale = await token(service)
    store.offers["off1"] = replace(store.offers["off1"], title="Changed meanwhile")

    with pytest.raises(OfferChanged):
        await service.apply_partner_update(PUBLISHER, "off1", stale, HASH)

    assert auditor.rows == [] and session.commits == 0
    assert store.partners["off1"].pending_payload is not None


async def test_a_token_is_required_and_a_newer_proposal_than_the_one_shown_is_refused():
    service, _store, auditor, _ = build()

    with pytest.raises(InvalidInput) as missing:
        await service.apply_partner_update(PUBLISHER, "off1", None, HASH)
    with pytest.raises(OfferChanged):
        await service.apply_partner_update(
            PUBLISHER, "off1", await token(service), "b" * 64
        )

    assert missing.value.code == "review_token_required"
    assert auditor.rows == []


@pytest.mark.parametrize("action", ["apply_partner_update", "dismiss_partner_update"])
async def test_there_must_be_a_proposal_from_a_partner_submission(action):
    for kwargs in ({"payload": None}, {"provider": "awin"}):
        service, *_ = build(**kwargs)

        with pytest.raises(IllegalTransition):
            await getattr(service, action)(
                PUBLISHER, "off1", await token(service), HASH
            )


async def test_a_live_offer_needs_the_publish_permission_to_take_a_proposal():
    service, store, *_ = build(status=OfferStatus.PUBLISHED, last_verified_at=EARLIER)

    with pytest.raises(PermissionDenied):
        await service.apply_partner_update(WRITER, "off1", await token(service), HASH)

    assert store.offers["off1"].title != "Retailer title"


async def test_a_destination_that_no_longer_fits_the_merchants_site_fails_the_whole_apply():
    service, _store, *_ = build(
        payload={**PAYLOAD, "destination_url": "https://evil.example.net/x"}
    )

    with pytest.raises(InvalidInput) as refused:
        await service.apply_partner_update(
            PUBLISHER, "off1", await token(service), HASH
        )

    assert refused.value.code == "destination_not_allowed"


async def test_a_link_is_left_alone_when_the_offer_has_two_direct_links_or_the_url_is_the_same():
    service, store, *_ = build()
    store.links["l2"] = link("l2", provider="direct")

    await service.apply_partner_update(PUBLISHER, "off1", await token(service), HASH)

    assert store.links["l1"].destination_url == "https://shop.example.com/go"
    assert store.links["l2"].destination_url == "https://shop.example.com/go"


async def test_dismissing_clears_the_proposal_and_changes_only_that():
    service, store, auditor, session = build()
    before = store.offers["off1"]

    detail = await service.dismiss_partner_update(
        PUBLISHER, "off1", await token(service), HASH
    )

    assert store.offers["off1"] == before
    assert store.partners["off1"].pending_payload is None
    assert store.applied_hashes == [None]
    assert [r["action"] for r in auditor.rows] == [
        "admin.offers.partner_update_dismissed"
    ]
    assert session.commits == 1 and detail.partner is not None


def test_a_stored_proposal_becomes_typed_offer_columns():
    changes = proposed_changes(PAYLOAD)

    assert changes["offer_type"] is OfferType.FREE_TRIAL
    assert changes["end_at"].year == 2099 and changes["start_at"] is None
    assert "destination_url" not in changes


@pytest.mark.parametrize(
    ("current_end", "proposed_end", "kept"),
    [
        (NOW - timedelta(days=1), None, True),
        (NOW - timedelta(days=1), "2020-01-01T00:00:00+00:00", True),
        (NOW - timedelta(days=1), "2099-01-01T00:00:00+00:00", False),
        (None, None, False),
        (NOW + timedelta(days=1), None, False),
    ],
)
def test_an_ended_offer_keeps_its_end_unless_the_proposal_gives_a_future_one(
    current_end, proposed_end, kept
):
    current = offer("off1", end_at=current_end)
    changes = proposed_changes({**PAYLOAD, "end_at": proposed_end})

    result = keep_an_ended_offer_ended(current, changes, NOW)

    assert ("end_at" not in result) is kept


@pytest.mark.parametrize(
    "bad",
    [
        {"title": "<b>Bold</b>"},
        {"title": ""},
        {"title": "x" * 201},
        {"title": 5},
        {"description": "not a list"},
        {"description": ["<script>x</script>"]},
        {"steps": [{"title": "t"}]},
        {"steps": [{"title": "<i>t</i>", "detail": "d"}]},
        {"offer_type": "CASHBACK_NOPE"},
        {"postage_kind": "SOMETIMES"},
        {"requires_card": "yes"},
        {"postage_amount_minor": "5"},
        {"start_at": "not a date"},
        {"end_at": "2099-01-01T00:00:00"},
        {"end_at": 5},
        {"category_id": None},
        {"destination_url": 5},
    ],
)
async def test_a_stored_proposal_that_is_not_valid_content_is_refused_and_nothing_is_written(
    bad,
):
    service, store, auditor, session = build(payload={**PAYLOAD, **bad})

    with pytest.raises(InvalidInput) as refused:
        await service.apply_partner_update(
            PUBLISHER, "off1", await token(service), HASH
        )

    assert refused.value.code == "invalid_pending_payload"
    assert store.offers["off1"].title != "Retailer title"
    assert auditor.rows == [] and session.commits == 0


async def test_a_proposal_missing_a_field_is_refused_not_a_crash():
    payload = {k: v for k, v in PAYLOAD.items() if k != "eligibility"}
    service, *_ = build(payload=payload)

    with pytest.raises(InvalidInput):
        await service.apply_partner_update(
            PUBLISHER, "off1", await token(service), HASH
        )


async def test_a_partner_draft_always_needs_the_basis_confirmed_to_publish_even_with_a_network_link():
    from app.services.admin_catalogue_errors import PublishBlocked

    service, store, *_ = build(last_verified_at=EARLIER, payload=None)
    store.links["l2"] = link("l2", provider="awin")
    store.links.pop("l1")

    with pytest.raises(PublishBlocked) as blocked:
        await service.transition(
            PUBLISHER, "off1", OfferAction.PUBLISH, await token(service)
        )

    assert "commercial_basis_required" in blocked.value.reasons
