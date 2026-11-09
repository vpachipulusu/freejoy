"""``OfferImporter`` on an in-memory catalogue: drafts, idempotency, refreshes and the edit protection."""

import dataclasses
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.models.platform.offer import OfferFreshness, OfferStatus, OfferType
from app.repositories.awin_import import OfferImportRecord
from app.services.awin_offer_import import (
    SOURCE,
    ImportAborted,
    ImportTally,
    OfferImporter,
)
from app.services.awin_offer_mapping import fingerprint
from app.services.awin_promotions import Promotion, source_hash
from app.services.offer_lifecycle import PublishFacts, PublishReason, publish_blockers
from tests.awin_samples import CODE, promotion, tracking_for
from tests.fakes_awin_import import T0, FakeDatabase

LATER = T0 + timedelta(days=1)


async def run(
    db: FakeDatabase, *promotions: Promotion, now: datetime = T0
) -> ImportTally:
    async with db.unit() as unit:
        importer = OfferImporter(unit.store, unit.session, unit.auditor, lambda: now)
        return await importer.import_batch(promotions)


def only_offer(db: FakeDatabase) -> Any:
    (offer,) = db.committed.offers.values()
    return offer


def only_import(db: FakeDatabase) -> OfferImportRecord:
    (record,) = db.committed.imports.values()
    return record


def actions(db: FakeDatabase) -> list[str]:
    return [audit["action"] for audit in db.committed.audits]


def snapshot(db: FakeDatabase) -> Any:
    state = db.committed
    return (
        dict(state.offers),
        dict(state.links),
        dict(state.imports),
        dict(state.merchants),
        list(state.audits),
    )


# --- creating ------------------------------------------------------------------------------


async def test_a_new_promotion_becomes_a_draft_offer_with_a_link_and_provenance() -> (
    None
):
    db = FakeDatabase()

    tally = await run(db, promotion())

    state = db.committed
    offer, link = only_offer(db), next(iter(state.links.values()))
    assert tally.created == 1 and tally.processed == 1
    assert offer.status is OfferStatus.DRAFT
    assert offer.offer_type is OfferType.VOUCHER
    assert offer.last_verified_at is None and offer.featured is False
    assert link.offer_id == offer.id and link.provider == "awin" and link.active
    assert link.destination_url == promotion().tracking_url
    assert offer.source_url == "https://www.acme.example/sale-1001"
    record = only_import(db)
    assert (record.provider, record.external_id) == ("awin", "1001")
    assert record.offer_id == offer.id
    assert record.source_hash == record.applied_source_hash == source_hash(promotion())
    assert record.applied_content_hash == fingerprint(offer, [link])
    assert record.source_changed_at is None and record.applied_at == T0


async def test_an_imported_draft_cannot_be_published_until_an_editor_acts() -> None:
    db = FakeDatabase()
    await run(db, promotion())
    state = db.committed
    offer = only_offer(db)
    category = state.categories[offer.category_id]
    merchant = state.merchants[offer.merchant_id]

    blockers = publish_blockers(
        PublishFacts(
            category_enabled=category.enabled,
            merchant_active=merchant.active,
            active_links=1,
            has_source_url=bool(offer.source_url),
            end_at=offer.end_at,
            last_verified_at=offer.last_verified_at,
        ),
        T0,
    )

    assert blockers == [
        PublishReason.CATEGORY_DISABLED,
        PublishReason.MERCHANT_INACTIVE,
        PublishReason.NOT_VERIFIED,
    ]


async def test_the_holding_category_is_created_once_and_never_enabled() -> None:
    db = FakeDatabase()

    await run(db, promotion(1))
    await run(db, promotion(2))

    (category,) = db.committed.categories.values()
    assert category.slug == "imported-uncategorised" and category.enabled is False
    assert {o.category_id for o in db.committed.offers.values()} == {category.id}


async def test_a_holding_category_that_was_enabled_stops_the_import() -> None:
    db = FakeDatabase()
    await run(db, promotion(1))
    (category,) = db.committed.categories.values()
    db.committed.categories[category.id] = dataclasses.replace(category, enabled=True)
    before = snapshot(db)

    with pytest.raises(ImportAborted) as caught:
        await run(db, promotion(2))

    assert caught.value.reason == "import_category_enabled"
    assert snapshot(db) == before


