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
  active links are on no affiliate network (``needs_basis_confirmation``) also needs the editor's explicit
  confirmation of the basis in the publish request; without it the publish is refused as
  ``commercial_basis_required``, and a confirmation that is not the stored basis is ``OfferChanged``. A link change
  that would turn a PUBLISHED offer from "needs no confirmation" into "needs it" is refused the same way (unpublish,
  change the links, publish again), so a live offer never sits in that state unconfirmed. Changing the basis
  itself needs the ``review_token`` the editor loaded (``OfferChanged`` otherwise, nothing written), so a stale
  form cannot overwrite it, and on a PUBLISHED offer also ``offers.publish`` (``PermissionDenied``).
* URLs are validated here (https only, see ``app.core.catalogue_urls``); the slug is derived
  from the title unless the editor supplies one, and cannot be changed afterwards.
"""

from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from typing import Any, Protocol
from urllib.parse import urlsplit

from sqlalchemy.exc import IntegrityError

from app.core.affiliate_hosts import destination_allowed, is_known_provider
from app.core.config import settings
from app.core.permissions import AdminContext
from app.core.slugs import CATALOGUE_SLUG_MAX_LENGTH, InvalidSlug, slugify
from app.models.platform.offer import CommercialBasis, OfferStatus
from app.repositories.admin_offers import (
    AffiliateLinkRecord,
    OfferFilters,
    OfferPage,
    OfferProvenance,
    OfferRecord,
    PartnerSubmissionRecord,
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
    PermissionDenied,
    PublishBlocked,
    SlugTaken,
)
from app.services.offer_fingerprint import review_token as compute_review_token
from app.services.offer_lifecycle import (
    OfferAction,
    PublishFacts,
    PublishReason,
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
    needs_basis_confirmation,
)

__all__ = ["AdminOfferService", "OfferDetail"]

LINK_FIELDS = frozenset({"provider", "destination_url", "tracking_id", "active"})
LINK_AUDITED = frozenset({"provider", "active"})
RESOURCE = "offers"
DISMISS_ACTION = "dismiss_source_change"
PARTNER_PROVIDER = "partner"
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
    async def get_partner_submission(
        self, offer_id: str, *, lock: bool = False
    ) -> PartnerSubmissionRecord | None: ...
    async def clear_partner_pending(
        self, offer_id: str, *, applied_hash: str | None = None
    ) -> None: ...
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
    # The retailer behind an offer submitted through the partner API, and its waiting update (None otherwise).
    partner: PartnerSubmissionRecord | None = None


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
        self,
        actor: AdminContext,
        offer_id: str,
        changes: Mapping[str, Any],
        review_token: str | None = None,
    ) -> OfferDetail:
        """Edit an offer. ``review_token`` is needed only when the request changes the commercial basis."""
        detail = await self._stage_update(actor, offer_id, changes, review_token)
        await self.session.commit()
        return detail

    async def _stage_update(
        self,
        actor: AdminContext,
        offer_id: str,
        changes: Mapping[str, Any],
        review_token: str | None,
    ) -> OfferDetail:
        """Every step of an edit up to the audit row, without the commit (``apply_partner_update`` adds to it)."""
        if not changes or not changes.keys() <= EDITABLE_FIELDS:
            raise ValueError("Invalid offer fields")
        current = await self._locked(offer_id)
        previous = await self._stored_token(current)
        require_editable(current.status)
        wanted_merchant = changes.get("merchant_id")
        if (
            wanted_merchant is not None
            and wanted_merchant != current.merchant_id
            and await self._is_partner_submission(offer_id)
        ):
            # The retailer's submission is keyed by its merchant: moving the offer would orphan it.
            raise InvalidInput("partner_merchant_fixed")
        self._guard_basis_change(actor, current, changes, review_token, previous)
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
        return await self._detail(updated, previous)

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
        partner = False
        if action is OfferAction.PUBLISH:
            previous = await self._require_unchanged(current, review_token)
            partner = await self._is_partner_submission(offer_id)
            if partner and commercial_basis is None:
                # A retailer's draft carries a placeholder basis that nobody chose: the editor states one.
                raise PublishBlocked([PublishReason.COMMERCIAL_BASIS_REQUIRED.value])
            if (
                not partner
                and commercial_basis is not None
                and commercial_basis != current.commercial_basis
            ):
                raise OfferChanged()
        else:
            previous = await self._stored_token(current)
        target = next_status(current.status, action)
        if action is OfferAction.PUBLISH:
            await self._require_publishable(
                current, basis_confirmed=commercial_basis is not None
            )
        changes: dict[str, Any] = {"status": target}
        if partner and commercial_basis is not None:
            changes["commercial_basis"] = (
                commercial_basis  # the editor's statement replaces the placeholder
            )
        if partner and action is OfferAction.PUBLISH:
            fresh = await self._fresh_partner_slug(current)
            if fresh is not None:
                changes["slug"] = fresh
        updated = await self.store.update(offer_id, changes)
        await self.auditor.record(
            actor,
            action=f"admin.offers.{action.value}",
            resource=RESOURCE,
            resource_id=offer_id,
            before=(
                {
                    "status": current.status.value,
                    "commercial_basis": current.commercial_basis.value,
                }
                if partner
                else {"status": current.status.value}
            ),
            after=self._transition_after(action, target, updated),
            details=(
                {"basis_confirmed": commercial_basis is not None}
                if action is OfferAction.PUBLISH
                else None
            ),
        )
        detail = await self._detail(updated, previous)
        await self.session.commit()
        return detail

    @staticmethod
    def _transition_after(
        action: OfferAction, target: OfferStatus, current: OfferRecord
    ) -> dict[str, Any]:
        """The audit ``after`` of a transition: the status, and for publish the basis it went live with."""
        if action is not OfferAction.PUBLISH:
            return {"status": target.value}
        return {
            "status": target.value,
            "commercial_basis": current.commercial_basis.value,
        }

    @staticmethod
    def _guard_basis_change(
        actor: AdminContext,
        current: OfferRecord,
        changes: Mapping[str, Any],
        review_token: str | None,
        stored_token: str,
    ) -> None:
        """Changing the commercial basis needs the loaded token, and ``offers.publish`` when the offer is live.

        A form loaded before somebody changed the basis sends every field back, so without the token a stale
        edit would silently undo a sponsorship. Both refusals happen before anything is written.
        """
        wanted = changes.get("commercial_basis")
        if wanted is None or wanted == current.commercial_basis:
            return
        if (
            current.status is OfferStatus.PUBLISHED
            and "offers.publish" not in actor.permissions
        ):
            raise PermissionDenied()
        if not review_token or review_token != stored_token:
            raise OfferChanged()

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
        if (
            marked is None
            or marked.source_changed_at is None
            or marked.provider
            == PARTNER_PROVIDER  # a retailer's update has its own review (dismiss-partner-update)
        ):
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
        await self._require_confirmation_not_newly_needed(
            offer,
            [
                *await self._provider_pairs(offer_id),
                (values["provider"], values.get("active", True)),
            ],
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
        updated = await self._stage_link_update(actor, offer_id, link_id, changes)
        await self.session.commit()
        return updated

    async def _stage_link_update(
        self,
        actor: AdminContext,
        offer_id: str,
        link_id: str,
        changes: Mapping[str, Any],
    ) -> AffiliateLinkRecord:
        """Every step of a link edit up to the audit row, without the commit."""
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
        await self._require_confirmation_not_newly_needed(
            offer,
            [
                (
                    (
                        values.get("provider", link.provider),
                        values.get("active", link.active),
                    )
                    if item_id == link_id
                    else (provider, active)
                )
                for item_id, provider, active in await self._link_rows(offer_id)
            ],
        )
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
        partner = (
            await self.store.get_partner_submission(offer.id)
            if provenance is not None and provenance.provider == PARTNER_PROVIDER
            else None
        )
        return OfferDetail(
            offer,
            links,
            compute_review_token(offer, links, refs),
            previous,
            provenance,
            partner,
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

    async def _link_rows(self, offer_id: str) -> list[tuple[str, str, bool]]:
        return [
            (item.id, item.provider, item.active)
            for item in await self.store.list_links(offer_id)
        ]

    async def _provider_pairs(self, offer_id: str) -> list[tuple[str, bool]]:
        return [
            (provider, active)
            for _id, provider, active in await self._link_rows(offer_id)
        ]

    async def _require_confirmation_not_newly_needed(
        self, offer: OfferRecord, after: list[tuple[str, bool]]
    ) -> None:
        """A link change may not leave a PUBLISHED offer needing a basis confirmation it never gave.

        Only the change from "needs none" to "needs one" is refused; the editor unpublishes, changes the
        links and publishes again with the confirmation.
        """
        if offer.status is not OfferStatus.PUBLISHED:
            return
        before = await self._provider_pairs(offer.id)
        if not needs_basis_confirmation(before) and needs_basis_confirmation(after):
            raise PublishBlocked([PublishReason.COMMERCIAL_BASIS_REQUIRED.value])

    async def _basis_unconfirmed(self, offer_id: str, *, confirmed: bool) -> bool:
        """True when publishing needs a confirmed basis (see ``needs_basis_confirmation``) and none was sent."""
        if confirmed:
            return False
        if await self._is_partner_submission(offer_id):
            # The basis of a retailer's draft is a placeholder (nothing set it), whatever links an editor added.
            return True
        return needs_basis_confirmation(await self._provider_pairs(offer_id))

    async def _fresh_partner_slug(self, current: OfferRecord) -> str | None:
        """For a retailer's draft that was never live, the slug rebuilt from the title as it is now (token kept).

        The slug was made from the title the retailer first sent, which an editor may never have seen; a retailer
        could otherwise keep keywords in the public address after changing the title. Once published it is fixed.
        """
        partner = await self.store.get_partner_submission(current.id)
        if partner is None or partner.first_published_at is not None:
            return None
        token = current.slug.rsplit("-", 1)[-1]
        try:
            base = slugify(current.title, CATALOGUE_SLUG_MAX_LENGTH - len(token) - 1)
        except InvalidSlug:
            base = "offer"
        slug = f"{base}-{token}"
        if slug == current.slug or await self.store.slug_exists(slug):
            return None
        return slug

    async def _is_partner_submission(self, offer_id: str) -> bool:
        provenance = await self.store.get_provenance(offer_id)
        return provenance is not None and provenance.provider == PARTNER_PROVIDER

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
