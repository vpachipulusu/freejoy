"""Write validated Awin promotions into the catalogue as draft offers, safely and idempotently.

``OfferImporter.import_batch`` handles one batch inside one transaction: it takes the import lock,
makes sure the disabled holding category exists, then for each promotion decides from the provenance
row (``offer_imports``, keyed by provider and promotion id) what to do:

=============================================  ===============================================
state                                          result
=============================================  ===============================================
never imported                                 create a DRAFT offer, an ``awin`` link, the
                                               provenance row (and an inactive merchant if the
                                               advertiser is new)                  -> created
source unchanged since the last run            nothing is written                  -> unchanged
source changed, offer is an untouched import   refresh the imported fields         -> updated
(DRAFT and its fingerprint still matches)
source changed, offer was edited, published,   content is NEVER overwritten; only
expired or archived                            ``source_changed_at`` is set        -> source_changed
=============================================  ===============================================

"Edited by a person" is detected by fingerprint, not by ``updated_at``: when the importer writes an
offer it stores a hash of the editor-changeable fields (all but ``commercial_basis``, which it writes once as
AFFILIATE and never refreshes) and the offer's links
(``offer_fingerprint.fingerprint``) in ``applied_content_hash``. Any later difference means a person
changed the offer (a refresh by the importer re-stores the new fingerprint). ``updated_at`` would also
move for the importer's own writes and for housekeeping such as expiry, which is why it is not used.

Nothing here publishes, verifies or features an offer. Every create, refresh and source-change marker
writes an audit row through ``record_system_action`` (actor ``commands.import_awin_offers``) holding ids,
enums and counts, never copy, a URL beyond its host, or a token. The caller opens the session, passes
the store, auditor and committer, and a failure leaves the batch uncommitted.
"""

import enum
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Protocol
from urllib.parse import urlsplit

from app.core.offer_content import AUDITED_FIELDS
from app.core.offer_fingerprint import fingerprint, is_untouched_draft
from app.repositories.admin_categories import CategoryRecord
from app.repositories.admin_merchants import MerchantRecord
from app.repositories.admin_offers import AffiliateLinkRecord, OfferRecord
from app.repositories.awin_import import OfferImportRecord
from app.services.admin_catalogue_common import Clock, changed_values, utc_now
from app.services.awin_offer_mapping import (
    import_category_values,
    link_values,
    merchant_slug_candidates,
    merchant_values,
    offer_slug_candidates,
    offer_values,
    refresh_values,
)
from app.services.awin_promotions import PROVIDER, Promotion, SkipReason, source_hash

SOURCE = "commands.import_awin_offers"
logger = logging.getLogger("freejoy.awin_import")
OFFERS = "offers"
LINKS = "affiliate_links"
MERCHANTS = "merchants"
CATEGORIES = "categories"
# Titles come from a third party: the audit row names the field in ``details.fields`` and keeps the text out.
AUDITED_NON_TEXT_FIELDS = AUDITED_FIELDS - {"title"}


class Outcome(enum.StrEnum):
    CREATED = "created"
    UPDATED = "updated"
    UNCHANGED = "unchanged"
    SOURCE_CHANGED = "source_changed"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class ItemResult:
    outcome: Outcome
    reason: SkipReason | None = None


@dataclass(frozen=True)
class ImportTally:
    """Counts by outcome (and skip reason). Immutable: ``plus`` and ``merged`` return new tallies."""

    created: int = 0
    updated: int = 0
    unchanged: int = 0
    source_changed: int = 0
    skipped: Mapping[str, int] = field(default_factory=dict)

    @property
    def skipped_total(self) -> int:
        return sum(self.skipped.values())

    @property
    def processed(self) -> int:
        counted = self.created + self.updated + self.unchanged + self.source_changed
        return counted + self.skipped_total

    def plus(self, result: ItemResult) -> "ImportTally":
        if result.outcome is Outcome.SKIPPED:
            return self.skip(result.reason or SkipReason.INVALID_ITEM)
        return replace(
            self, **{result.outcome.value: getattr(self, result.outcome.value) + 1}
        )

    def skip(self, reason: SkipReason) -> "ImportTally":
        counts = {**self.skipped, reason.value: self.skipped.get(reason.value, 0) + 1}
        return replace(self, skipped=counts)

    def merged(self, other: "ImportTally") -> "ImportTally":
        skipped = dict(self.skipped)
        for reason, count in other.skipped.items():
            skipped[reason] = skipped.get(reason, 0) + count
        return ImportTally(
            self.created + other.created,
            self.updated + other.updated,
            self.unchanged + other.unchanged,
            self.source_changed + other.source_changed,
            skipped,
        )