async def test_both_kinds_are_vouchers_and_nothing_is_a_free_sample() -> None:
    db = FakeDatabase()

    await run(db, promotion(1), promotion(2, type="promotion", voucher=None))

    assert {o.offer_type for o in db.committed.offers.values()} == {OfferType.VOUCHER}


async def test_the_voucher_code_is_stored_nowhere() -> None:
    db = FakeDatabase()

    await run(db, promotion())

    state = db.committed
    everything = json.dumps(
        [
            [dataclasses.asdict(r) for r in state.offers.values()],
            [dataclasses.asdict(r) for r in state.links.values()],
            [dataclasses.asdict(r) for r in state.imports.values()],
            [dataclasses.asdict(r) for r in state.merchants.values()],
            state.audits,
        ],
        default=str,
    )
    assert CODE not in everything
    assert "not stored" in only_offer(db).publisher_notes


async def test_every_batch_takes_the_run_lock_first() -> None:
    db = FakeDatabase()

    await run(db, promotion(1))
    await run(db, promotion(2))

    assert db.committed.run_locks == 2


# --- merchants and slugs -------------------------------------------------------------------


async def test_a_new_advertiser_gets_an_inactive_merchant_mapped_by_advertiser_id() -> (
    None
):
    db = FakeDatabase()

    await run(db, promotion())

    state = db.committed
    (merchant,) = state.merchants.values()
    assert merchant.name == "Acme Stores" and merchant.active is False
    assert merchant.website is None  # the editor sets it when activating
    assert state.merchant_imports == {("awin", "77"): merchant.id}
    assert only_offer(db).merchant_id == merchant.id


async def test_promotions_of_one_advertiser_share_one_merchant() -> None:
    db = FakeDatabase()

    await run(db, promotion(1), promotion(2))
    await run(db, promotion(3))

    assert len(db.committed.merchants) == 1
    assert len({o.merchant_id for o in db.committed.offers.values()}) == 1


async def test_a_merchant_with_the_same_name_is_never_reused_or_touched() -> None:
    db = FakeDatabase()
    await run(db, promotion(1))
    (human,) = db.committed.merchants.values()
    db.committed.merchant_imports.clear()  # as if a person made it, with no provenance
    before = dataclasses.asdict(human)

    await run(db, promotion(2))

    assert len(db.committed.merchants) == 2
    assert dataclasses.asdict(db.committed.merchants[human.id]) == before
    other = next(m for m in db.committed.merchants.values() if m.id != human.id)
    assert other.slug == "awin-advertiser-77" and other.active is False


async def test_a_renamed_advertiser_does_not_change_or_activate_its_merchant() -> None:
    db = FakeDatabase()
    await run(db, promotion(1))
    (merchant,) = db.committed.merchants.values()
    renamed = promotion(
        1, advertiser={"id": 77, "name": "Acme Renamed", "joined": True}
    )

    await run(db, renamed, now=LATER)

    assert db.committed.merchants == {merchant.id: merchant}


async def test_the_offer_slug_is_stable_across_runs_and_refreshes() -> None:
    db = FakeDatabase()
    await run(db, promotion(1001))
    slug = only_offer(db).slug

    await run(db, promotion(1001, title="A completely different title"), now=LATER)

    assert only_offer(db).slug == slug and slug.endswith("-1001")


async def test_a_slug_taken_by_another_offer_falls_back_to_the_promotion_id() -> None:
    db = FakeDatabase()
    await run(db, promotion(1001))
    first = only_offer(db)
    db.committed.imports.clear()  # forget the provenance: the same promotion looks new
    db.committed.merchant_imports.clear()

    tally = await run(db, promotion(1001))

    assert tally.created == 1
    slugs = sorted(o.slug for o in db.committed.offers.values())
    assert slugs == sorted([first.slug, "awin-1001"])


