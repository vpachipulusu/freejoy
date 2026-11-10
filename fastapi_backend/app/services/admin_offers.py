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
  offer's content, its links in click order and its merchant's and category's public fields) and
  refuse with ``OfferChanged`` when it no longer matches. The token is recomputed after the offer
  row is locked and the category and merchant are read FOR SHARE (lock order: offer, category,
  merchant, as in publish). Writers of the offer or its links (editor edits, link edits, the Awin
  importer, the expiry job) take the offer lock first and edits of the category or merchant take
  their own row lock, so none can change what was compared before the transition commits.
  ``unpublish``, ``expire`` and ``archive`` only take content off the public site (or end an
  offer's life), claim nothing was reviewed and need no token.
* ``dismiss_source_change`` is the review queue's one write: it clears the importer's
  ``source_changed_at`` marker after the same token check and under the same offer lock, so an
  editor can say "I have seen that the source moved on". The token does not cover the marker, so the
  request also carries the marker the editor saw and a different stored one is refused with
  ``OfferChanged`` (the importer noted a newer change since). It touches nothing else; in particular
  it leaves the importer's hashes alone (see ``docs/developer/backend-awin-import.md``).
* ``commercial_basis`` (``EDITORIAL``, ``AFFILIATE`` or ``SPONSORED``) is required on create, has no default
  anywhere and is part of the review token. Publishing (and re-publishing an expired offer) an offer whose only
  active links are ``direct`` also needs the editor's explicit confirmation of the basis in the publish
  request; without it the publish is refused as ``commercial_basis_required``, and a confirmation that is not
  the stored basis is ``OfferChanged``. Link changes on an already published offer never revisit that
  confirmation: its stored basis stays in force and stays visible to the editor.
* URLs are validated here (https only, see ``app.core.catalogue_urls``); the slug is derived
  from the title unless the editor supplies one, and cannot be changed afterwards.
"""

from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from typing import Any, Protocol
from urllib.parse import urlsplit

from sqlalchemy.exc import IntegrityError

from app.core.affiliate_hosts import (
    DIRECT_PROVIDER,
    destination_allowed,
    is_known_provider,
)
from app.core.config import settings
from app.core.permissions import AdminContext
from app.models.platform.offer import CommercialBasis, OfferStatus
from app.repositories.admin_offers import (
    AffiliateLinkRecord,
    OfferFilters,
    OfferPage,
    OfferProvenance,
    OfferRecord,
    ReviewRefs,
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
    IllegalTransition,
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

__all__ = ["AdminOfferService", "OfferDetail"]

LINK_FIELDS = frozenset({"provider", "destination_url", "tracking_id", "active"})
LINK_AUDITED = frozenset({"provider", "active"})
RESOURCE = "offers"
DISMISS_ACTION = "dismiss_source_change"
LINK_RESOURCE = "affiliate_links"


class OfferStore(Protocol):
    async def list_offers(
        self, filters: OfferFilters, limit: int, offset: int
    ) -> OfferPage: ...
    async def review_refs(
        self, category_id: str, merchant_id: str, *, share: bool = False
    ) -> ReviewRefs: ...
    async def get(self, offer_id: str, *, lock: bool = False) -> OfferRecord | None: ...
    async def get_provenance(
        self, offer_id: str, *, lock: bool = False
    ) -> OfferProvenance | None: ...
    async def clear_source_change(self, offer_id: str) -> None: ...
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
    review_token: str
    # Writes only: the token of the stored state the write replaced, read under the same row lock. When it is
    # not the token the editor loaded, somebody else changed the offer first.
    previous_review_token: str | None = None
    # Where an imported offer came from (None for a hand-made offer).
    provenance: OfferProvenance | None = None


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
        "commercial_basis": offer.commercial_basis.value,
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
    ) -> OfferPage:
        return await self.store.list_offers(filters, *clamp_page(limit, offset))

    async def get_offer(self, offer_id: str) -> OfferDetail:
        offer = await self.store.get(offer_id)
        if offer is None:
            raise CatalogueNotFound("offer")
        return await self._detail(offer)

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
        detail = await self._detail(created)
        await self.session.commit()
        return detail

    async def update_offer(
        self, actor: AdminContext, offer_id: str, changes: Mapping[str, Any]
    ) -> OfferDetail:
        if not changes or not changes.keys() <= EDITABLE_FIELDS:
            raise ValueError("Invalid offer fields")
        current = await self._locked(offer_id)
        previous = await self._stored_token(current)
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
        detail = await self._detail(updated, previous)
        await self.session.commit()
        return detail

    async def transition(
        self,
        actor: AdminContext,
        offer_id: str,
        action: OfferAction,
        review_token: str | None = None,
        commercial_basis: CommercialBasis | None = None,
    ) -> OfferDetail:
        """publish, unpublish, expire or archive (``verify`` has its own method).

        ``publish`` needs the ``review_token`` of the offer as the editor loaded it; the other
        actions ignore it. ``commercial_basis`` is the basis the editor confirms when publishing: it
        is required when the offer's only active links are ``direct`` and, whenever it is sent, must be
        the stored one (``OfferChanged`` otherwise: what the editor saw is not what is stored).
        """
        if action is OfferAction.VERIFY:
            raise ValueError("Use verify")
        current = await self._locked(offer_id)
        if action is OfferAction.PUBLISH:
            previous = await self._require_unchanged(current, review_token)
            if commercial_basis not in (None, current.commercial_basis):
                raise OfferChanged()
        else:
            previous = await self._stored_token(current)
        target = next_status(current.status, action)
        if action is OfferAction.PUBLISH:
            await self._require_publishable(
                current, basis_confirmed=commercial_basis is not None
            )
        updated = await self.store.update(offer_id, {"status": target})
        await self.auditor.record(
            actor,
            action=f"admin.offers.{action.value}",
            resource=RESOURCE,
            resource_id=offer_id,
            before={"status": current.status.value},
            after={"status": target.value},
        )
        detail = await self._detail(updated, previous)
        await self.session.commit()
        return detail

    async def verify(
        self,
        actor: AdminContext,
        offer_id: str,
        note: str | None,
        review_token: str | None,
    ) -> OfferDetail:
        current = await self._locked(offer_id)
        previous = await self._require_unchanged(current, review_token)
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
        detail = await self._detail(updated, previous)
        await self.session.commit()
        return detail

    async def dismiss_source_change(
        self,
        actor: AdminContext,
        offer_id: str,
        review_token: str | None,
        seen_marker: datetime,
    ) -> OfferDetail:
        """Clear the "source changed" marker of an imported offer, after the editor has seen it.

        ``seen_marker`` is the ``source_changed_at`` the editor's page showed. Same order as ``verify``:
        offer row lock, token check (409 ``offer_changed``), the archived refusal, then the provenance row
        ``FOR UPDATE``. An archived offer, an offer that was never imported and a marker that is already
        clear are "nothing to dismiss" (409 ``illegal_transition``); a stored marker other than the one the
        editor saw (the importer noted a newer change meanwhile) is 409 ``offer_changed``, nothing written.
        Only ``source_changed_at`` changes.
        """
        current = await self._locked(offer_id)
        previous = await self._require_unchanged(current, review_token)
        require_editable(current.status)
        marked = await self.store.get_provenance(offer_id, lock=True)
        if marked is None or marked.source_changed_at is None:
            raise IllegalTransition(current.status.value, DISMISS_ACTION)
        if marked.source_changed_at != seen_marker:
            raise OfferChanged()
        await self.store.clear_source_change(offer_id)
        await self.auditor.record(
            actor,
            action="admin.offers.source_change_dismissed",
            resource=RESOURCE,
            resource_id=offer_id,
            before={"source_changed_at": marked.source_changed_at},
            after={"source_changed_at": None},
            details={"provider": marked.provider, "external_id": marked.external_id},
        )
        detail = await self._detail(current, previous)
        await self.session.commit()
        return detail

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

    async def _detail(
        self, offer: OfferRecord, previous: str | None = None
    ) -> OfferDetail:
        """The offer with its links and the review token of exactly that snapshot.

        Write paths call it before ``commit()``, inside the transaction, so a failure rolls the write back.
        """
        links = await self.store.list_links(offer.id)
        refs = await self.store.review_refs(offer.category_id, offer.merchant_id)
        provenance = await self.store.get_provenance(offer.id)
        return OfferDetail(
            offer,
            links,
            compute_review_token(offer, links, refs),
            previous,
            provenance,
        )

    async def _stored_token(self, locked: OfferRecord) -> str:
        """The token of the state stored now, read under the offer's row lock.

        Order: links (the offer lock covers every writer of them), then the category and the merchant
        FOR SHARE, so an edit of either waits for this transaction.
        """
        links = await self.store.list_links(locked.id)
        refs = await self.store.review_refs(
            locked.category_id, locked.merchant_id, share=True
        )
        return compute_review_token(locked, links, refs)

    async def _require_unchanged(self, locked: OfferRecord, token: str | None) -> str:
        """The editor's token must match what is stored now; returns it (the state the write replaces)."""
        if not token:
            raise InvalidInput("review_token_required")
        current = await self._stored_token(locked)
        if current != token:
            raise OfferChanged()
        return current

    async def _require_references(self, category_id: str, merchant_id: str) -> None:
        if await self.store.category_enabled(category_id) is None:
            raise InvalidInput("unknown_category")
        if await self.store.merchant_active(merchant_id) is None:
            raise InvalidInput("unknown_merchant")

    async def _basis_unconfirmed(self, offer_id: str, *, confirmed: bool) -> bool:
        """True when every active link is ``direct`` (at least one) and the editor confirmed no basis."""
        if confirmed:
            return False
        active = [item for item in await self.store.list_links(offer_id) if item.active]
        return bool(active) and all(item.provider == DIRECT_PROVIDER for item in active)

    async def _require_publishable(
        self,
        offer: OfferRecord,
        *,
        check_end: bool = True,
        basis_confirmed: bool = True,
    ) -> None:
        facts = PublishFacts(
            category_enabled=bool(await self.store.category_enabled(offer.category_id)),
            merchant_active=bool(await self.store.merchant_active(offer.merchant_id)),
            active_links=await self.store.count_active_links(offer.id),
            has_source_url=bool(offer.source_url),
            end_at=offer.end_at if check_end else None,
            last_verified_at=offer.last_verified_at,
            basis_unconfirmed=await self._basis_unconfirmed(
                offer.id, confirmed=basis_confirmed
            ),
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
