"""``seed_catalogue`` upsert rules and generated text, against an in-memory fake session.

No database is needed. ``tests/commands/test_seed_catalogue.py`` runs the same rules against
Postgres when ``TEST_DATABASE_URL`` is set.
"""

import re
from collections.abc import Iterable
from datetime import date, timedelta
from typing import Any

import pytest

from app.core.ids import new_id
from app.models.platform import (
    AffiliateLink,
    Category,
    CommercialBasis,
    Merchant,
    Offer,
    OfferType,
    PostageKind,
)
from commands import seed_catalogue
from commands.seed_catalogue import SeedConflict, SeedSummary
from commands.seed_data import SAMPLE_NOTE
from commands.seed_data_offers import OFFERS

TODAY = date(2026, 10, 29)
YEAR = re.compile(r"\b20\d{2}\b")


class FakeSession:
    """Just enough of ``AsyncSession`` for ``seed_catalogue``: preset rows in, adds recorded."""

    def __init__(self, rows: Iterable[Any] = ()) -> None:
        self.rows: list[Any] = list(rows)
        self.added: list[Any] = []

    async def scalars(self, statement: Any) -> list[Any]:
        entity = statement.column_descriptions[0]["entity"]
        return [row for row in self.rows if isinstance(row, entity)]

    def add(self, row: Any) -> None:
        row.id = new_id()
        self.added.append(row)
        self.rows.append(row)

    async def flush(self) -> None:
        return None


async def seed(session: FakeSession, today: date = TODAY) -> SeedSummary:
    return await seed_catalogue.seed_catalogue(session, today=today)  # type: ignore[arg-type]


def of_type[Row](session: FakeSession, model: type[Row]) -> list[Row]:
    return [row for row in session.rows if isinstance(row, model)]


async def test_every_sample_offer_is_editorial_because_nobody_pays_for_it() -> None:
    session = FakeSession()

    await seed(session)

    offers = of_type(session, Offer)
    assert offers and {o.commercial_basis for o in offers} == {
        CommercialBasis.EDITORIAL
    }


async def test_first_run_on_an_empty_catalogue_adds_every_row() -> None:
    session = FakeSession()

    summary = await seed(session)

    assert summary == SeedSummary(
        categories=6, merchants=8, offers=24, affiliate_links=24
    )
    assert len(session.added) == 62
    offers = of_type(session, Offer)
    assert {o.publisher_notes for o in offers} == {SAMPLE_NOTE}
    assert all(o.id for o in session.added)


async def test_second_run_adds_nothing_and_resets_edited_sample_rows() -> None:
    first = FakeSession()
    await seed(first)
    offer = next(o for o in of_type(first, Offer) if o.slug == "coffee-discovery-pack")
    merchant = of_type(first, Merchant)[0]
    category = of_type(first, Category)[0]
    link = next(
        link for link in of_type(first, AffiliateLink) if link.offer_id == offer.id
    )
    offer_id, merchant_name = offer.id, merchant.name
    offer.title, link.destination_url = "Edited", "https://other.invalid/x"
    merchant.name, category.enabled = "Edited (Sample)", False
    second = FakeSession(first.rows)

    summary = await seed(second)

    assert second.added == []
    assert summary == SeedSummary(6, 8, 24, 24)
    assert offer.title == "Coffee discovery pack"
    assert offer.id == offer_id
    assert link.destination_url == "https://example.com/go/coffee-discovery-pack"
    assert merchant.name == merchant_name
    assert category.enabled is False  # categories are insert-only


async def test_second_run_on_a_later_day_moves_the_dates() -> None:
    first = FakeSession()
    await seed(first)
    offer = next(o for o in of_type(first, Offer) if o.slug == "coffee-discovery-pack")
    verified = offer.last_verified_at
    assert verified is not None

    await seed(FakeSession(first.rows), today=TODAY + timedelta(days=2))

    assert offer.last_verified_at == verified + timedelta(days=2)


async def test_a_real_offer_with_a_seed_slug_raises_a_conflict() -> None:
    real = Offer(slug="coffee-discovery-pack", title="Mine", publisher_notes="mine")

    with pytest.raises(SeedConflict) as conflict:
        await seed(FakeSession([real]))

    assert (conflict.value.table, conflict.value.slug) == (
        "offers",
        "coffee-discovery-pack",
    )
    assert real.title == "Mine"


async def test_an_offer_with_the_sample_note_but_a_real_source_raises_a_conflict() -> (
    None
):
    real = Offer(
        slug="coffee-discovery-pack",
        publisher_notes=SAMPLE_NOTE,
        source_url="https://real.invalid/x",
    )

    with pytest.raises(SeedConflict) as conflict:
        await seed(FakeSession([real]))

    assert conflict.value.table == "offers"


@pytest.mark.parametrize(
    ("name", "website"),
    [
        ("Tea Time", "https://example.com/merchants/tea-time"),
        ("Tea Time (Sample)", "https://tea-time.example.org"),
        ("Tea Time (Sample)", None),
        ("Tea Time (Sample)", "https://example.com.evil.invalid/merchants/tea-time"),
        ("Tea Time (Sample)", "https://example.community/merchants/tea-time"),
    ],
)
async def test_a_merchant_that_is_not_marked_as_sample_raises_a_conflict(
    name: str, website: str | None
) -> None:
    real = Merchant(slug="tea-time", name=name, website=website)

    with pytest.raises(SeedConflict) as conflict:
        await seed(FakeSession([real]))

    assert conflict.value.table == "merchants"
    assert real.name == name
    assert real.website == website


async def test_a_sample_merchant_is_updated_not_rejected() -> None:
    first = FakeSession()
    await seed(first)

    await seed(FakeSession(first.rows))

    assert seed_catalogue._is_sample(of_type(first, Merchant)[0])


def test_a_category_is_never_sample_data() -> None:
    category = Category(slug="beauty", name="Beauty")

    assert not seed_catalogue._is_sample(category)


async def test_a_postage_offer_keeps_its_cost_and_currency() -> None:
    session = FakeSession()
    await seed(session)

    costed = [o for o in of_type(session, Offer) if o.postage_kind is PostageKind.COST]

    assert costed
    assert all(o.postage_currency == "GBP" and o.postage_amount_minor for o in costed)
    assert {o.offer_type for o in costed} == {OfferType.FREE_SAMPLE}


# --- availability text is derived from the run date -------------------------------------------


def test_stored_text_holds_no_fixed_year() -> None:
    for offer in OFFERS:
        assert not YEAR.search(offer.availability), offer.slug
        assert not YEAR.search(offer.subscription_terms or ""), offer.slug


def test_every_availability_template_has_an_end_date_to_fill_it() -> None:
    for offer in OFFERS:
        if "{end}" in offer.availability:
            assert offer.end_days is not None, offer.slug


async def test_availability_follows_the_end_date_of_the_run() -> None:
    first, later = FakeSession(), FakeSession()
    await seed(first, TODAY)
    await seed(later, TODAY + timedelta(days=30))
    by_slug = {o.slug: o for o in of_type(first, Offer)}
    later_by_slug = {o.slug: o for o in of_type(later, Offer)}
    templated = [o for o in OFFERS if "{end}" in o.availability]

    assert templated
    for seed_offer in templated:
        before, after = by_slug[seed_offer.slug], later_by_slug[seed_offer.slug]
        for offer in (before, after):
            assert offer.end_at is not None
            assert f"{offer.end_at.day} {offer.end_at:%B %Y}" in offer.availability
            assert "{" not in offer.availability
        assert before.availability != after.availability