async def test_when_every_slug_is_taken_the_item_is_skipped_and_nothing_is_created() -> (
    None
):
    db = FakeDatabase()
    await run(db, promotion(1001))
    db.committed.imports.clear()
    db.committed.merchant_imports.clear()
    await run(db, promotion(1001))
    db.committed.imports.clear()
    db.committed.merchant_imports.clear()
    offers, merchants = len(db.committed.offers), len(db.committed.merchants)

    tally = await run(db, promotion(1001))

    assert tally.created == 0 and tally.skipped == {"slug_unavailable": 1}
    assert len(db.committed.offers) == offers
    assert len(db.committed.merchants) == merchants


# --- idempotency and refresh ---------------------------------------------------------------


async def test_a_second_run_with_the_same_source_changes_nothing() -> None:
    db = FakeDatabase()
    await run(db, promotion(1), promotion(2))
    before = snapshot(db)

    tally = await run(db, promotion(1), promotion(2), now=LATER)

    assert tally.unchanged == 2 and tally.created == 0 and tally.updated == 0
    assert snapshot(db) == before


async def test_an_untouched_import_is_refreshed_when_the_source_changes() -> None:
    db = FakeDatabase()
    await run(db, promotion())
    created = only_offer(db)
    created_link = next(iter(db.committed.links.values()))
    changed = promotion(title="Brand new title", endDate="2026-12-15T00:00:00.000")

    tally = await run(db, changed, now=LATER)

    offer = only_offer(db)
    link = next(iter(db.committed.links.values()))
    record = only_import(db)
    assert tally.updated == 1
    assert offer.title == "Brand new title"
    assert offer.end_at == datetime(2026, 12, 15, tzinfo=UTC)
    assert offer.id == created.id and offer.slug == created.slug
    assert link.destination_url == created_link.destination_url == changed.tracking_url
    assert record.source_hash == record.applied_source_hash == source_hash(changed)
    assert record.applied_content_hash == fingerprint(offer, [link])
    assert record.applied_at == LATER and record.source_changed_at is None
    assert len(db.committed.links) == 1


async def test_successive_refreshes_keep_working_because_the_fingerprint_is_restored() -> (
    None
):
    db = FakeDatabase()
    await run(db, promotion())

    for n in range(3):
        tally = await run(db, promotion(title=f"Title {n}"), now=LATER)
        assert tally.updated == 1

    assert only_offer(db).title == "Title 2"


async def test_a_refresh_does_not_look_at_updated_at() -> None:
    db = FakeDatabase()
    await run(db, promotion())
    db.edit_offer(only_offer(db).id, updated_at=LATER + timedelta(days=9))

    tally = await run(db, promotion(title="Changed at the source"), now=LATER)

    assert tally.updated == 1 and only_offer(db).title == "Changed at the source"


async def test_a_refresh_audits_the_change_without_the_copy() -> None:
    db = FakeDatabase()
    await run(db, promotion())
    db.committed.audits.clear()

    await run(
        db, promotion(title="Secret new title", endDate="2026-12-20T00:00:00.000")
    )

    (audit,) = db.committed.audits
    assert audit["action"] == "admin.offers.update" and audit["source"] == SOURCE
    assert audit["details"]["reason"] == "source_refreshed"
    assert "title" in audit["details"]["fields"]
    assert "Secret new title" not in json.dumps(audit, default=str)


async def test_a_change_that_only_renames_the_advertiser_is_not_a_change_at_all() -> (
    None
):
    db = FakeDatabase()
    await run(db, promotion())
    db.edit_offer(only_offer(db).id, title="Edited by a person")
    before = snapshot(db)
    renamed = promotion(advertiser={"id": 77, "name": "Acme Renamed", "joined": True})

    tally = await run(db, renamed, now=LATER)

    assert tally.unchanged == 1 and snapshot(db) == before
    assert only_import(db).source_changed_at is None


# --- the edit protection -------------------------------------------------------------------

EDITS: dict[str, dict[str, Any]] = {
    "title": {"title": "An editor's headline"},
    "summary": {"summary": "An editor's summary"},
    "description": {"description": ["An editor rewrote this."]},
    "category": {"category_id": "a-real-category"},
    "featured": {"featured": True},
    "freshness": {"freshness": OfferFreshness.POPULAR},
    "verified": {"last_verified_at": T0},
    "end_at": {"end_at": datetime(2027, 1, 1, tzinfo=UTC)},
    "source_url": {"source_url": "https://other.example/"},
    "requires_card": {"requires_card": True},
    "notes": {"publisher_notes": "Checked with the merchant."},
}


