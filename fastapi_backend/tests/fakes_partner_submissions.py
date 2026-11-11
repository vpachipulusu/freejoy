"""In-memory doubles of the partner submission store, so the service's rules run without a database.

``FakeSubmissionStore`` has the same methods and the same tenancy as ``PartnerSubmissionStore``: every read and write
carries a merchant id and finds only that merchant's rows. The Postgres suites (``*_e2e.py``) prove the real store.
"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import Any, Self

from sqlalchemy.exc import IntegrityError

from app.partner.submission_store import (
    ImportRow,
    LinkRow,
    MerchantFacts,
    OfferRow,
    SubmissionRow,
    ViewRow,
)

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


class FakeDriverError(Exception):
    """What asyncpg raises under SQLAlchemy's wrapper: it names the constraint that was violated."""

    def __init__(self, constraint_name: str) -> None:
        super().__init__("duplicate key")
        self.constraint_name = constraint_name


def unique_violation(constraint: str) -> IntegrityError:
    orig = Exception("duplicate key")
    orig.__cause__ = FakeDriverError(constraint)
    return IntegrityError("INSERT", {}, orig)


class FakeSession:
    """Counts commits and rollbacks; ``begin_nested`` is a savepoint that rolls the fake store back on an error."""

    def __init__(self, store: "FakeSubmissionStore | None" = None) -> None:
        self.commits = 0
        self.rollbacks = 0
        self.store = store

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1

    def begin_nested(self) -> "FakeSavepoint":
        return FakeSavepoint(self.store)


class FakeSavepoint:
    def __init__(self, store: "FakeSubmissionStore | None") -> None:
        self._store = store
        self._snapshot: Any = None

    async def __aenter__(self) -> Self:
        if self._store is not None:
            self._snapshot = self._store.snapshot()
        return self

    async def __aexit__(
        self,
        _kind: type[BaseException] | None,
        error: BaseException | None,
        _trace: TracebackType | None,
    ) -> bool:
        if error is not None and self._store is not None:
            self._store.restore(self._snapshot)
        return False


class FakeCategories:
    def __init__(self, enabled: dict[str, str] | None = None) -> None:
        self.enabled = enabled if enabled is not None else {"home": "cat-home"}

    async def enabled_category_id(self, slug: str) -> str | None:
        return self.enabled.get(slug)


