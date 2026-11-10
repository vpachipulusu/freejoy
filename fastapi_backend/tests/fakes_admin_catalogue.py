"""In-memory doubles for the admin catalogue stores, audit recorder and session."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.ids import new_id
from app.core.permissions import AdminContext
from app.models.platform.offer import (
    CommercialBasis,
    OfferStatus,
    OfferType,
    PostageKind,
)
from app.repositories.admin_categories import CategoryPage, CategoryRecord
from app.repositories.admin_merchants import MerchantPage, MerchantRecord
from app.repositories.admin_offers import (
    AffiliateLinkRecord,
    OfferFilters,
    OfferPage,
    OfferProvenance,
    OfferRecord,
    ReviewRefs,
)

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
LATER = NOW + timedelta(days=30)
EARLIER = NOW - timedelta(days=1)


class FakeAuditor:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    async def record(self, admin: AdminContext, **fields: Any) -> None:
        self.rows.append({"actor": admin.user_id, **fields})


class FakeSession:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


def category(category_id: str = "cat1", *, enabled: bool = True) -> CategoryRecord:
    return CategoryRecord(
        id=category_id,
        name=category_id.title(),
        slug=category_id,
        short_name=category_id.title(),
        description="Desc",
        intro="Intro",
        tint="sage",
        sort_order=0,
        enabled=enabled,
        created_at=NOW,
        updated_at=NOW,
    )


def merchant(merchant_id: str = "mer1", *, active: bool = True) -> MerchantRecord:
    return MerchantRecord(
        id=merchant_id,
        name=merchant_id.title(),
        slug=merchant_id,
        website=None,
        logo_url=None,
        active=active,
        created_at=NOW,
        updated_at=NOW,
    )


def offer(offer_id: str = "off1", **changes: Any) -> OfferRecord:
    base = OfferRecord(
        id=offer_id,
        title=f"Offer {offer_id}",
        slug=offer_id,
        summary="Summary",
        description=[],
        category_id="cat1",
        merchant_id="mer1",
        offer_type=OfferType.FREE_SAMPLE,
        commercial_basis=CommercialBasis.EDITORIAL,
        status=OfferStatus.DRAFT,
        featured=False,
        image_tint="sage",
        image_label="Free",
        image_alt="Alt",
        source_url=None,
        postage_kind=PostageKind.FREE,
        postage_amount_minor=None,
        postage_currency=None,
        requires_card=False,
        subscription_terms=None,
        eligibility="UK",
        availability="Today",
        steps=[],
        start_at=None,
        end_at=None,
        last_verified_at=None,
        freshness=None,
        publisher_notes=None,
        created_at=NOW,
        updated_at=NOW,
    )
    return replace(base, **changes)


def provenance(
    *, source_changed_at: datetime | None = None, provider: str = "awin"
) -> OfferProvenance:
    return OfferProvenance(
        provider=provider,
        external_id="1001",
        applied_at=EARLIER,
        source_changed_at=source_changed_at,
    )


def link(
    link_id: str,
    offer_id: str = "off1",
    *,
    active: bool = True,
    provider: str = "awin",
) -> AffiliateLinkRecord:
    return AffiliateLinkRecord(
        id=link_id,
        offer_id=offer_id,
        provider=provider,
        destination_url="https://shop.example.com/go",
        tracking_id=None,
        active=active,
        created_at=NOW,
        updated_at=NOW,
    )


class FakeOfferStore:
    def __init__(self) -> None:
        self.offers: dict[str, OfferRecord] = {}
        self.links: dict[str, AffiliateLinkRecord] = {}
        self.categories = {"cat1": True}
        self.merchants = {"mer1": True}
        self.websites: dict[str, str | None] = {"mer1": "https://www.acme.example"}
        self.merchant_names: dict[str, str] = {}
        self.merchant_logos: dict[str, str | None] = {}
        self.category_names: dict[str, str] = {}
        self.category_short_names: dict[str, str] = {}
        self.locked: list[str] = []
        self.filters: list[tuple[OfferFilters, int, int]] = []
        self.provenance: dict[str, OfferProvenance] = {}
        self.provenance_locked: list[str] = []

    async def list_offers(
        self, filters: OfferFilters, limit: int, offset: int
    ) -> OfferPage:
        self.filters.append((filters, limit, offset))
        rows = list(self.offers.values())
        page = rows[offset : offset + limit]
        found = {i.id: self.provenance[i.id] for i in page if i.id in self.provenance}
        return OfferPage(page, len(rows), found)

    async def get_provenance(
        self, offer_id: str, *, lock: bool = False
    ) -> OfferProvenance | None:
        if lock:
            self.provenance_locked.append(offer_id)
        return self.provenance.get(offer_id)

    async def clear_source_change(self, offer_id: str) -> None:
        self.provenance[offer_id] = replace(
            self.provenance[offer_id], source_changed_at=None
        )

    async def get(self, offer_id: str, *, lock: bool = False) -> OfferRecord | None:
        if lock:
            self.locked.append(offer_id)
        return self.offers.get(offer_id)

    async def slug_exists(self, slug: str) -> bool:
        return any(item.slug == slug for item in self.offers.values())

    async def insert(self, values: dict[str, Any]) -> OfferRecord:
        record = replace(offer(new_id()), **values)
        self.offers[record.id] = record
        return record

    async def update(self, offer_id: str, values: dict[str, Any]) -> OfferRecord:
        self.offers[offer_id] = replace(self.offers[offer_id], **values)
        return self.offers[offer_id]

    async def category_enabled(self, category_id: str) -> bool | None:
        return self.categories.get(category_id)

    async def merchant_active(self, merchant_id: str) -> bool | None:
        return self.merchants.get(merchant_id)

    async def merchant_website(self, merchant_id: str) -> str | None:
        return self.websites.get(merchant_id)

    async def list_links(self, offer_id: str) -> list[AffiliateLinkRecord]:
        return [item for item in self.links.values() if item.offer_id == offer_id]

    async def review_refs(
        self, category_id: str, merchant_id: str, *, share: bool = False
    ) -> ReviewRefs:
        enabled = self.categories.get(category_id)
        active = self.merchants.get(merchant_id)
        return ReviewRefs(
            category_name=(
                None
                if enabled is None
                else self.category_names.get(category_id, category_id)
            ),
            category_short_name=(
                None
                if enabled is None
                else self.category_short_names.get(category_id, category_id)
            ),
            category_enabled=enabled,
            merchant_name=(
                None
                if active is None
                else self.merchant_names.get(merchant_id, merchant_id)
            ),
            merchant_website=self.websites.get(merchant_id),
            merchant_logo_url=self.merchant_logos.get(merchant_id),
            merchant_active=active,
        )

    async def count_active_links(self, offer_id: str) -> int:
        return sum(1 for item in await self.list_links(offer_id) if item.active)

    async def get_link(
        self, offer_id: str, link_id: str, *, lock: bool = False
    ) -> AffiliateLinkRecord | None:
        found = self.links.get(link_id)
        return found if found and found.offer_id == offer_id else None

    async def insert_link(self, values: dict[str, Any]) -> AffiliateLinkRecord:
        record = replace(link(new_id()), **values)
        self.links[record.id] = record
        return record

    async def update_link(
        self, link_id: str, values: dict[str, Any]
    ) -> AffiliateLinkRecord:
        self.links[link_id] = replace(self.links[link_id], **values)
        return self.links[link_id]


class FakeCategoryStore:
    def __init__(self, *items: CategoryRecord) -> None:
        self.items = {item.id: item for item in items}

    async def list_categories(
        self, search: str | None, enabled: bool | None, limit: int, offset: int
    ) -> CategoryPage:
        rows = [
            i for i in self.items.values() if enabled is None or i.enabled is enabled
        ]
        return CategoryPage(rows[offset : offset + limit], len(rows))

    async def get(
        self, category_id: str, *, lock: bool = False
    ) -> CategoryRecord | None:
        return self.items.get(category_id)

    async def slug_exists(self, slug: str) -> bool:
        return any(item.slug == slug for item in self.items.values())

    async def insert(self, values: dict[str, Any]) -> CategoryRecord:
        record = replace(category(new_id()), **values)
        self.items[record.id] = record
        return record

    async def update(self, category_id: str, values: dict[str, Any]) -> CategoryRecord:
        self.items[category_id] = replace(self.items[category_id], **values)
        return self.items[category_id]


class FakeMerchantStore:
    def __init__(self, *items: MerchantRecord) -> None:
        self.items = {item.id: item for item in items}
        self.direct_links: dict[str, list[str]] = {}

    async def active_direct_link_ids(self, merchant_id: str) -> list[str]:
        return self.direct_links.get(merchant_id, [])

    async def list_merchants(
        self, search: str | None, active: bool | None, limit: int, offset: int
    ) -> MerchantPage:
        rows = [i for i in self.items.values() if active is None or i.active is active]
        return MerchantPage(rows[offset : offset + limit], len(rows))

    async def get(
        self, merchant_id: str, *, lock: bool = False
    ) -> MerchantRecord | None:
        return self.items.get(merchant_id)

    async def slug_exists(self, slug: str) -> bool:
        return any(item.slug == slug for item in self.items.values())

    async def insert(self, values: dict[str, Any]) -> MerchantRecord:
        record = replace(merchant(new_id()), **values)
        self.items[record.id] = record
        return record

    async def update(self, merchant_id: str, values: dict[str, Any]) -> MerchantRecord:
        self.items[merchant_id] = replace(self.items[merchant_id], **values)
        return self.items[merchant_id]
