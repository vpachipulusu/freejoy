"""Promotion to offer, link and merchant values; slugs; and the fingerprint that detects human edits."""

import dataclasses
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest

from app.core.slugs import validate_catalogue_slug
from app.models.platform.offer import Offer, OfferStatus, OfferType, PostageKind
from app.repositories.admin_offers import AffiliateLinkRecord, OfferRecord
from app.services.awin_offer_mapping import (
    IMPORT_CATEGORY_SLUG,
    PROVIDER,
    import_category_values,
    link_values,
    merchant_slug_candidates,
    merchant_values,
    offer_slug_candidates,
    offer_values,
    refresh_values,
)
from app.services.awin_promotions import Promotion
from app.services.offer_fingerprint import fingerprint
from app.services.offer_rules import EDITABLE_FIELDS

START = datetime(2026, 11, 1, tzinfo=UTC)
END = datetime(2026, 12, 1, tzinfo=UTC)
TRACKING = "https://www.awin1.com/cread.php?awinmid=77&awinaffid=424242"


def make_promotion(**changes: Any) -> Promotion:
    base = Promotion(
        promotion_id=1001,
        kind="voucher",
        advertiser_id=77,
        advertiser_name="Acme Stores",
        title="10% off everything",
        summary="Save 10% on all orders.",
        description=("Save 10% on all orders.", "Ends soon."),
        terms="New customers only.",
        start_at=START,
        end_at=END,
        source_url="https://www.acme.example/sale",
        tracking_url=TRACKING,
        has_voucher_code=True,
    )
    return dataclasses.replace(base, **changes)


def make_offer(**changes: Any) -> OfferRecord:
    values = offer_values(make_promotion(), "cat-1", "mer-1", "10-off-1001")
    base = OfferRecord(
        id="offer-1",
        created_at=START,
        updated_at=START,
        last_verified_at=None,
        **{k: v for k, v in values.items() if k not in ("last_verified_at",)},
    )
    return dataclasses.replace(base, **changes)


def make_link(**changes: Any) -> AffiliateLinkRecord:
    base = AffiliateLinkRecord(
        id="link-1",
        offer_id="offer-1",
        created_at=START,
        updated_at=START,
        **link_values(make_promotion()),
    )
    return dataclasses.replace(base, **changes)


# --- offer values --------------------------------------------------------------------------


def test_an_import_is_always_a_draft_that_is_neither_verified_nor_featured() -> None:
    values = offer_values(make_promotion(), "cat-1", "mer-1", "slug-1")

    assert values["status"] is OfferStatus.DRAFT
    assert values["featured"] is False
    assert values.get("last_verified_at") is None
    assert values.get("freshness") is None


@pytest.mark.parametrize("kind", ["voucher", "promotion"])
def test_both_kinds_map_to_the_voucher_type_and_never_to_a_free_sample(
    kind: str,
) -> None:
    values = offer_values(make_promotion(kind=kind), "c", "m", "s")

    assert values["offer_type"] is OfferType.VOUCHER
    assert values["offer_type"] is not OfferType.FREE_SAMPLE


def test_nothing_is_claimed_about_postage_or_cards() -> None:
    values = offer_values(make_promotion(), "c", "m", "s")

    assert values["postage_kind"] is PostageKind.CHECK_TERMS
    assert values.get("postage_amount_minor") is None
    assert values.get("postage_currency") is None
    assert values["requires_card"] is False


def test_the_values_are_exactly_columns_the_offer_model_has() -> None:
    values = offer_values(make_promotion(), "cat-1", "mer-1", "slug-1")

    offer = Offer(**values)  # an unknown column name raises

    assert offer.title == "10% off everything"
    assert offer.category_id == "cat-1" and offer.merchant_id == "mer-1"
    assert offer.slug == "slug-1"


def test_the_content_comes_from_the_promotion() -> None:
    values = offer_values(make_promotion(), "c", "m", "s")

    assert values["title"] == "10% off everything"
    assert values["summary"] == "Save 10% on all orders."
    assert values["description"] == ["Save 10% on all orders.", "Ends soon."]
    assert values["source_url"] == "https://www.acme.example/sale"
    assert values["start_at"] == START and values["end_at"] == END
    assert values["eligibility"] == "New customers only."


def test_missing_terms_leave_a_neutral_eligibility_not_a_blank() -> None:
    values = offer_values(make_promotion(terms=None), "c", "m", "s")

    assert values["eligibility"].strip()
    assert "terms" in values["eligibility"].lower()


