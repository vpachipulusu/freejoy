"""The offer fingerprint: stable across releases, and sensitive to exactly what an editor can change.

The importer stores it as ``applied_content_hash``, so its bytes are a contract: a change of the hash for an
unchanged offer would make every untouched import look edited by a person.
"""

import dataclasses
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest

from app.models.platform.offer import (
    OfferFreshness,
    OfferStatus,
    OfferType,
    PostageKind,
)
from app.repositories.admin_offers import AffiliateLinkRecord, OfferRecord
from app.services.offer_fingerprint import (
    FINGERPRINT_FIELDS,
    FINGERPRINT_VERSION,
    fingerprint,
)
from app.services.offer_rules import EDITABLE_FIELDS
from tests.services.test_awin_offer_mapping import END, START, make_link, make_offer

# What commit 369ae6c (before the fingerprint moved to its own module) computes for KNOWN_OFFER and KNOWN_LINKS.
KNOWN_DIGEST = "7831a024ad3fb424e92598abadb9ad7a2675a4afc24604c426f1ac32d35b9677"

KNOWN_OFFER = OfferRecord(
    id="off_known",
    title="Gratis Käsekuchen für alle 🧀",
    slug="gratis-kasekuchen",
    summary="Ein Stück Kuchen – kostenlos.",
    description=["Erster Absatz.", "Zweiter Absatz mit Ünïcödé."],
    category_id="cat_known",
    merchant_id="mer_known",
    offer_type=OfferType.FREE_SAMPLE,
    status=OfferStatus.DRAFT,
    featured=True,
    image_tint="sage",
    image_label="Käse",
    image_alt="Ein Kuchen",
    source_url="https://shop.example.com/kuchen?ref=1",
    postage_kind=PostageKind.COST,
    postage_amount_minor=199,
    postage_currency="GBP",
    requires_card=True,
    subscription_terms="Danach 9,99 € im Monat",
    eligibility="Nur für UK",
    availability="Solange der Vorrat reicht",
    steps=[
        {"title": "Öffnen", "detail": "Seite öffnen"},
        {"title": "Klicken", "detail": "Knopf"},
    ],
    start_at=datetime(2026, 3, 1, 9, 30, tzinfo=UTC),
    end_at=datetime(
        2026, 12, 31, 23, 59, 59, tzinfo=timezone(timedelta(hours=5, minutes=30))
    ),
    last_verified_at=datetime(2026, 6, 1, 12, 0, tzinfo=UTC),
    freshness=OfferFreshness.POPULAR,
    publisher_notes="Intern: prüfen",
    created_at=datetime(2026, 1, 1, tzinfo=UTC),
    updated_at=datetime(2026, 2, 1, tzinfo=UTC),
)
# Deliberately out of order: the fingerprint does not depend on the order of the links.
KNOWN_LINKS = [
    AffiliateLinkRecord(
        id="lnk_b",
        offer_id="off_known",
        provider="direct",
        destination_url="https://shop.example.com/kuchen",
        tracking_id=None,
        active=False,
        created_at=datetime(2026, 1, 2, tzinfo=UTC),
        updated_at=datetime(2026, 1, 2, tzinfo=UTC),
    ),
    AffiliateLinkRecord(
        id="lnk_a",
        offer_id="off_known",
        provider="awin",
        destination_url="https://www.awin1.com/cread.php?awinmid=1&awinaffid=2",
        tracking_id="trk-1",
        active=True,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    ),
]


def test_the_fingerprint_of_a_known_offer_is_byte_stable_across_releases() -> None:
    assert fingerprint(KNOWN_OFFER, KNOWN_LINKS) == KNOWN_DIGEST
    assert fingerprint(KNOWN_OFFER, KNOWN_LINKS[::-1]) == KNOWN_DIGEST


def test_a_naive_datetime_is_refused_not_read_as_local_time() -> None:
    naive = dataclasses.replace(KNOWN_OFFER, end_at=datetime(2026, 12, 31, 23, 59))

    with pytest.raises(TypeError):
        fingerprint(naive, KNOWN_LINKS)


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


def test_the_fingerprint_fields_are_pinned_so_a_new_editable_field_is_a_deliberate_change() -> (
    None
):
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
