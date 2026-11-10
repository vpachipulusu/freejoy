"""Test doubles for the catalogue: record builders, a fake repository and a recording session."""

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from app.models.platform.offer import (
    CommercialBasis,
    OfferFreshness,
    OfferStatus,
    OfferType,
    PostageKind,
)
from app.repositories.catalogue import CategoryRecord, OfferFilters, OfferRecord
from tests.fakes import FakeSession

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


def make_offer(**overrides: Any) -> OfferRecord:
    base = OfferRecord(
        id="offer_1",
        slug="free-shampoo",
        title="Free shampoo sample",
        summary="A travel size bottle, posted free.",
        description=["First paragraph.", "Second paragraph."],
        category_slug="beauty",
        category_name="Beauty",
        merchant_slug="acme",
        merchant_name="Acme",
        merchant_logo_url=None,
        offer_type=OfferType.FREE_SAMPLE,
        commercial_basis=CommercialBasis.EDITORIAL,
        status=OfferStatus.PUBLISHED,
        featured=False,
        image_tint="blush",
        image_label="soft & kind",
        image_alt="A bottle of shampoo",
        postage_kind=PostageKind.FREE,
        postage_amount_minor=None,
        postage_currency=None,
        requires_card=False,
        subscription_terms=None,
        eligibility="UK residents",
        availability="While stocks last",
        start_at=datetime(2026, 5, 1, tzinfo=UTC),
        end_at=None,
        last_verified_at=datetime(2026, 5, 30, tzinfo=UTC),
        freshness=OfferFreshness.POPULAR,
        steps=[{"title": "Sign up", "detail": "Fill in the form."}],
    )
    return replace(base, **overrides)


def make_category(**overrides: Any) -> CategoryRecord:
    base = CategoryRecord(
        slug="beauty",
        name="Beauty",
        short_name="Beauty",
        description="Beauty freebies",
        intro="Treat yourself.",
        tint="blush",
        sort_order=1,
        offer_count=3,
    )
    return replace(base, **overrides)


class FakeCatalogueRepository:
    """Canned answers that also remember how they were asked."""

    def __init__(
        self,
        offers: list[OfferRecord] | None = None,
        total: int | None = None,
        by_slug: OfferRecord | None = None,
        by_id: OfferRecord | None = None,
        categories: list[CategoryRecord] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.offers = offers or []
        self.total = len(self.offers) if total is None else total
        self.by_slug = by_slug
        self.by_id = by_id
        self.categories = categories or []
        self.error = error
        self.list_calls: list[tuple[OfferFilters, datetime, int, int]] = []
        self.slugs: list[str] = []
        self.ids: list[str] = []
        self.category_now: list[datetime] = []

    async def list_live_offers(
        self, filters: OfferFilters, now: datetime, limit: int, offset: int
    ) -> tuple[int, list[OfferRecord]]:
        if self.error:
            raise self.error
        self.list_calls.append((filters, now, limit, offset))
        return self.total, self.offers

    async def find_visible_offer(self, slug: str) -> OfferRecord | None:
        if self.error:
            raise self.error
        self.slugs.append(slug)
        return self.by_slug

    async def find_visible_offer_by_id(self, offer_id: str) -> OfferRecord | None:
        if self.error:
            raise self.error
        self.ids.append(offer_id)
        return self.by_id

    async def list_enabled_categories(self, now: datetime) -> list[CategoryRecord]:
        if self.error:
            raise self.error
        self.category_now.append(now)
        return self.categories


class _Mappings:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def all(self) -> list[dict[str, Any]]:
        return self._rows

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None


class _Rows:
    def __init__(self, rows: list[dict[str, Any]], scalar: Any) -> None:
        self._rows = rows
        self._scalar = scalar

    def scalar_one(self) -> Any:
        return self._scalar

    def mappings(self) -> _Mappings:
        return _Mappings(self._rows)


class RowsSession(FakeSession):
    """Keeps each statement object (to compile later) and answers from a queue."""

    def __init__(self, *answers: tuple[list[dict[str, Any]], Any]) -> None:
        super().__init__()
        self.executed: list[Any] = []
        self._answers = list(answers)

    async def execute(self, statement: Any) -> Any:  # type: ignore[override]
        self.executed.append(statement)
        rows, scalar = self._answers.pop(0) if self._answers else ([], 0)
        return _Rows(rows, scalar)