@pytest.mark.parametrize("edit", EDITS.values(), ids=EDITS.keys())
async def test_an_offer_an_editor_edited_is_never_overwritten(
    edit: dict[str, Any],
) -> None:
    db = FakeDatabase()
    await run(db, promotion())
    offer_id = only_offer(db).id
    db.edit_offer(offer_id, **edit)
    before = db.committed.offers[offer_id]
    db.committed.audits.clear()
    changed = promotion(
        title="Source rewrote the title", endDate="2026-12-31T00:00:00.000"
    )

    tally = await run(db, changed, now=LATER)

    record = only_import(db)
    assert tally.source_changed == 1 and tally.updated == 0
    assert db.committed.offers[offer_id] == before
    assert record.source_changed_at == LATER
    assert record.source_hash == source_hash(changed)
    assert record.applied_source_hash == source_hash(promotion())
    assert actions(db) == ["admin.offers.source_changed"]


@pytest.mark.parametrize(
    "link_edit",
    [
        {"active": False},
        {"provider": "direct"},
        {"destination_url": "https://www.awin1.com/cread.php?edited=1"},
        {"tracking_id": "campaign-9"},
    ],
)
async def test_an_edited_affiliate_link_protects_the_offer_too(
    link_edit: dict[str, Any],
) -> None:
    db = FakeDatabase()
    await run(db, promotion())
    link = next(iter(db.committed.links.values()))
    db.edit_link(link.id, **link_edit)
    before = (dict(db.committed.offers), dict(db.committed.links))

    tally = await run(db, promotion(title="Source rewrote the title"), now=LATER)

    assert tally.source_changed == 1
    assert (dict(db.committed.offers), dict(db.committed.links)) == before


async def test_an_extra_link_added_by_an_editor_protects_the_offer() -> None:
    db = FakeDatabase()
    await run(db, promotion())
    link = next(iter(db.committed.links.values()))
    db.committed.links["link-extra"] = dataclasses.replace(link, id="link-extra")

    tally = await run(db, promotion(title="Source rewrote the title"), now=LATER)

    assert (
        tally.source_changed == 1 and only_offer(db).title != "Source rewrote the title"
    )


@pytest.mark.parametrize(
    "status", [OfferStatus.PUBLISHED, OfferStatus.EXPIRED, OfferStatus.ARCHIVED]
)
async def test_a_published_expired_or_archived_offer_is_never_overwritten(
    status: OfferStatus,
) -> None:
    db = FakeDatabase()
    await run(db, promotion())
    offer_id = only_offer(db).id
    db.set_status(offer_id, status)
    before = db.committed.offers[offer_id]

    tally = await run(db, promotion(title="Source rewrote the title"), now=LATER)

    assert tally.source_changed == 1
    assert db.committed.offers[offer_id] == before
    assert only_import(db).source_changed_at == LATER


async def test_a_status_change_alone_protects_even_if_a_fingerprint_were_restored() -> (
    None
):
    # Status is checked on its own, not only through the fingerprint.
    db = FakeDatabase()
    await run(db, promotion())
    offer_id = only_offer(db).id
    db.set_status(offer_id, OfferStatus.PUBLISHED)
    record = only_import(db)
    link = next(iter(db.committed.links.values()))
    restored = dataclasses.replace(
        record, applied_content_hash=fingerprint(only_offer(db), [link])
    )
    db.committed.imports[("awin", "1001")] = restored

    tally = await run(db, promotion(title="Source rewrote the title"), now=LATER)

    assert tally.source_changed == 1


async def test_an_edited_offer_with_an_unchanged_source_is_left_alone_and_unmarked() -> (
    None
):
    db = FakeDatabase()
    await run(db, promotion())
    db.edit_offer(only_offer(db).id, title="Edited")
    before = snapshot(db)

    tally = await run(db, promotion(), now=LATER)

    assert tally.unchanged == 1 and snapshot(db) == before


