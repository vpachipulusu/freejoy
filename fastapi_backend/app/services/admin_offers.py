"""Admin offers: create, edit, affiliate links and the publish lifecycle.

Rules enforced here, not only in the router (see ``offer_lifecycle`` for the status table):

* Every write locks the offer row, stages one audit row on the same session and commits once
  after ``record``; a refusal raises first, so nothing is persisted.
* Publishing never stamps ``last_verified_at``: an editor verifies explicitly (``verify``) and
  ``publish`` refuses an unverified offer. The database CHECK
  ``ck_offers_published_is_verified`` is the backstop.
* A published offer keeps meeting the publish rules: edits and link changes that would break
  them (category disabled, no destination left, ``end_at`` in the past) are refused.
* ``verify`` and ``publish`` are the steps that say "a person reviewed this": they need the
  ``review_token`` the admin read returned (``offer_fingerprint.review_token``: a hash of the
  reviewable content and the affiliate links) and refuse with ``OfferChanged`` when it no longer
  matches. The token is recomputed after the offer row is locked, and every writer of the offer or
  its links takes that lock first (editor edits, link edits, the Awin importer), so nothing can
  change the offer between the check and the write. ``unpublish``, ``expire`` and ``archive`` only
  take content off the public site (or end an offer's life), claim nothing was reviewed and need no
  token.
* URLs are validated here (https only, see ``app.core.catalogue_urls``); the slug is derived
  from the title unless the editor supplies one, and cannot be changed afterwards.
"""

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from typing import Any, Protocol
from urllib.parse import urlsplit

from sqlalchemy.exc import IntegrityError

from app.core.affiliate_hosts import destination_allowed, is_known_provider
from app.core.config import settings
from app.core.permissions import AdminContext
from app.models.platform.offer import OfferStatus
from app.repositories.admin_offers import (
    AffiliateLinkRecord,
    OfferFilters,
    OfferPage,
    OfferRecord,
)
from app.services.admin_catalogue_common import (
    AuditRecorder,
    Clock,
    Committer,
    changed_values,
    clamp_page,
    clean_urls,
    https_url,
    resolve_slug,
    slug_conflict,
    utc_now,
)
from app.services.admin_catalogue_errors import (
    CatalogueNotFound,
    InvalidInput,
    OfferChanged,
    PublishBlocked,
    SlugTaken,
)
from app.services.offer_fingerprint import review_token as compute_review_token
from app.services.offer_lifecycle import (
    OfferAction,
    PublishFacts,
    next_status,
    publish_blockers,
    require_editable,
)
from app.services.offer_rules import (
    AUDITED_FIELDS,
    EDITABLE_FIELDS,
    URL_FIELDS,
    check_postage,
    check_window,
)

__all__ = ["AdminOfferService", "OfferDetail", "OfferListItem", "OfferListing"]

LINK_FIELDS = frozenset({"provider", "destination_url", "tracking_id", "active"})
LINK_AUDITED = frozenset({"provider", "active"})
RESOURCE = "offers"
LINK_RESOURCE = "affiliate_links"


class OfferStore(Protocol):
    async def list_offers(
        self, filters: OfferFilters, limit: int, offset: int
    ) -> OfferPage: ...
    async def list_links_by_offer(
        self, offer_ids: Sequence[str]
    ) -> dict[str, list[AffiliateLinkRecord]]: ...
    async def get(self, offer_id: str, *, lock: bool = False) -> OfferRecord | None: ...
    async def slug_exists(self, slug: str) -> bool: ...
    async def insert(self, values: dict[str, Any]) -> OfferRecord: ...
    async def update(self, offer_id: str, values: dict[str, Any]) -> OfferRecord: ...
    async def category_enabled(self, category_id: str) -> bool | None: ...
    async def merchant_active(self, merchant_id: str) -> bool | None: ...
    async def merchant_website(self, merchant_id: str) -> str | None: ...
    async def list_links(self, offer_id: str) -> list[AffiliateLinkRecord]: ...
    async def count_active_links(self, offer_id: str) -> int: ...
    async def get_link(
        self, offer_id: str, link_id: str, *, lock: bool = False
    ) -> AffiliateLinkRecord | None: ...
    async def insert_link(self, values: dict[str, Any]) -> AffiliateLinkRecord: ...
    async def update_link(
        self, link_id: str, values: dict[str, Any]
    ) -> AffiliateLinkRecord: ...


@dataclass(frozen=True)
class OfferDetail:
    offer: OfferRecord
    links: list[AffiliateLinkRecord]

    @property
    def review_token(self) -> str:
        return compute_review_token(self.offer, self.links)


@dataclass(frozen=True)
class OfferListItem:
    offer: OfferRecord
    review_token: str


@dataclass(frozen=True)
class OfferListing:
    offers: list[OfferListItem]
    total_count: int


def _host(url: str) -> str:
    return urlsplit(url).hostname or ""


