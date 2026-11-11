"""The partner offer submissions: create, update, read, list and end (ADR-013, FREEJ-58). Nothing here publishes.

A submission only ever becomes a ``DRAFT`` offer. The rules, in the order they apply to a ``PUT``:

* The content is cleaned and validated (``app.partner.content``) before anything is written; a refusal writes nothing.
* **New ref:** one draft offer (the status is the column default, the role cannot name it), its one ``direct`` link,
  an ``offer_imports`` row with provider ``partner`` (that is what lists it in the editors' review queue, with no
  change to ``needs_review``), the ``partner_submissions`` row and an audit row, in one transaction. Under a
  per-merchant advisory lock the two caps are counted first (``app.partner.caps``). The unique
  ``(merchant_id, ref)`` is the arbiter if two requests still race: the loser rolls back its attempt and carries on as
  an update.
* **Known ref, same content hash:** nothing is written.
* **Known ref, untouched draft** (``app.core.offer_fingerprint.is_untouched_draft``, the rule the Awin importer uses): the
  draft is refreshed in place and the importer-style hashes move with it.
* **Known ref, anything else** (an edited draft, a live or ended offer): the sanitised content is stored as the
  pending update and the "source changed" marker is set; the offer, and so the published page, does not change.
* **Archived:** refused (``offer_closed``); an editor's decision stands, the retailer uses a new ref.

Lock order, as in ``AdminOfferService``: the offer row first, then its provenance row, then the submission row. The
partner side never calls ``AdminOfferService.transition`` or ``verify`` (it cannot import them, and the role cannot
write ``status`` or ``last_verified_at``).
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.ids import new_id
from app.core.offer_content import IMAGE_ALT, IMAGE_LABEL_LENGTH, IMAGE_TINT
from app.core.offer_fingerprint import fingerprint, is_untouched_draft
from app.core.offer_vocabulary import STATUS_ARCHIVED
from app.core.plain_text import fit_line
from app.core.slugs import CATALOGUE_SLUG_MAX_LENGTH, InvalidSlug, slugify
from app.partner.auth import PartnerContext
from app.partner.caps import MAX_NEW_SUBMISSIONS_PER_DAY, MAX_OPEN_SUBMISSIONS
from app.partner.content import (
    CleanContent,
    clean_submission,
    require_current_window,
)
from app.partner.errors import SubmissionBusy, SubmissionNotFound, SubmissionRefused
from app.partner.schemas import (
    EndResult,
    OfferSubmission,
    Outcome,
    SubmissionResult,
    SubmissionsPage,
    SubmissionView,
)
from app.partner.statuses import LIVE, partner_status
from app.partner.submission_store import (
    CategoryReader,
    ImportRow,
    MerchantFacts,
    OfferRow,
    PartnerSubmissionStore,
    SubmissionRow,
    ViewRow,
)

logger = logging.getLogger("yourapp.partner")

ACTION_SUBMIT = "partner.offers.submit"
ACTION_UPDATE = "partner.offers.update"
ACTION_END = "partner.offers.end"
ACTOR = "partner_api"
SLUG_CONSTRAINT = "uq_offers_slug"
REF_CONSTRAINT = "uq_partner_submissions_merchant_id_ref"
SLUG_TOKEN_LENGTH = 8
SLUG_ATTEMPTS = 3

Clock = Callable[[], datetime]
UpdateOutcome = Literal["refreshed", "pending_update", "cleared"]


def utc_now() -> datetime:
    return datetime.now(UTC)


def violated_constraint(error: IntegrityError) -> str | None:
    """The name of the constraint a unique or check violation names (asyncpg puts it on the wrapped exception)."""
    cause = getattr(error.orig, "__cause__", None)
    name = getattr(cause, "constraint_name", None) or getattr(
        error.orig, "constraint_name", None
    )
    return name if isinstance(name, str) else None


def draft_slug(title: str) -> str:
    """The slug of a new draft: the title, then a random token, so no other merchant's slug is ever probed or taken."""
    token = new_id()[:SLUG_TOKEN_LENGTH]
    try:
        base = slugify(title, CATALOGUE_SLUG_MAX_LENGTH - SLUG_TOKEN_LENGTH - 1)
    except InvalidSlug:
        return f"offer-{token}"
    return f"{base}-{token}"