class FakeSubmissionStore:
    """Offers, links, provenance and submissions of several merchants, in dictionaries."""

    def __init__(self) -> None:
        self.merchants: dict[str, MerchantFacts] = {}
        self.offers: dict[str, OfferRow] = {}
        self.links_by_offer: dict[str, list[LinkRow]] = {}
        self.imports: dict[str, ImportRow] = {}
        self.submissions: dict[str, SubmissionRow] = {}
        self.audits: list[dict[str, Any]] = []
        self.locks: list[str] = []
        self.calls: list[str] = []
        self.fail_inserts: list[str] = []
        self.created_today_override: int | None = None
        # The next N unlocked reads of a submission find nothing, as if another request had not committed yet.
        self.hide_find = 0
        self._sequence = 0

    # --- test helpers -------------------------------------------------------------------------

    def snapshot(self) -> tuple[Any, ...]:
        return (
            dict(self.offers),
            {k: list(v) for k, v in self.links_by_offer.items()},
            dict(self.imports),
            dict(self.submissions),
        )

    def restore(self, state: tuple[Any, ...]) -> None:
        self.offers, self.links_by_offer, self.imports, self.submissions = state

    def add_merchant(
        self,
        merchant_id: str,
        name: str = "Shop",
        website: str | None = "https://shop.example.com",
    ) -> None:
        self.merchants[merchant_id] = MerchantFacts(name, website)

    def _next(self, prefix: str) -> str:
        self._sequence += 1
        return f"{prefix}{self._sequence}"

    def by_ref(self, merchant_id: str, ref: str) -> SubmissionRow | None:
        return next(
            (
                s
                for s in self.submissions.values()
                if (s.merchant_id, s.ref) == (merchant_id, ref)
            ),
            None,
        )

    def edit_offer(self, offer_id: str, **changes: Any) -> None:
        self.offers[offer_id] = replace(self.offers[offer_id], **changes)

    # --- the store interface ------------------------------------------------------------------

    async def merchant_facts(self, merchant_id: str) -> MerchantFacts | None:
        return self.merchants.get(merchant_id)

    async def find_submission(
        self, merchant_id: str, ref: str, *, lock: bool = False
    ) -> SubmissionRow | None:
        self.calls.append(f"find:{merchant_id}:{ref}")
        if self.hide_find > 0 and not lock:
            self.hide_find -= 1
            return None
        return self.by_ref(merchant_id, ref)

    async def lock_offer(self, merchant_id: str, offer_id: str) -> OfferRow | None:
        self.locks.append(f"offer:{offer_id}")
        offer = self.offers.get(offer_id)
        return offer if offer and offer.merchant_id == merchant_id else None

    async def links(self, merchant_id: str, offer_id: str) -> list[LinkRow]:
        offer = self.offers.get(offer_id)
        if offer is None or offer.merchant_id != merchant_id:
            return []
        return list(self.links_by_offer.get(offer_id, []))

    async def import_row(
        self, merchant_id: str, offer_id: str, *, lock: bool = False
    ) -> ImportRow | None:
        if lock:
            self.locks.append(f"import:{offer_id}")
        offer = self.offers.get(offer_id)
        if offer is None or offer.merchant_id != merchant_id:
            return None
        return self.imports.get(offer_id)

    async def lock_creation(self, merchant_id: str) -> None:
        self.locks.append(f"creation:{merchant_id}")

    async def count_open(self, merchant_id: str) -> int:
        return sum(
            1
            for s in self.submissions.values()
            if s.merchant_id == merchant_id
            and self.offers[s.offer_id].status == "DRAFT"
        )

    async def count_created_today(self, merchant_id: str) -> int:
        if self.created_today_override is not None:
            return self.created_today_override
        return sum(1 for s in self.submissions.values() if s.merchant_id == merchant_id)

    async def insert_offer(self, merchant_id: str, values: dict[str, Any]) -> OfferRow:
        if "slug" in self.fail_inserts:
            self.fail_inserts.remove("slug")
            raise unique_violation("uq_offers_slug")
        offer = OfferRow(
            id=self._next("offer"),
            merchant_id=merchant_id,
            status="DRAFT",
            featured=False,
            source_url=None,
            last_verified_at=None,
            freshness=None,
            publisher_notes=None,
            past_end=False,
            **values,
        )
        self.offers[offer.id] = offer
        return offer

    async def update_offer(
        self, merchant_id: str, offer_id: str, values: dict[str, Any]
    ) -> OfferRow:
        offer = self.offers[offer_id]
        assert offer.merchant_id == merchant_id
        self.offers[offer_id] = replace(offer, **values)
        return self.offers[offer_id]

    async def end_offer(self, merchant_id: str, offer_id: str) -> bool:
        offer = self.offers.get(offer_id)
        if offer is None or offer.merchant_id != merchant_id:
            return False
        if offer.end_at is not None and offer.end_at <= NOW:
            return False
        start = None if offer.start_at and offer.start_at > NOW else offer.start_at
        self.offers[offer_id] = replace(
            offer, end_at=NOW, start_at=start, past_end=True
        )
        return True

    async def insert_link(
        self, merchant_id: str, offer_id: str, destination_url: str
    ) -> LinkRow:
        link = LinkRow(
            self._next("link"), offer_id, "direct", destination_url, None, True
        )
        self.links_by_offer.setdefault(offer_id, []).append(link)
        return link

    async def update_link(
        self, merchant_id: str, offer_id: str, link_id: str, destination_url: str
    ) -> LinkRow:
        links = self.links_by_offer[offer_id]
        index = next(i for i, link in enumerate(links) if link.id == link_id)
        links[index] = replace(links[index], destination_url=destination_url)
        return links[index]

    async def insert_import(
        self,
        merchant_id: str,
        *,
        offer_id: str,
        external_id: str,
        digest: str,
        applied_content_hash: str,
    ) -> None:
        self.imports[offer_id] = ImportRow(
            self._next("import"),
            offer_id,
            digest,
            digest,
            applied_content_hash,
            NOW,
            None,
        )

    async def update_import(
        self, merchant_id: str, offer_id: str, values: dict[str, Any]
    ) -> None:
        current = self.imports[offer_id]
        resolved = {
            k: (NOW if k.endswith("_at") and v is not None else v)
            for k, v in values.items()
        }
        self.imports[offer_id] = replace(current, **resolved)

    async def insert_submission(
        self,
        merchant_id: str,
        *,
        submission_id: str,
        ref: str,
        offer_id: str,
        content_hash: str,
        key_id: str,
    ) -> None:
        if "ref" in self.fail_inserts or self.by_ref(merchant_id, ref) is not None:
            if "ref" in self.fail_inserts:
                self.fail_inserts.remove("ref")
            raise unique_violation("uq_partner_submissions_merchant_id_ref")
        self.submissions[submission_id] = SubmissionRow(
            submission_id,
            merchant_id,
            ref,
            offer_id,
            content_hash,
            None,
            None,
            None,
            NOW,
            key_id,
            None,
            NOW,
        )

    async def update_submission(
        self, merchant_id: str, submission_id: str, values: dict[str, Any]
    ) -> None:
        current = self.submissions[submission_id]
        assert current.merchant_id == merchant_id
        resolved = {
            k: (NOW if k.endswith("_at") and v is not None else v)
            for k, v in values.items()
        }
        self.submissions[submission_id] = replace(current, **resolved)

    async def audit(
        self, merchant_id: str, *, action: str, offer_id: str, metadata: dict[str, Any]
    ) -> None:
        self.audits.append(
            {
                "action": action,
                "offer_id": offer_id,
                "merchant_id": merchant_id,
                **metadata,
            }
        )

    async def view(self, merchant_id: str, ref: str) -> ViewRow | None:
        self.calls.append(f"view:{merchant_id}:{ref}")
        submission = self.by_ref(merchant_id, ref)
        if submission is None:
            return None
        return self._view(submission)

    def _view(self, submission: SubmissionRow) -> ViewRow:
        offer = self.offers[submission.offer_id]
        return ViewRow(
            ref=submission.ref,
            title=offer.title,
            slug=offer.slug,
            offer_status=offer.status,
            end_at=offer.end_at,
            past_end=offer.past_end,
            ever_published=submission.first_published_at is not None,
            update_pending=submission.pending_hash is not None,
            submitted_at=submission.submitted_at,
        )

    async def list_views(
        self, merchant_id: str, limit: int, offset: int
    ) -> tuple[list[ViewRow], int]:
        mine = [s for s in self.submissions.values() if s.merchant_id == merchant_id]
        mine.sort(key=lambda s: s.id, reverse=True)
        return [self._view(s) for s in mine[offset : offset + limit]], len(mine)


def a_day_later() -> datetime:
    return NOW + timedelta(days=1)