async def test_the_marker_clears_when_the_source_returns_to_what_the_offer_reflects() -> (
    None
):
    db = FakeDatabase()
    await run(db, promotion())
    db.edit_offer(only_offer(db).id, title="Edited")
    await run(db, promotion(title="Moved on"), now=LATER)
    assert only_import(db).source_changed_at == LATER

    tally = await run(db, promotion(), now=LATER + timedelta(days=1))

    record = only_import(db)
    assert tally.unchanged == 1 and record.source_changed_at is None
    assert record.source_hash == record.applied_source_hash


async def test_each_further_change_at_the_source_moves_the_marker_and_is_audited() -> (
    None
):
    db = FakeDatabase()
    await run(db, promotion())
    db.edit_offer(only_offer(db).id, title="Edited")
    db.committed.audits.clear()

    await run(db, promotion(title="Second"), now=LATER)
    await run(db, promotion(title="Third"), now=LATER + timedelta(days=1))

    assert only_import(db).source_changed_at == LATER + timedelta(days=1)
    assert actions(db) == ["admin.offers.source_changed"] * 2
    assert only_offer(db).title == "Edited"


# --- audit ---------------------------------------------------------------------------------


async def test_a_creation_is_audited_by_the_command_without_urls_copy_or_codes() -> (
    None
):
    db = FakeDatabase()

    await run(db, promotion())

    assert actions(db) == [
        "admin.categories.create",
        "admin.merchants.create",
        "admin.offers.create",
        "admin.offer_links.create",
    ]
    assert {a["source"] for a in db.committed.audits} == {SOURCE}
    flat = json.dumps(db.committed.audits, default=str)
    for forbidden in ("https://", "awinaffid", CODE, "10% off", "Save 10%", "terms"):
        assert forbidden not in flat
    link_audit = db.committed.audits[-1]
    assert link_audit["details"]["destination_host"] == "www.awin1.com"
    assert link_audit["after"] == {"provider": "awin", "active": True}


async def test_a_second_run_writes_no_audit_rows() -> None:
    db = FakeDatabase()
    await run(db, promotion())
    audits = len(db.committed.audits)

    await run(db, promotion(), now=LATER)

    assert len(db.committed.audits) == audits


async def test_a_batch_that_fails_part_way_commits_nothing() -> None:
    db = FakeDatabase(fail_insert_offer_at=2)

    with pytest.raises(RuntimeError):
        await run(db, promotion(1), promotion(2), promotion(3))

    assert db.committed.offers == {} and db.committed.audits == []
    assert db.commits == 0


def test_a_tally_adds_up() -> None:
    first = ImportTally(created=1, skipped={"a": 1})
    second = ImportTally(updated=2, skipped={"a": 2, "b": 1})

    merged = first.merged(second)

    assert merged.created == 1 and merged.updated == 2
    assert merged.skipped == {"a": 3, "b": 1}
    assert merged.processed == 7 and merged.skipped_total == 4


async def test_an_offer_an_editor_reverted_is_refreshed_again_with_the_pending_source() -> (
    None
):
    db = FakeDatabase()
    await run(db, promotion())
    created = only_offer(db)
    db.edit_offer(created.id, title="Edited")
    moved = promotion(title="Moved on at the source")
    await run(db, moved, now=LATER)
    assert only_import(db).source_changed_at == LATER
    db.committed.offers[created.id] = created  # the editor undoes every edit

    tally = await run(db, moved, now=LATER + timedelta(days=1))

    assert tally.updated == 1 and only_offer(db).title == "Moved on at the source"
    assert only_import(db).source_changed_at is None


async def test_a_known_source_change_is_not_noted_or_audited_again_on_every_run() -> (
    None
):
    db = FakeDatabase()
    await run(db, promotion())
    db.edit_offer(only_offer(db).id, title="Edited")
    moved = promotion(title="Moved on")
    await run(db, moved, now=LATER)
    before = snapshot(db)

    tally = await run(db, moved, now=LATER + timedelta(days=1))

    assert tally.unchanged == 1 and snapshot(db) == before


async def test_a_changed_tracking_url_is_never_written_over_an_untouched_draft() -> (
    None
):
    db = FakeDatabase()
    await run(db, promotion())
    db.committed.audits.clear()
    offers, links = dict(db.committed.offers), dict(db.committed.links)
    moved = promotion(urlTracking=tracking_for(1001, clickref2="1"))

    tally = await run(db, moved, now=LATER)

    assert tally.source_changed == 1 and tally.updated == 0
    assert dict(db.committed.offers) == offers and dict(db.committed.links) == links
    record = only_import(db)
    assert record.source_changed_at == LATER and record.source_hash == source_hash(
        moved
    )
    assert actions(db) == ["admin.offers.source_changed"]