def public_url(slug: str) -> str:
    return f"{settings.PUBLIC_SITE_URL.rstrip('/')}/freebies/{slug}"


def view_of(row: ViewRow) -> SubmissionView:
    status = partner_status(
        offer_status=row.offer_status,
        past_end=row.past_end,
        ever_published=row.ever_published,
    )
    return SubmissionView(
        ref=row.ref,
        status=status,
        title=row.title,
        # A disabled category hides its offers from the public site, so a link to the page would be a 404.
        public_url=(
            public_url(row.slug) if status == LIVE and row.category_enabled else None
        ),
        update_pending=row.update_pending,
        submitted_at=row.submitted_at,
        ends_at=row.end_at,
    )


@dataclass(frozen=True)
class _Locked:
    """The rows a write on an existing submission holds, locked in the order offer, provenance, submission."""

    offer: OfferRow
    provenance: ImportRow
    submission: SubmissionRow


class SubmissionService:
    def __init__(
        self,
        session: AsyncSession,
        context: PartnerContext,
        *,
        clock: Clock = utc_now,
        store: PartnerSubmissionStore | None = None,
        categories: CategoryReader | None = None,
    ) -> None:
        """``store`` and ``categories`` default to the Core implementations on ``session``; tests pass fakes."""
        self._session = session
        self._context = context
        self._merchant_id = context.merchant_id
        self._store = store or PartnerSubmissionStore(session)
        self._categories = categories or CategoryReader(session)
        self._clock = clock

    # --- PUT ---------------------------------------------------------------------------------

    async def submit(self, ref: str, submission: OfferSubmission) -> SubmissionResult:
        merchant = await self._store.merchant_facts(self._merchant_id)
        if merchant is None:
            raise SubmissionNotFound
        category_id = await self._categories.enabled_category_id(submission.category)
        if category_id is None:
            raise SubmissionRefused("unknown_category")
        content = clean_submission(
            submission,
            category_id=category_id,
            merchant_website=merchant.website,
        )
        found = await self._store.find_submission(self._merchant_id, ref)
        if found is not None and found.content_hash == content.hash:
            unchanged = await self._unchanged_without_locks(ref)
            if unchanged is not None:
                return unchanged
        if found is None:
            created = await self._create(ref, content, merchant)
            if created is not None:
                return created
        return await self._update(ref, content)

    async def _unchanged_without_locks(self, ref: str) -> SubmissionResult | None:
        """The commonest call of a sync job, the same body again, answered with no row lock at all.

        A concurrent change just orders this request before it. An archived offer still gets the locked path
        (and its ``offer_closed``), so this only answers for an offer an editor has not closed.
        """
        row = await self._store.view(self._merchant_id, ref)
        if row is None or row.offer_status == STATUS_ARCHIVED:
            return None
        return self._result_of(row, "unchanged")

    def _require_future_end(self, content: CleanContent) -> None:
        """A new or changed submission must not already have ended (a resend of what is stored is judged before this)."""
        require_current_window(content.start_at, content.end_at, self._clock())

    async def _create(
        self, ref: str, content: CleanContent, merchant: MerchantFacts
    ) -> SubmissionResult | None:
        """The new submission, or ``None`` when another request created this ref first (the caller then updates)."""
        store, merchant_id = self._store, self._merchant_id
        self._require_future_end(content)
        await store.lock_creation(merchant_id)
        if await store.find_submission(merchant_id, ref) is not None:
            return None
        await self._require_room()
        submission_id = new_id()
        for _attempt in range(SLUG_ATTEMPTS):
            try:
                async with self._session.begin_nested():
                    offer = await self._insert_draft(
                        ref, submission_id, content, merchant
                    )
            except IntegrityError as error:
                violated = violated_constraint(error)
                if violated == REF_CONSTRAINT:
                    return None
                if violated != SLUG_CONSTRAINT:
                    raise
                continue
            await self._audit(ACTION_SUBMIT, offer.id, ref, "created", content.hash)
            result = await self._result(ref, "created")
            await self._session.commit()
            return result
        raise SubmissionBusy

    async def _require_room(self) -> None:
        if await self._store.count_open(self._merchant_id) >= MAX_OPEN_SUBMISSIONS:
            raise SubmissionRefused("open_submission_cap")
        if (
            await self._store.count_created_today(self._merchant_id)
            >= MAX_NEW_SUBMISSIONS_PER_DAY
        ):
            raise SubmissionRefused("daily_submission_cap")

    async def _insert_draft(
        self,
        ref: str,
        submission_id: str,
        content: CleanContent,
        merchant: MerchantFacts,
    ) -> OfferRow:
        """Offer, submission, link, provenance: the order the database guards need (each checks the one before)."""
        store, merchant_id = self._store, self._merchant_id
        offer = await store.insert_offer(
            merchant_id,
            {
                **content.offer_values(),
                "slug": draft_slug(content.title),
                "image_tint": IMAGE_TINT,
                "image_label": fit_line(merchant.name, IMAGE_LABEL_LENGTH),
                "image_alt": IMAGE_ALT,
            },
        )
        await store.insert_submission(
            merchant_id,
            submission_id=submission_id,
            ref=ref,
            offer_id=offer.id,
            content_hash=content.hash,
            key_id=self._context.key_id,
        )
        link = await store.insert_link(merchant_id, offer.id, content.destination_url)
        await store.insert_import(
            merchant_id,
            offer_id=offer.id,
            external_id=submission_id,
            digest=content.hash,
            applied_content_hash=fingerprint(offer, [link]),
        )
        return offer

    async def _update(self, ref: str, content: CleanContent) -> SubmissionResult:
        locked = await self._lock(ref)
        if locked.offer.status == STATUS_ARCHIVED:
            raise SubmissionRefused("offer_closed")
        digest = content.hash
        if digest == locked.submission.content_hash:
            result = await self._result(ref, "unchanged")
            await self._session.rollback()  # nothing changed: release the locks
            return result
        self._require_future_end(content)
        links = await self._store.links(self._merchant_id, locked.offer.id)
        untouched = is_untouched_draft(
            status=locked.offer.status,
            link_count=len(links),
            current_fingerprint=fingerprint(locked.offer, links),
            applied_fingerprint=locked.provenance.applied_content_hash,
        )
        if untouched:
            outcome = await self._refresh(locked, links[0].id, content)
        else:
            outcome = await self._queue(locked, content)
        await self._audit(ACTION_UPDATE, locked.offer.id, ref, outcome, digest)
        result = await self._result(
            ref, "unchanged" if outcome == "cleared" else outcome
        )
        await self._session.commit()
        return result

    async def _lock(self, ref: str) -> _Locked:
        """Offer, provenance and submission, in the admin's order, with ``FOR NO KEY UPDATE`` (it excludes every
        writer but does not block a click's foreign-key check on the offer)."""
        store, merchant_id = self._store, self._merchant_id
        found = await store.find_submission(merchant_id, ref)
        if found is None:
            raise SubmissionNotFound
        offer = await store.lock_offer(merchant_id, found.offer_id)
        provenance = await store.import_row(merchant_id, found.offer_id, lock=True)
        submission = await store.find_submission(merchant_id, ref, lock=True)
        if offer is None or provenance is None or submission is None:
            logger.error("partner submission without its offer or provenance")
            raise SubmissionNotFound
        return _Locked(offer, provenance, submission)

    async def _refresh(
        self, locked: _Locked, link_id: str, content: CleanContent
    ) -> UpdateOutcome:
        """An untouched draft takes the new content as it stands, and the hashes follow it."""
        store, merchant_id = self._store, self._merchant_id
        offer_id, digest = locked.offer.id, content.hash
        updated = await store.update_offer(
            merchant_id, offer_id, content.offer_values()
        )
        link = await store.update_link(
            merchant_id, offer_id, link_id, content.destination_url
        )
        await store.update_import(
            merchant_id,
            offer_id,
            {
                "source_hash": digest,
                "applied_source_hash": digest,
                "applied_content_hash": fingerprint(updated, [link]),
                "applied_at": func.now(),
                "source_changed_at": None,
            },
        )
        await store.update_submission(
            merchant_id, locked.submission.id, self._submission_values(digest)
        )
        return "refreshed"

    async def _queue(self, locked: _Locked, content: CleanContent) -> UpdateOutcome:
        """Anything that is not an untouched draft keeps its content: the proposal waits for an editor."""
        store, merchant_id = self._store, self._merchant_id
        digest = content.hash
        if digest == locked.provenance.applied_source_hash:
            # The retailer is back to what the offer already reflects: nothing is left for an editor to decide.
            await store.update_import(
                merchant_id,
                locked.offer.id,
                {"source_hash": digest, "source_changed_at": None},
            )
            await store.update_submission(
                merchant_id, locked.submission.id, self._submission_values(digest)
            )
            return "cleared"
        await store.update_import(
            merchant_id,
            locked.offer.id,
            {"source_hash": digest, "source_changed_at": func.now()},
        )
        await store.update_submission(
            merchant_id,
            locked.submission.id,
            {
                **self._submission_values(digest),
                "pending_payload": content.payload(),
                "pending_hash": digest,
                "pending_at": func.now(),
            },
        )
        return "pending_update"

    def _submission_values(self, digest: str) -> dict[str, Any]:
        """The submission columns every accepted update sets: the new hash, no pending proposal, this key."""
        return {
            "content_hash": digest,
            "pending_payload": None,
            "pending_hash": None,
            "pending_at": None,
            "key_id": self._context.key_id,
        }

    # --- end ---------------------------------------------------------------------------------

    async def end(self, ref: str) -> EndResult:
        """Set ``end_at`` to now, on the database clock, unless the offer has ended already. Never touches ``status``.

        A proposal still waiting for an editor was sent before the retailer said the offer was over, so ending
        supersedes it: the proposal and the marker are cleared and the stored hash goes back to the content the offer
        reflects (a later update then queues normally, and an editor cannot apply an old proposal over the end).
        """
        locked = await self._lock(ref)
        changed = False
        superseded = False
        if locked.offer.status != STATUS_ARCHIVED:
            changed = await self._store.end_offer(self._merchant_id, locked.offer.id)
        if changed:
            superseded = locked.submission.pending_hash is not None
            if superseded:
                await self._supersede_pending(locked)
            await self._audit(
                ACTION_END,
                locked.offer.id,
                ref,
                "ended",
                None,
                {"superseded_pending": True} if superseded else None,
            )
        view = await self._require_view(ref)
        if changed:
            await self._session.commit()
        else:
            await self._session.rollback()
        return EndResult(**view_of(view).model_dump(), changed=changed)

    async def _supersede_pending(self, locked: _Locked) -> None:
        applied = locked.provenance.applied_source_hash
        await self._store.update_import(
            self._merchant_id,
            locked.offer.id,
            {"source_hash": applied, "source_changed_at": None},
        )
        await self._store.update_submission(
            self._merchant_id, locked.submission.id, self._submission_values(applied)
        )

    # --- reads -------------------------------------------------------------------------------

    async def get(self, ref: str) -> SubmissionView:
        return view_of(await self._require_view(ref))

    async def list_submissions(self, limit: int, offset: int) -> SubmissionsPage:
        rows, total = await self._store.list_views(self._merchant_id, limit, offset)
        return SubmissionsPage(
            offers=[view_of(row) for row in rows],
            total_count=total,
            limit=limit,
            offset=offset,
        )

    async def _require_view(self, ref: str) -> ViewRow:
        row = await self._store.view(self._merchant_id, ref)
        if row is None:
            raise SubmissionNotFound
        return row

    def _result_of(self, row: ViewRow, outcome: Outcome) -> SubmissionResult:
        return SubmissionResult(**view_of(row).model_dump(), outcome=outcome)

    async def _result(self, ref: str, outcome: Outcome) -> SubmissionResult:
        """Read inside the write's own transaction (before its commit), so the answer costs no extra connection."""
        return self._result_of(await self._require_view(ref), outcome)

    async def _audit(
        self,
        action: str,
        offer_id: str,
        ref: str,
        outcome: str,
        digest: str | None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """Ids, the ref the retailer chose (a plain identifier), the outcome and a hash: never any copy."""
        metadata: dict[str, Any] = {
            "actor": ACTOR,
            "key_id": self._context.key_id,
            "ref": ref,
            "outcome": outcome,
            **(extra or {}),
        }
        if digest is not None:
            metadata["content_hash"] = digest
        await self._store.audit(
            self._merchant_id, action=action, offer_id=offer_id, metadata=metadata
        )