class ImportAborted(Exception):
    """The run must stop for a reason that is not an error in the data (``reason`` is a code)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class ImportStore(Protocol):
    async def lock_import_run(self) -> None: ...
    async def find_import_category(self) -> CategoryRecord | None: ...
    async def insert_category(self, values: dict[str, Any]) -> CategoryRecord: ...
    async def find_merchant_id(self, provider: str, external_id: str) -> str | None: ...
    async def merchant_slug_exists(self, slug: str) -> bool: ...
    async def insert_merchant(self, values: dict[str, Any]) -> MerchantRecord: ...
    async def insert_merchant_import(
        self, provider: str, external_id: str, merchant_id: str
    ) -> None: ...
    async def find_offer_import(
        self, provider: str, external_id: str
    ) -> OfferImportRecord | None: ...
    async def offer_slug_exists(self, slug: str) -> bool: ...
    async def get_offer(
        self, offer_id: str, *, lock: bool = False
    ) -> OfferRecord | None: ...
    async def list_links(self, offer_id: str) -> list[AffiliateLinkRecord]: ...
    async def insert_offer(self, values: dict[str, Any]) -> OfferRecord: ...
    async def update_offer(
        self, offer_id: str, values: dict[str, Any]
    ) -> OfferRecord: ...
    async def insert_link(self, values: dict[str, Any]) -> AffiliateLinkRecord: ...
    async def insert_offer_import(self, values: dict[str, Any]) -> None: ...
    async def update_offer_import(
        self, import_id: str, values: dict[str, Any]
    ) -> None: ...


class SystemAuditor(Protocol):
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
    ) -> None: ...


class Committer(Protocol):
    async def commit(self) -> None: ...


def _host(url: str) -> str:
    return urlsplit(url).hostname or ""


class OfferImporter:
    def __init__(
        self,
        store: ImportStore,
        session: Committer,
        auditor: SystemAuditor,
        clock: Clock = utc_now,
    ) -> None:
        self.store, self.session, self.auditor, self.clock = (
            store,
            session,
            auditor,
            clock,
        )

    async def import_batch(self, promotions: Sequence[Promotion]) -> ImportTally:
        """Import every promotion in one transaction and commit once (a failure commits nothing)."""
        await self.store.lock_import_run()
        category_id = await self._import_category()
        tally = ImportTally()
        for promotion in promotions:
            tally = tally.plus(await self._import_one(promotion, category_id))
        await self.session.commit()
        return tally

    async def _import_one(self, promotion: Promotion, category_id: str) -> ItemResult:
        digest = source_hash(promotion)
        known = await self.store.find_offer_import(
            PROVIDER, str(promotion.promotion_id)
        )
        if known is None:
            return await self._create(promotion, category_id, digest)
        if known.source_hash == digest and known.source_changed_at is None:
            return ItemResult(Outcome.UNCHANGED)
        return await self._reconcile(promotion, known, digest)

    # --- creating ---------------------------------------------------------------------------

    async def _create(
        self, promotion: Promotion, category_id: str, digest: str
    ) -> ItemResult:
        slug = await _first_free(
            offer_slug_candidates(promotion), self.store.offer_slug_exists
        )
        merchant_id = await self._merchant_id(promotion) if slug else None
        if slug is None or merchant_id is None:
            logger.warning(
                "awin item skipped: promotion_id=%s reason=%s",
                promotion.promotion_id,
                SkipReason.SLUG_UNAVAILABLE.value,
            )
            return ItemResult(Outcome.SKIPPED, SkipReason.SLUG_UNAVAILABLE)
        offer = await self.store.insert_offer(
            offer_values(promotion, category_id, merchant_id, slug)
        )
        link = await self.store.insert_link(
            {**link_values(promotion), "offer_id": offer.id}
        )
        await self.store.insert_offer_import(
            {
                "provider": PROVIDER,
                "external_id": str(promotion.promotion_id),
                "offer_id": offer.id,
                "source_hash": digest,
                "applied_source_hash": digest,
                "applied_content_hash": fingerprint(offer, [link]),
                "applied_at": self.clock(),
                "source_changed_at": None,
            }
        )
        await self._audit_created(promotion, offer, link)
        return ItemResult(Outcome.CREATED)

    async def _merchant_id(self, promotion: Promotion) -> str | None:
        external_id = str(promotion.advertiser_id)
        known = await self.store.find_merchant_id(PROVIDER, external_id)
        if known is not None:
            return known
        slug = await _first_free(
            merchant_slug_candidates(promotion), self.store.merchant_slug_exists
        )
        if slug is None:
            return None
        merchant = await self.store.insert_merchant(merchant_values(promotion, slug))
        await self.store.insert_merchant_import(PROVIDER, external_id, merchant.id)
        await self.auditor.record_system_action(
            SOURCE,
            action="admin.merchants.create",
            resource=MERCHANTS,
            resource_id=merchant.id,
            after={"slug": merchant.slug, "active": False},
            details={
                "provider": PROVIDER,
                "external_id": external_id,
                "reason": "imported",
            },
        )
        return merchant.id

    async def _import_category(self) -> str:
        found = await self.store.find_import_category()
        if found is not None:
            if (
                found.enabled
            ):  # someone enabled the holding category: imports would be publishable
                raise ImportAborted("import_category_enabled")
            return found.id
        created = await self.store.insert_category(import_category_values())
        await self.auditor.record_system_action(
            SOURCE,
            action="admin.categories.create",
            resource=CATEGORIES,
            resource_id=created.id,
            after={"name": created.name, "slug": created.slug, "enabled": False},
            details={"provider": PROVIDER, "reason": "import_holding_category"},
        )
        return created.id

    async def _audit_created(
        self, promotion: Promotion, offer: OfferRecord, link: AffiliateLinkRecord
    ) -> None:
        origin = {"provider": PROVIDER, "external_id": str(promotion.promotion_id)}
        await self.auditor.record_system_action(
            SOURCE,
            action="admin.offers.create",
            resource=OFFERS,
            resource_id=offer.id,
            after={
                "slug": offer.slug,
                "category_id": offer.category_id,
                "merchant_id": offer.merchant_id,
                "offer_type": offer.offer_type.value,
                "commercial_basis": offer.commercial_basis.value,
                "status": offer.status.value,
            },
            details={**origin, "reason": "imported"},
        )
        await self.auditor.record_system_action(
            SOURCE,
            action="admin.offer_links.create",
            resource=LINKS,
            resource_id=link.id,
            after={"provider": link.provider, "active": link.active},
            details={
                "offer_id": offer.id,
                "destination_host": _host(link.destination_url),
            },
        )

    # --- an offer that is already imported ----------------------------------------------------

    async def _reconcile(
        self, promotion: Promotion, known: OfferImportRecord, digest: str
    ) -> ItemResult:
        offer = await self.store.get_offer(known.offer_id, lock=True)
        if (
            offer is None
        ):  # deleted between the lookup and the lock; the next run recreates it
            raise LookupError("imported offer vanished")
        links = await self.store.list_links(offer.id)
        if await self._may_refresh(promotion, known, offer, links):
            return await self._refresh(promotion, known, offer, links[0], digest)
        if digest == known.source_hash:  # already recorded: nothing new for a reviewer
            return ItemResult(Outcome.UNCHANGED)
        return await self._note_source_change(known, digest)

    async def _may_refresh(
        self,
        promotion: Promotion,
        known: OfferImportRecord,
        offer: OfferRecord,
        links: list[AffiliateLinkRecord],
    ) -> bool:
        """True only for an untouched import whose refresh would rewrite text and nothing else.

        The text of an untouched draft may be refreshed. A destination the editor may have looked at is
        never rewritten in place (neither the tracking URL nor the source URL may have moved), and neither
        is an offer whose
        advertiser is no longer the merchant it was imported under.
        """
        if (
            not is_untouched_draft(
                status=offer.status.value,
                link_count=len(links),
                current_fingerprint=fingerprint(offer, links),
                applied_fingerprint=known.applied_content_hash,
            )
            or links[0].destination_url != promotion.tracking_url
            or offer.source_url != promotion.source_url
        ):
            return False
        merchant_id = await self.store.find_merchant_id(
            PROVIDER, str(promotion.advertiser_id)
        )
        return merchant_id == offer.merchant_id

    async def _refresh(
        self,
        promotion: Promotion,
        known: OfferImportRecord,
        offer: OfferRecord,
        link: AffiliateLinkRecord,
        digest: str,
    ) -> ItemResult:
        changes = _differences(offer, refresh_values(promotion))
        updated = await self.store.update_offer(offer.id, changes) if changes else offer
        await self.store.update_offer_import(
            known.id,
            {
                "source_hash": digest,
                "applied_source_hash": digest,
                "applied_content_hash": fingerprint(updated, [link]),
                "applied_at": self.clock(),
                "source_changed_at": None,
            },
        )
        if not changes:
            return ItemResult(Outcome.UNCHANGED)
        before, after = changed_values(offer, changes, AUDITED_NON_TEXT_FIELDS)
        await self.auditor.record_system_action(
            SOURCE,
            action="admin.offers.update",
            resource=OFFERS,
            resource_id=offer.id,
            before=before,
            after=after,
            details={
                "provider": known.provider,
                "external_id": known.external_id,
                "fields": sorted(changes),
                "reason": "source_refreshed",
            },
        )
        return ItemResult(Outcome.UPDATED)

    async def _note_source_change(
        self, known: OfferImportRecord, digest: str
    ) -> ItemResult:
        """The offer is not an untouched import: record that the source moved, never its content."""
        reverted = digest == known.applied_source_hash
        await self.store.update_offer_import(
            known.id,
            {
                "source_hash": digest,
                "source_changed_at": None if reverted else self.clock(),
            },
        )
        if (
            reverted
        ):  # the source is back to what the offer reflects: nothing for a reviewer
            return ItemResult(Outcome.UNCHANGED)
        await self.auditor.record_system_action(
            SOURCE,
            action="admin.offers.source_changed",
            resource=OFFERS,
            resource_id=known.offer_id,
            details={"provider": known.provider, "external_id": known.external_id},
        )
        return ItemResult(Outcome.SOURCE_CHANGED)


def _differences(current: object, wanted: Mapping[str, Any]) -> dict[str, Any]:
    return {
        name: value for name, value in wanted.items() if getattr(current, name) != value
    }


async def _first_free(
    candidates: Sequence[str], exists: Callable[[str], Awaitable[bool]]
) -> str | None:
    for slug in candidates:
        if not await exists(slug):
            return slug
    return None