def _snapshot(offer: OfferRecord) -> dict[str, Any]:
    """What a create writes to the audit row: identifiers and enums, no free text."""
    return {
        "title": offer.title,
        "slug": offer.slug,
        "category_id": offer.category_id,
        "merchant_id": offer.merchant_id,
        "offer_type": offer.offer_type.value,
        "status": offer.status.value,
    }


class AdminOfferService:
    def __init__(
        self,
        store: OfferStore,
        session: Committer,
        auditor: AuditRecorder,
        clock: Clock = utc_now,
    ) -> None:
        self.store, self.session = store, session
        self.auditor, self.clock = auditor, clock

    async def list_offers(
        self, filters: OfferFilters, limit: int, offset: int
    ) -> OfferListing:
        page = await self.store.list_offers(filters, *clamp_page(limit, offset))
        links = await self.store.list_links_by_offer([o.id for o in page.offers])
        items = [
            OfferListItem(o, compute_review_token(o, links[o.id])) for o in page.offers
        ]
        return OfferListing(items, page.total_count)

    async def get_offer(self, offer_id: str) -> OfferDetail:
        offer = await self.store.get(offer_id)
        if offer is None:
            raise CatalogueNotFound("offer")
        return OfferDetail(offer, await self.store.list_links(offer_id))

    async def create_offer(
        self, actor: AdminContext, data: Mapping[str, Any]
    ) -> OfferDetail:
        values = clean_urls(data, URL_FIELDS)
        check_postage(values)
        check_window(values.get("start_at"), values.get("end_at"))
        slug = resolve_slug(values.pop("slug", None), values["title"])
        await self._require_references(values["category_id"], values["merchant_id"])
        if await self.store.slug_exists(slug):
            raise SlugTaken()
        try:
            created = await self.store.insert(
                {**values, "slug": slug, "status": OfferStatus.DRAFT}
            )
        except IntegrityError as exc:
            await self.session.rollback()
            raise slug_conflict(exc) from None
        await self.auditor.record(
            actor,
            action="admin.offers.create",
            resource=RESOURCE,
            resource_id=created.id,
            after=_snapshot(created),
        )
        await self.session.commit()
        return OfferDetail(created, [])

    async def update_offer(
        self, actor: AdminContext, offer_id: str, changes: Mapping[str, Any]
    ) -> OfferDetail:
        if not changes or not changes.keys() <= EDITABLE_FIELDS:
            raise ValueError("Invalid offer fields")
        current = await self._locked(offer_id)
        require_editable(current.status)
        values = clean_urls(changes, URL_FIELDS)
        merged = replace(current, **values)
        check_postage(asdict(merged))
        check_window(merged.start_at, merged.end_at)
        await self._require_references(merged.category_id, merged.merchant_id)
        if merged.status is OfferStatus.PUBLISHED:
            # An offer already past its end date stays editable; the date is only judged
            # when this request changes it.
            await self._require_publishable(merged, check_end="end_at" in values)
        updated = await self.store.update(offer_id, values)
        before, after = changed_values(current, values, AUDITED_FIELDS)
        await self.auditor.record(
            actor,
            action="admin.offers.update",
            resource=RESOURCE,
            resource_id=offer_id,
            before=before,
            after=after,
            details={"fields": sorted(values)},
        )
        await self.session.commit()
        return OfferDetail(updated, await self.store.list_links(offer_id))

    async def transition(
        self,
        actor: AdminContext,
        offer_id: str,
        action: OfferAction,
        review_token: str | None = None,
    ) -> OfferDetail:
        """publish, unpublish, expire or archive (``verify`` has its own method).

        ``publish`` needs the ``review_token`` of the offer as the editor loaded it; the other
        actions ignore it.
        """
        if action is OfferAction.VERIFY:
            raise ValueError("Use verify")
        current = await self._locked(offer_id)
        if action is OfferAction.PUBLISH:
            await self._require_unchanged(current, review_token)
        target = next_status(current.status, action)
        if action is OfferAction.PUBLISH:
            await self._require_publishable(current)
        updated = await self.store.update(offer_id, {"status": target})
        await self.auditor.record(
            actor,
            action=f"admin.offers.{action.value}",
            resource=RESOURCE,
            resource_id=offer_id,
            before={"status": current.status.value},
            after={"status": target.value},
        )
        await self.session.commit()
        return OfferDetail(updated, await self.store.list_links(offer_id))

    async def verify(
        self,
        actor: AdminContext,
        offer_id: str,
        note: str | None,
        review_token: str | None,
    ) -> OfferDetail:
        current = await self._locked(offer_id)
        await self._require_unchanged(current, review_token)
        next_status(current.status, OfferAction.VERIFY)  # refuses an archived offer
        verified_at = self.clock()
        updated = await self.store.update(offer_id, {"last_verified_at": verified_at})
        await self.auditor.record(
            actor,
            action="admin.offers.verify",
            resource=RESOURCE,
            resource_id=offer_id,
            before={"last_verified_at": current.last_verified_at},
            after={"last_verified_at": verified_at},
            details={"note": note} if note else None,
        )
        await self.session.commit()
        return OfferDetail(updated, await self.store.list_links(offer_id))

    async def create_link(
        self, actor: AdminContext, offer_id: str, data: Mapping[str, Any]
    ) -> AffiliateLinkRecord:
        offer = await self._locked(offer_id)
        require_editable(offer.status)
        values = {**data, "destination_url": https_url(data["destination_url"])}
        await self._require_allowed_destination(
            offer, values["provider"], values["destination_url"]
        )
        link = await self.store.insert_link({**values, "offer_id": offer_id})
        await self.auditor.record(
            actor,
            action="admin.offer_links.create",
            resource=LINK_RESOURCE,
            resource_id=link.id,
            after={"provider": link.provider, "active": link.active},
            details={
                "offer_id": offer_id,
                "destination_host": _host(link.destination_url),
            },
        )
        await self.session.commit()
        return link

    async def update_link(
        self,
        actor: AdminContext,
        offer_id: str,
        link_id: str,
        changes: Mapping[str, Any],
    ) -> AffiliateLinkRecord:
        if not changes or not changes.keys() <= LINK_FIELDS:
            raise ValueError("Invalid link fields")
        offer = await self._locked(offer_id)
        require_editable(offer.status)
        link = await self.store.get_link(offer_id, link_id, lock=True)
        if link is None:
            raise CatalogueNotFound("link")
        values = clean_urls(changes, ("destination_url",))
        # Also when a switched-off link is switched on: it may predate the allowlist.
        reactivating = values.get("active") is True and not link.active
        if "provider" in values or "destination_url" in values or reactivating:
            await self._require_allowed_destination(
                offer,
                values.get("provider", link.provider),
                values.get("destination_url", link.destination_url),
            )
        if values.get("active") is False and link.active:
            await self._require_destination_remains(offer)
        updated = await self.store.update_link(link_id, values)
        before, after = changed_values(link, values, LINK_AUDITED)
        if (
            "destination_url" in values
        ):  # the host only: paths and queries carry tracking ids
            before["destination_host"] = _host(link.destination_url)
            after["destination_host"] = _host(updated.destination_url)
        await self.auditor.record(
            actor,
            action="admin.offer_links.update",
            resource=LINK_RESOURCE,
            resource_id=link_id,
            before=before,
            after=after,
            details={"offer_id": offer_id, "fields": sorted(values)},
        )
        await self.session.commit()
        return updated

    async def _require_allowed_destination(
        self, offer: OfferRecord, provider: str, destination_url: str
    ) -> None:
        """The provider must be known and the host one it may use (also checked when followed)."""
        if not is_known_provider(provider):
            raise InvalidInput("unknown_provider")
        allowed = destination_allowed(
            provider,
            _host(destination_url),
            merchant_website=await self.store.merchant_website(offer.merchant_id),
            allow_sample=settings.relaxed_environment,
        )
        if not allowed:
            raise InvalidInput("destination_not_allowed")

    async def _locked(self, offer_id: str) -> OfferRecord:
        offer = await self.store.get(offer_id, lock=True)
        if offer is None:
            raise CatalogueNotFound("offer")
        return offer

    async def _require_unchanged(self, locked: OfferRecord, token: str | None) -> None:
        """The editor's token must match the offer and its links as they are under the row lock."""
        if not token:
            raise InvalidInput("review_token_required")
        links = await self.store.list_links(locked.id)
        if compute_review_token(locked, links) != token:
            raise OfferChanged()

    async def _require_references(self, category_id: str, merchant_id: str) -> None:
        if await self.store.category_enabled(category_id) is None:
            raise InvalidInput("unknown_category")
        if await self.store.merchant_active(merchant_id) is None:
            raise InvalidInput("unknown_merchant")

    async def _require_publishable(
        self, offer: OfferRecord, *, check_end: bool = True
    ) -> None:
        facts = PublishFacts(
            category_enabled=bool(await self.store.category_enabled(offer.category_id)),
            merchant_active=bool(await self.store.merchant_active(offer.merchant_id)),
            active_links=await self.store.count_active_links(offer.id),
            has_source_url=bool(offer.source_url),
            end_at=offer.end_at if check_end else None,
            last_verified_at=offer.last_verified_at,
        )
        blockers = publish_blockers(facts, self.clock())
        if blockers:
            raise PublishBlocked([reason.value for reason in blockers])

    async def _require_destination_remains(self, offer: OfferRecord) -> None:
        """Deactivating a link may not leave a published offer without a destination."""
        if offer.status is not OfferStatus.PUBLISHED or offer.source_url:
            return
        if await self.store.count_active_links(offer.id) <= 1:
            raise PublishBlocked(["no_destination"])
