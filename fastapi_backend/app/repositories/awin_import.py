"""Queries behind the Awin offers importer. No business rules live here.

Catalogue reads and writes reuse the admin repositories (one place builds an offer, a link, a merchant or a
category row); this class adds what only the importer needs: the provenance tables, the lookup of the
holding category and the run lock. Methods keep distinct names (``insert_offer``, ``insert_merchant``) because
the admin repositories use ``insert`` for each of their own aggregates.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.platform.catalogue_import import MerchantImport, OfferImport
from app.models.platform.category import Category
from app.repositories.admin_categories import AdminCategoryRepository, CategoryRecord
from app.repositories.admin_merchants import AdminMerchantRepository, MerchantRecord
from app.repositories.admin_offers import (
    AdminOfferRepository,
    AffiliateLinkRecord,
    OfferRecord,
)
from app.repositories.base import BaseRepository

# One constant for every importer run: ``pg_advisory_xact_lock`` makes concurrent runs take turns.
IMPORT_LOCK_KEY = 7_460_046_001
# Longest a batch waits for the run lock or an offer an editor has open before it fails (``lock_timeout``).
DEFAULT_LOCK_TIMEOUT_MS = 30_000
IMPORT_CATEGORY_SLUG = "imported-uncategorised"


@dataclass(frozen=True)
class OfferImportRecord:
    id: str
    provider: str
    external_id: str
    offer_id: str
    source_hash: str
    applied_source_hash: str
    applied_content_hash: str
    applied_at: datetime
    source_changed_at: datetime | None


def _import_record(row: OfferImport) -> OfferImportRecord:
    return OfferImportRecord(
        id=row.id,
        provider=row.provider,
        external_id=row.external_id,
        offer_id=row.offer_id,
        source_hash=row.source_hash,
        applied_source_hash=row.applied_source_hash,
        applied_content_hash=row.applied_content_hash,
        applied_at=row.applied_at,
        source_changed_at=row.source_changed_at,
    )


class AwinImportRepository(BaseRepository):
    def __init__(
        self, session: AsyncSession, lock_timeout_ms: int = DEFAULT_LOCK_TIMEOUT_MS
    ) -> None:
        super().__init__(session)
        self._lock_timeout_ms = lock_timeout_ms
        self._offers = AdminOfferRepository(session)
        self._merchants = AdminMerchantRepository(session)
        self._categories = AdminCategoryRepository(session)

    async def lock_import_run(self) -> None:
        """Wait (at most ``lock_timeout_ms``) until no other import holds the lock; released at commit.

        The timeout is set for the rest of the transaction, so a row lock held by a stuck admin
        transaction later in the batch fails the batch as well (SQLSTATE 55P03) instead of waiting forever.
        """
        await self._session.execute(
            text("SELECT set_config('lock_timeout', :ms, true)"),
            {"ms": str(self._lock_timeout_ms)},
        )
        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(:key)"), {"key": IMPORT_LOCK_KEY}
        )

    # --- the holding category ---------------------------------------------------------------

    async def find_import_category(self) -> CategoryRecord | None:
        found = await self._session.scalar(
            select(Category.id)
            .where(Category.slug == IMPORT_CATEGORY_SLUG)
            .with_for_update(
                read=True
            )  # FOR SHARE: a concurrent enable or disable waits for this batch
        )
        return await self._categories.get(found) if found else None

    async def insert_category(self, values: dict[str, Any]) -> CategoryRecord:
        return await self._categories.insert(values)

    # --- merchants --------------------------------------------------------------------------

    async def find_merchant_id(self, provider: str, external_id: str) -> str | None:
        return await self._session.scalar(
            select(MerchantImport.merchant_id).where(
                MerchantImport.provider == provider,
                MerchantImport.external_id == external_id,
            )
        )

    async def merchant_slug_exists(self, slug: str) -> bool:
        return await self._merchants.slug_exists(slug)

    async def insert_merchant(self, values: dict[str, Any]) -> MerchantRecord:
        return await self._merchants.insert(values)

    async def insert_merchant_import(
        self, provider: str, external_id: str, merchant_id: str
    ) -> None:
        self._session.add(
            MerchantImport(
                provider=provider, external_id=external_id, merchant_id=merchant_id
            )
        )
        await self._session.flush()

    # --- offers and links -------------------------------------------------------------------

    async def find_offer_import(
        self, provider: str, external_id: str
    ) -> OfferImportRecord | None:
        row = await self._session.scalar(
            select(OfferImport).where(
                OfferImport.provider == provider,
                OfferImport.external_id == external_id,
            )
        )
        return _import_record(row) if row else None

    async def offer_slug_exists(self, slug: str) -> bool:
        return await self._offers.slug_exists(slug)

    async def get_offer(
        self, offer_id: str, *, lock: bool = False
    ) -> OfferRecord | None:
        """The offer, row-locked when ``lock``: an editor saving it at this moment finishes first."""
        return await self._offers.get(offer_id, lock=lock)

    async def list_links(self, offer_id: str) -> list[AffiliateLinkRecord]:
        return await self._offers.list_links(offer_id)

    async def insert_offer(self, values: dict[str, Any]) -> OfferRecord:
        return await self._offers.insert(values)

    async def update_offer(self, offer_id: str, values: dict[str, Any]) -> OfferRecord:
        return await self._offers.update(offer_id, values)

    async def insert_link(self, values: dict[str, Any]) -> AffiliateLinkRecord:
        return await self._offers.insert_link(values)

    # --- provenance -------------------------------------------------------------------------

    async def insert_offer_import(self, values: dict[str, Any]) -> None:
        self._session.add(OfferImport(**values))
        await self._session.flush()

    async def update_offer_import(self, import_id: str, values: dict[str, Any]) -> None:
        await self._session.execute(
            update(OfferImport).where(OfferImport.id == import_id).values(**values)
        )
