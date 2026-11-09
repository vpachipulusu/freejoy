"""An in-memory catalogue for the Awin importer tests: transactions, provenance, audit rows. No SQL.

``FakeDatabase.unit()`` hands out a unit of work like the real one: a working copy of the committed
state, which becomes the committed state only when the unit's session commits. A unit that ends without
a commit (an exception, or simply not committing) is discarded, so tests can see what a failed batch
leaves behind. The real SQL is covered by ``tests/commands/test_import_awin_offers_e2e.py``.
"""

import dataclasses
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from app.models.platform.offer import OfferStatus
from app.repositories.admin_categories import CategoryRecord
from app.repositories.admin_merchants import MerchantRecord
from app.repositories.admin_offers import AffiliateLinkRecord, OfferRecord
from app.repositories.awin_import import IMPORT_CATEGORY_SLUG, OfferImportRecord

T0 = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)


@dataclass
class State:
    offers: dict[str, OfferRecord] = field(default_factory=dict)
    links: dict[str, AffiliateLinkRecord] = field(default_factory=dict)
    imports: dict[tuple[str, str], OfferImportRecord] = field(default_factory=dict)
    merchants: dict[str, MerchantRecord] = field(default_factory=dict)
    merchant_imports: dict[tuple[str, str], str] = field(default_factory=dict)
    categories: dict[str, CategoryRecord] = field(default_factory=dict)
    audits: list[dict[str, Any]] = field(default_factory=list)
    counter: int = 0
    run_locks: int = 0

    def copy(self) -> "State":
        return dataclasses.replace(
            self,
            offers=dict(self.offers),
            links=dict(self.links),
            imports=dict(self.imports),
            merchants=dict(self.merchants),
            merchant_imports=dict(self.merchant_imports),
            categories=dict(self.categories),
            audits=list(self.audits),
        )

    def next_id(self, prefix: str) -> str:
        self.counter += 1
        return f"{prefix}-{self.counter}"


class FakeSession:
    def __init__(self, database: "FakeDatabase", working: State) -> None:
        self._database, self._working = database, working

    async def commit(self) -> None:
        self._database.commits += 1
        self._database.committed = self._working.copy()


class FakeAuditor:
    def __init__(self, working: State) -> None:
        self._working = working

    async def record_system_action(
        self,
        source: str,
        *,
        action: str,
        resource: str,
        resource_id: str | None = None,
        before: Any = None,
        after: Any = None,
        details: Any = None,
    ) -> None:
        self._working.audits.append(
            {
                "source": source,
                "action": action,
                "resource": resource,
                "resource_id": resource_id,
                "before": before,
                "after": after,
                "details": details,
            }
        )


class FakeImportStore:
    """The importer's store over a working ``State``; ``fail_insert_offer_at`` raises on that insert."""

    def __init__(self, working: State, database: "FakeDatabase") -> None:
        self.s, self._database = working, database

    async def lock_import_run(self) -> None:
        self.s.run_locks += 1

    async def find_import_category(self) -> CategoryRecord | None:
        return next(
            (c for c in self.s.categories.values() if c.slug == IMPORT_CATEGORY_SLUG),
            None,
        )

    async def insert_category(self, values: dict[str, Any]) -> CategoryRecord:
        record = CategoryRecord(
            id=self.s.next_id("cat"), created_at=T0, updated_at=T0, **values
        )
        self.s.categories[record.id] = record
        return record

    async def find_merchant_id(self, provider: str, external_id: str) -> str | None:
        return self.s.merchant_imports.get((provider, external_id))

    async def merchant_slug_exists(self, slug: str) -> bool:
        return any(m.slug == slug for m in self.s.merchants.values())

    async def insert_merchant(self, values: dict[str, Any]) -> MerchantRecord:
        record = MerchantRecord(
            id=self.s.next_id("mer"), created_at=T0, updated_at=T0, **values
        )
        self.s.merchants[record.id] = record
        return record

    async def insert_merchant_import(
        self, provider: str, external_id: str, merchant_id: str
    ) -> None:
        self.s.merchant_imports[(provider, external_id)] = merchant_id

    async def find_offer_import(
        self, provider: str, external_id: str
    ) -> OfferImportRecord | None:
        return self.s.imports.get((provider, external_id))

    async def offer_slug_exists(self, slug: str) -> bool:
        return any(o.slug == slug for o in self.s.offers.values())

    async def get_offer(
        self, offer_id: str, *, lock: bool = False
    ) -> OfferRecord | None:
        return self.s.offers.get(offer_id)

    async def list_links(self, offer_id: str) -> list[AffiliateLinkRecord]:
        return [link for link in self.s.links.values() if link.offer_id == offer_id]

    async def insert_offer(self, values: dict[str, Any]) -> OfferRecord:
        self._database.offer_inserts += 1
        if self._database.fail_insert_offer_at == self._database.offer_inserts:
            raise RuntimeError("simulated database failure")
        record = OfferRecord(
            id=self.s.next_id("offer"), created_at=T0, updated_at=T0, **values
        )
        self.s.offers[record.id] = record
        return record

    async def update_offer(self, offer_id: str, values: dict[str, Any]) -> OfferRecord:
        record = dataclasses.replace(
            self.s.offers[offer_id], **values, updated_at=T0 + timedelta(hours=1)
        )
        self.s.offers[offer_id] = record
        return record

    async def insert_link(self, values: dict[str, Any]) -> AffiliateLinkRecord:
        record = AffiliateLinkRecord(
            id=self.s.next_id("link"), created_at=T0, updated_at=T0, **values
        )
        self.s.links[record.id] = record
        return record

    async def update_link(
        self, link_id: str, values: dict[str, Any]
    ) -> AffiliateLinkRecord:
        record = dataclasses.replace(self.s.links[link_id], **values)
        self.s.links[link_id] = record
        return record

    async def insert_offer_import(self, values: dict[str, Any]) -> None:
        record = OfferImportRecord(id=self.s.next_id("imp"), **values)
        self.s.imports[(record.provider, record.external_id)] = record

    async def update_offer_import(self, import_id: str, values: dict[str, Any]) -> None:
        for key, record in self.s.imports.items():
            if record.id == import_id:
                self.s.imports[key] = dataclasses.replace(record, **values)
                return
        raise LookupError(import_id)


class FakeDatabase:
    """Committed state plus the units of work that change it."""

    def __init__(self, *, fail_insert_offer_at: int | None = None) -> None:
        self.committed = State()
        self.commits = 0
        self.units_opened = 0
        self.offer_inserts = 0
        self.fail_insert_offer_at = fail_insert_offer_at

    @asynccontextmanager
    async def unit(self) -> AsyncIterator[Any]:
        from app.services.awin_import_run import Unit

        self.units_opened += 1
        working = self.committed.copy()
        yield Unit(
            FakeImportStore(working, self),
            FakeSession(self, working),
            FakeAuditor(working),
        )

    # --- helpers for tests -----------------------------------------------------------------

    def edit_offer(self, offer_id: str, **changes: Any) -> None:
        """What an editor's save does: change the committed offer row."""
        state = self.committed
        state.offers[offer_id] = dataclasses.replace(state.offers[offer_id], **changes)

    def set_status(self, offer_id: str, status: OfferStatus) -> None:
        self.edit_offer(offer_id, status=status)

    def edit_link(self, link_id: str, **changes: Any) -> None:
        state = self.committed
        state.links[link_id] = dataclasses.replace(state.links[link_id], **changes)