def test_every_required_text_column_is_filled_within_the_admin_limits() -> None:
    values = offer_values(
        make_promotion(advertiser_name="N" * 120, title="T" * 200, terms="E" * 500),
        "c",
        "m",
        "s",
    )

    for column in (
        "title",
        "summary",
        "image_tint",
        "image_label",
        "image_alt",
        "eligibility",
        "availability",
    ):
        assert values[column].strip(), column
    assert len(values["title"]) <= 200 and len(values["summary"]) <= 300
    assert len(values["image_label"]) <= 40 and len(values["image_alt"]) <= 200
    assert len(values["eligibility"]) <= 500 and len(values["availability"]) <= 500
    assert len(values["publisher_notes"]) <= 2000


def notes_for(**changes: Any) -> str:
    return offer_values(make_promotion(**changes), "c", "m", "s")["publisher_notes"]


def test_publisher_notes_record_the_origin_and_the_awin_type() -> None:
    voucher = notes_for(kind="voucher", has_voucher_code=True)
    no_code = notes_for(kind="voucher", has_voucher_code=False)
    promotion = notes_for(kind="promotion", has_voucher_code=False)

    assert "1001" in voucher and "Awin" in voucher
    assert "Awin type: voucher" in voucher and "not stored" in voucher
    assert "Awin type: voucher (no code listed); choose the offer type." in no_code
    assert "Awin type: promotion (no code); choose the offer type." in promotion
    assert "code" not in promotion.replace("no code", "").lower()


def test_the_image_alt_text_does_not_call_every_offer_a_voucher() -> None:
    values = offer_values(make_promotion(kind="promotion"), "c", "m", "s")

    assert "voucher" not in values["image_alt"].lower()
    assert values["image_alt"] == "Illustration of an offer"


def test_the_image_label_is_not_cleaned_twice() -> None:
    plain_name = "&lt;b&gt;"  # what plain-text cleaning leaves of an advertiser called "&amp;lt;b&amp;gt;"
    values = offer_values(make_promotion(advertiser_name=plain_name), "c", "m", "s")

    assert values["image_label"] == plain_name
    again = offer_values(
        make_promotion(advertiser_name=values["image_label"]), "c", "m", "s"
    )
    assert again["image_label"] == values["image_label"]


def test_a_refresh_rewrites_only_the_imported_fields() -> None:
    values = refresh_values(make_promotion(title="New title"))

    assert set(values) == {
        "title",
        "summary",
        "description",
        "offer_type",
        "source_url",
        "start_at",
        "end_at",
        "eligibility",
        "publisher_notes",
    }
    assert values["title"] == "New title"
    assert set(values) <= EDITABLE_FIELDS


# --- link, merchant, category --------------------------------------------------------------


def test_the_tracking_url_becomes_an_active_awin_link() -> None:
    values = link_values(make_promotion())

    assert values == {
        "provider": PROVIDER,
        "destination_url": TRACKING,
        "tracking_id": None,
        "active": True,
    }


def test_a_merchant_is_created_inactive_with_the_advertiser_name() -> None:
    values = merchant_values(make_promotion(), "acme-stores")

    assert values["name"] == "Acme Stores" and values["slug"] == "acme-stores"
    assert values["active"] is False
    assert values["logo_url"] is None


@pytest.mark.parametrize(
    "url", [None, "https://www.acme.example/sale", "https://www.awin1.com/cread.php"]
)
def test_an_imported_merchant_has_no_website_the_editor_sets_it(
    url: str | None,
) -> None:
    values = merchant_values(make_promotion(source_url=url), "acme-stores-77")

    assert values["website"] is None


def test_the_import_category_can_never_be_published_into() -> None:
    values = import_category_values()

    assert values["slug"] == IMPORT_CATEGORY_SLUG
    assert values["enabled"] is False
    assert "uncategorised" in values["name"].lower()
    assert values["name"].strip() and values["intro"].strip() and values["tint"]


# --- slugs ---------------------------------------------------------------------------------


def test_the_offer_slug_ends_in_the_promotion_id_and_is_valid() -> None:
    first, fallback = offer_slug_candidates(make_promotion())

    assert first == "10-off-everything-1001"
    assert fallback == "awin-1001"
    for slug in (first, fallback):
        assert validate_catalogue_slug(slug) == slug


def test_the_offer_slug_is_the_same_every_time_for_the_same_promotion() -> None:
    assert offer_slug_candidates(make_promotion()) == offer_slug_candidates(
        make_promotion()
    )


def test_two_promotions_with_one_title_get_different_slugs() -> None:
    a = offer_slug_candidates(make_promotion(promotion_id=1))
    b = offer_slug_candidates(make_promotion(promotion_id=2))

    assert set(a).isdisjoint(b)