async def test_the_pending_tracking_change_is_neither_refreshed_nor_audited_again() -> (
    None
):
    db = FakeDatabase()
    await run(db, promotion())
    moved = promotion(urlTracking=tracking_for(1001, clickref2="1"))
    await run(db, moved, now=LATER)
    before = snapshot(db)

    tally = await run(db, moved, now=LATER + timedelta(days=1))

    assert tally.unchanged == 1 and snapshot(db) == before


async def test_a_text_change_still_refreshes_an_untouched_draft_when_the_link_is_unchanged() -> (
    None
):
    db = FakeDatabase()
    await run(db, promotion())
    link = next(iter(db.committed.links.values()))

    tally = await run(db, promotion(title="Text only change"), now=LATER)

    assert tally.updated == 1 and only_offer(db).title == "Text only change"
    assert next(iter(db.committed.links.values())) == link


async def test_a_tracking_change_together_with_text_is_held_back_whole() -> None:
    db = FakeDatabase()
    await run(db, promotion())
    title = only_offer(db).title

    tally = await run(
        db,
        promotion(title="New text", urlTracking=tracking_for(1001, clickref2="1")),
        now=LATER,
    )

    assert tally.source_changed == 1 and only_offer(db).title == title


async def test_a_promotion_that_moves_to_another_advertiser_is_not_refreshed_under_the_old_merchant() -> (
    None
):
    db = FakeDatabase()
    await run(db, promotion())
    offers = dict(db.committed.offers)
    moved = promotion(
        advertiser={"id": 78, "name": "Other Brand", "joined": True},
        urlTracking=(
            "https://www.awin1.com/cread.php?awinmid=78&awinaffid=424242"
            "&ued=https%3A%2F%2Fwww.acme.example%2Fsale-1001"
        ),
        title="Same promotion, new advertiser",
    )

    tally = await run(db, moved, now=LATER)

    assert tally.source_changed == 1 and tally.updated == 0
    assert dict(db.committed.offers) == offers
    assert (
        len(db.committed.merchants) == 1
    )  # no merchant was made for the new advertiser


def test_the_fingerprint_fields_are_pinned_so_a_new_editable_field_is_a_deliberate_change() -> (
    None
):
    from app.services.awin_offer_mapping import FINGERPRINT_FIELDS, FINGERPRINT_VERSION

    assert FINGERPRINT_VERSION == 1
    assert FINGERPRINT_FIELDS == (
        "availability",
        "category_id",
        "description",
        "eligibility",
        "end_at",
        "featured",
        "freshness",
        "image_alt",
        "image_label",
        "image_tint",
        "last_verified_at",
        "merchant_id",
        "offer_type",
        "postage_amount_minor",
        "postage_currency",
        "postage_kind",
        "publisher_notes",
        "requires_card",
        "source_url",
        "start_at",
        "status",
        "steps",
        "subscription_terms",
        "summary",
        "title",
    )


async def test_a_changed_source_url_is_held_back_and_never_written_in_place() -> None:
    db = FakeDatabase()
    await run(db, promotion())
    db.committed.audits.clear()
    offers, links = dict(db.committed.offers), dict(db.committed.links)
    moved = promotion(url="https://www.acme.example/a-different-page")

    tally = await run(db, moved, now=LATER)

    assert tally.source_changed == 1 and tally.updated == 0
    assert dict(db.committed.offers) == offers and dict(db.committed.links) == links
    assert only_import(db).source_changed_at == LATER
    assert actions(db) == ["admin.offers.source_changed"]


async def test_a_changed_source_url_with_a_pending_marker_is_not_audited_again() -> (
    None
):
    db = FakeDatabase()
    await run(db, promotion())
    moved = promotion(url="https://www.acme.example/a-different-page")
    await run(db, moved, now=LATER)
    before = snapshot(db)

    tally = await run(db, moved, now=LATER + timedelta(days=1))

    assert tally.unchanged == 1 and snapshot(db) == before
