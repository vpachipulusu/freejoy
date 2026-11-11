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
from app.core.plain_text import TextTooLong, plain_line
from app.models.platform.offer import OfferStatus, OfferType, PostageKind
from app.repositories.admin_offers import OfferRecord, PartnerSubmissionRecord
from app.services.admin_catalogue_errors import (
    IllegalTransition,
    InvalidInput,
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


# Caps of the proposal's texts as the admin edit form takes them: a stored proposal is checked again here, so a
# payload that did not come from the partner API's own cleaning (a tampered row) cannot put markup or oversized text on an offer.
LINE_CAPS = {
    "title": 200,
    "summary": 300,
    "eligibility": 500,
    "availability": 500,
    "subscription_terms": 1000,
}
PARAGRAPH_CAP = 2000
STEP_TITLE_CAP = 120
STEP_DETAIL_CAP = 1000
MAX_PARAGRAPHS = 20
MAX_STEPS = 20
BAD_PROPOSAL = "invalid_pending_payload"


def _bad() -> InvalidInput:
    return InvalidInput(BAD_PROPOSAL)


def _date(value: Any) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise _bad()
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise _bad() from None
    if parsed.utcoffset() is None:
        raise _bad()
    return parsed


def _clean_line(value: Any, cap: int, *, optional: bool = False) -> str | None:
    """A text that is already plain: cleaning it again must change nothing (and it must not be empty)."""
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise _bad()
    try:
        cleaned = plain_line(value, cap)
    except TextTooLong:
        raise _bad() from None
    if cleaned != value or not cleaned:
        raise _bad()
    return cleaned


def _paragraphs(value: Any) -> list[str]:
    if not isinstance(value, list) or len(value) > MAX_PARAGRAPHS:
        raise _bad()
    return [str(_clean_line(item, PARAGRAPH_CAP)) for item in value]


def _steps(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list) or len(value) > MAX_STEPS:
        raise _bad()
    steps: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict) or set(item) != {"title", "detail"}:
            raise _bad()
        steps.append(
            {
                "title": str(_clean_line(item["title"], STEP_TITLE_CAP)),
                "detail": str(_clean_line(item["detail"], STEP_DETAIL_CAP)),
            }
        )
    return steps


def _enum(kind: type[OfferType] | type[PostageKind], value: Any) -> Any:
    try:
        return kind(value)
    except ValueError:
        raise _bad() from None


def proposed_changes(payload: Mapping[str, Any]) -> dict[str, Any]:
    """The offer columns a stored proposal would write, checked again and typed as the edit path expects them.

    A proposal that is missing a field, has a wrong type, text that is not plain or an unknown vocabulary value is
    refused with ``InvalidInput("invalid_pending_payload")`` (a 422), never a crash and never written.
    """
    missing = [name for name in CONTENT_FIELDS if name not in payload]
    if missing:
        raise _bad()
    changes: dict[str, Any] = {
        name: _clean_line(payload[name], cap, optional=name == "subscription_terms")
        for name, cap in LINE_CAPS.items()
    }
    category_id, requires_card = payload["category_id"], payload["requires_card"]
    amount, currency = payload["postage_amount_minor"], payload["postage_currency"]
    if not isinstance(category_id, str) or not isinstance(requires_card, bool):
        raise _bad()
    if amount is not None and (not isinstance(amount, int) or isinstance(amount, bool)):
        raise _bad()
    if currency is not None and not isinstance(currency, str):
        raise _bad()
    changes.update(
        description=_paragraphs(payload["description"]),
        steps=_steps(payload["steps"]),
        category_id=category_id,
        requires_card=requires_card,
        postage_amount_minor=amount,
        postage_currency=currency,
        offer_type=_enum(OfferType, payload["offer_type"]),
        postage_kind=_enum(PostageKind, payload["postage_kind"]),
        start_at=_date(payload["start_at"]),
        end_at=_date(payload["end_at"]),
    )
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
        destination = payload.get("destination_url")
        if not isinstance(destination, str):
            raise _bad()
        await self._stage_update(actor, offer_id, changes, None)
        link_updated = await self._apply_destination(actor, offer_id, destination)
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