@pytest.mark.parametrize("title", ["!!!", "★★", "A", "T" * 200, "café über"])
def test_awkward_titles_still_give_valid_slugs_within_the_limit(title: str) -> None:
    for slug in offer_slug_candidates(make_promotion(title=title)):
        assert validate_catalogue_slug(slug) == slug
        assert len(slug) <= 80


def test_a_title_with_no_letters_falls_back_to_the_id_only() -> None:
    assert offer_slug_candidates(make_promotion(title="!!!")) == ("awin-1001",)


def test_merchant_slugs_always_carry_the_advertiser_id() -> None:
    plain = merchant_slug_candidates(make_promotion())
    odd = merchant_slug_candidates(make_promotion(advertiser_name="!!!"))

    assert plain == ("acme-stores-77", "awin-advertiser-77")
    assert odd == ("awin-advertiser-77",)
    assert "acme-stores" not in plain  # an import never reserves a bare brand slug
    for slug in (*plain, *odd):
        assert validate_catalogue_slug(slug) == slug


def test_a_very_long_advertiser_name_still_gives_a_valid_suffixed_slug() -> None:
    (slug, _) = merchant_slug_candidates(make_promotion(advertiser_name="long " * 60))

    assert slug.endswith("-77") and len(slug) <= 80
    assert validate_catalogue_slug(slug) == slug


# --- the fingerprint -----------------------------------------------------------------------


def test_the_fingerprint_is_a_stable_sha256_of_the_offer_and_its_links() -> None:
    first = fingerprint(make_offer(), [make_link()])

    assert first == fingerprint(make_offer(), [make_link()])
    assert len(first) == 64 and int(first, 16) >= 0


@pytest.mark.parametrize(
    "field", sorted(EDITABLE_FIELDS | {"status", "last_verified_at"})
)
def test_any_field_an_editor_can_change_changes_the_fingerprint(field: str) -> None:
    changed: dict[str, Any] = {
        "title": "Edited",
        "summary": "Edited",
        "description": ["Edited"],
        "category_id": "cat-2",
        "merchant_id": "mer-2",
        "offer_type": OfferType.FREE_SAMPLE,
        "featured": True,
        "image_tint": "mint",
        "image_label": "Edited",
        "image_alt": "Edited",
        "source_url": "https://other.example/",
        "postage_kind": PostageKind.FREE,
        "postage_amount_minor": 100,
        "postage_currency": "GBP",
        "requires_card": True,
        "subscription_terms": "Edited",
        "eligibility": "Edited",
        "availability": "Edited",
        "steps": [{"title": "t", "detail": "d"}],
        "start_at": START + timedelta(days=1),
        "end_at": END + timedelta(days=1),
        "freshness": None,
        "publisher_notes": "Edited",
        "status": OfferStatus.PUBLISHED,
        "last_verified_at": START,
    }
    offer = make_offer(freshness=None)
    if field == "freshness":
        from app.models.platform.offer import OfferFreshness

        changed["freshness"] = OfferFreshness.POPULAR

    edited = dataclasses.replace(offer, **{field: changed[field]})

    assert fingerprint(edited, [make_link()]) != fingerprint(offer, [make_link()])


def test_bookkeeping_columns_do_not_change_the_fingerprint() -> None:
    offer = make_offer()
    touched = dataclasses.replace(
        offer, updated_at=END, created_at=END, id="other", slug="another-slug"
    )

    assert fingerprint(touched, [make_link()]) == fingerprint(offer, [make_link()])


def test_the_same_instant_in_another_time_zone_is_the_same_fingerprint() -> None:
    offer = make_offer()
    shifted = dataclasses.replace(
        offer, start_at=START.astimezone(timezone(timedelta(hours=5)))
    )

    assert fingerprint(shifted, [make_link()]) == fingerprint(offer, [make_link()])


@pytest.mark.parametrize(
    "links",
    [
        [],
        [make_link(destination_url="https://www.awin1.com/other")],
        [make_link(active=False)],
        [make_link(provider="direct")],
        [make_link(tracking_id="t1")],
        [make_link(), make_link(id="link-2")],
    ],
)
def test_a_change_to_the_links_changes_the_fingerprint(
    links: list[AffiliateLinkRecord],
) -> None:
    assert fingerprint(make_offer(), links) != fingerprint(make_offer(), [make_link()])


def test_link_ids_and_timestamps_do_not_change_the_fingerprint() -> None:
    renamed = make_link(id="zzz", updated_at=END, created_at=END)

    assert fingerprint(make_offer(), [renamed]) == fingerprint(
        make_offer(), [make_link()]
    )
