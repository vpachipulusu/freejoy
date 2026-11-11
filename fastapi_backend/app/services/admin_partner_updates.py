"""Admin review of an update a retailer sent for an offer an editor has already touched (ADR-013, FREEJ-58).

The partner API never changes an offer that is not an untouched draft: it stores the sanitised proposal in
``partner_submissions.pending_*`` and sets the "source changed" marker, so the offer shows up in the review queue.
Two things an editor can do with it, both through the offer lock and the review token of ``AdminOfferService``:

* ``apply_partner_update`` writes the proposed content through the normal edit path (``_stage_update``: the same
  validation, publish checks and audit row as a hand edit; the commercial basis is not part of a proposal and is never
  changed here, so ``_guard_basis_change`` has nothing to refuse and nothing slips past it). It needs the
  ``review_token`` the editor loaded, and the hash of the proposal the editor was shown, so neither a changed offer
  nor a newer proposal is applied blind. On a PUBLISHED offer it also needs ``offers.publish``: it changes what
  visitors read. The retailer's link is updated through the normal link edit (host rule included) when the offer has
  exactly one ``direct`` link. Then the pending proposal and the marker are cleared and the provenance row records
  that the offer reflects the proposal. An offer that has already ended keeps its end date unless the proposal gives a
  new one in the future, so an old proposal cannot reopen what the retailer later ended.
* ``dismiss_partner_update`` clears the proposal and the marker and changes nothing else.

Lock order: the offer, then its provenance row, then its submission row (as the partner API takes them). One commit.
"""

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from app.core.permissions import AdminContext
from app.models.platform.offer import OfferStatus, OfferType, PostageKind
from app.repositories.admin_offers import OfferRecord, PartnerSubmissionRecord
from app.services.admin_catalogue_errors import (
    IllegalTransition,
    OfferChanged,
    PermissionDenied,
)
from app.services.admin_offers import (
    PARTNER_PROVIDER,
    RESOURCE,
    AdminOfferService,
    OfferDetail,
)

APPLY_ACTION = "partner_update_applied"
DISMISS_ACTION = "partner_update_dismissed"
DIRECT = "direct"
# What a proposal carries that is a column of ``offers`` (the link destination is applied through the link edit).
CONTENT_FIELDS = (
    "title",
    "summary",
    "description",
    "category_id",
    "offer_type",
    "postage_kind",
    "postage_amount_minor",
    "postage_currency",
    "requires_card",
    "subscription_terms",
    "eligibility",
    "availability",
    "steps",
    "start_at",
    "end_at",
)


def _date(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def proposed_changes(payload: Mapping[str, Any]) -> dict[str, Any]:
    """The offer columns a stored proposal would write, typed as the edit path expects them."""
    changes = {name: payload[name] for name in CONTENT_FIELDS}
    changes["offer_type"] = OfferType(changes["offer_type"])
    changes["postage_kind"] = PostageKind(changes["postage_kind"])
    changes["start_at"] = _date(changes["start_at"])
    changes["end_at"] = _date(changes["end_at"])
    return changes


def keep_an_ended_offer_ended(
    current: OfferRecord, changes: dict[str, Any], now: datetime
) -> dict[str, Any]:
    """Drop the proposed ``end_at`` when the offer has already ended and the proposal would not give it a future one."""
    ended = current.end_at is not None and current.end_at <= now
    proposed = changes["end_at"]
    if ended and (proposed is None or proposed <= now):
        return {name: value for name, value in changes.items() if name != "end_at"}
    return changes


class AdminPartnerUpdateService(AdminOfferService):
    async def apply_partner_update(
        self,
        actor: AdminContext,
        offer_id: str,
        review_token: str | None,
        pending_hash: str,
    ) -> OfferDetail:
        current, previous, submission = await self._load_pending(
            offer_id, review_token, pending_hash, APPLY_ACTION
        )
        if (
            current.status is OfferStatus.PUBLISHED
            and "offers.publish" not in actor.permissions
        ):
            raise PermissionDenied()
        payload = submission.pending_payload or {}
        changes = keep_an_ended_offer_ended(
            current, proposed_changes(payload), self.clock()
        )
        await self._stage_update(actor, offer_id, changes, None)
        link_updated = await self._apply_destination(
            actor, offer_id, str(payload["destination_url"])
        )
        await self.store.clear_partner_pending(offer_id, applied_hash=pending_hash)
        await self._record(actor, offer_id, APPLY_ACTION, submission, link_updated)
        stored = await self._locked(offer_id)
        detail = await self._detail(stored, previous)
        await self.session.commit()
        return detail

    async def dismiss_partner_update(
        self,
        actor: AdminContext,
        offer_id: str,
        review_token: str | None,
        pending_hash: str,
    ) -> OfferDetail:
        current, previous, submission = await self._load_pending(
            offer_id, review_token, pending_hash, DISMISS_ACTION
        )
        await self.store.clear_partner_pending(offer_id)
        await self._record(actor, offer_id, DISMISS_ACTION, submission, None)
        detail = await self._detail(current, previous)
        await self.session.commit()
        return detail

    async def _load_pending(
        self,
        offer_id: str,
        review_token: str | None,
        pending_hash: str,
        action: str,
    ) -> tuple[OfferRecord, str, PartnerSubmissionRecord]:
        """Lock, check the token, then require a proposal that is the one the editor saw (409 otherwise)."""
        current = await self._locked(offer_id)
        previous = await self._require_unchanged(current, review_token)
        provenance = await self.store.get_provenance(offer_id, lock=True)
        if provenance is None or provenance.provider != PARTNER_PROVIDER:
            raise IllegalTransition(current.status.value, action)
        submission = await self.store.get_partner_submission(offer_id, lock=True)
        if submission is None or submission.pending_payload is None:
            raise IllegalTransition(current.status.value, action)
        if submission.pending_hash != pending_hash:
            raise OfferChanged()
        return current, previous, submission

    async def _apply_destination(
        self, actor: AdminContext, offer_id: str, destination_url: str
    ) -> bool:
        """Point the retailer's one ``direct`` link at the proposed URL, through the normal link edit."""
        direct = [
            link
            for link in await self.store.list_links(offer_id)
            if link.provider == DIRECT
        ]
        if len(direct) != 1 or direct[0].destination_url == destination_url:
            return False
        await self._stage_link_update(
            actor, offer_id, direct[0].id, {"destination_url": destination_url}
        )
        return True

    async def _record(
        self,
        actor: AdminContext,
        offer_id: str,
        action: str,
        submission: PartnerSubmissionRecord,
        link_updated: bool | None,
    ) -> None:
        details: dict[str, Any] = {
            "ref": submission.ref,
            "merchant_id": submission.merchant_id,
            "pending_hash": submission.pending_hash,
        }
        if link_updated is not None:
            details["link_updated"] = link_updated
        await self.auditor.record(
            actor,
            action=f"admin.offers.{action}",
            resource=RESOURCE,
            resource_id=offer_id,
            details=details,
        )
