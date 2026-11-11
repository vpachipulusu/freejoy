"""Data access of the partner submissions, in SQLAlchemy Core, on the restricted partner role (ADR-013, FREEJ-58).

Every method of ``PartnerSubmissionStore`` takes ``merchant_id`` as its first argument and puts it in the ``WHERE``
clause (or, for a table that has no merchant column, joins to the offer and filters on the offer's merchant): there
is no way to ask for a row by its own id alone, so a ref or an offer of another merchant is simply not found, in
one query, exactly like one that does not exist. ``tests/api/partner/test_partner_submission_store.py`` asserts the
argument is there on every public method.

The caller owns the session and the transaction. Nothing here commits. The role's grants (``partner_role.sql``)
are exactly the columns used below in the way they are used; ``RETURNING`` lists only columns the role may read.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import (
    ColumnElement,
    Select,
    Text,
    case,
    func,
    insert,
    literal,
    select,
    update,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import new_id
from app.partner.keys import merchants
from app.partner.tables import (
    affiliate_links,
    audit_logs,
    categories,
    offer_imports,
    offers,
    partner_submissions,
)

PROVIDER = "partner"
DIRECT = "direct"
OFFER_FIELDS = (
    "id",
    "title",
    "slug",
    "summary",
    "description",
    "category_id",
    "merchant_id",
    "offer_type",
    "status",
    "featured",
    "image_tint",
    "image_label",
    "image_alt",
    "source_url",
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
    "last_verified_at",
    "freshness",
    "publisher_notes",
)
LINK_FIELDS = ("id", "offer_id", "provider", "destination_url", "tracking_id", "active")
IMPORT_FIELDS = (
    "id",
    "offer_id",
    "source_hash",
    "applied_source_hash",
    "applied_content_hash",
    "applied_at",
    "source_changed_at",
)
SUBMISSION_FIELDS = (
    "id",
    "merchant_id",
    "ref",
    "offer_id",
    "content_hash",
    "pending_hash",
    "key_id",
)
# Keys of the two-key advisory lock that serialises the creation of one merchant's submissions: a namespace of our
# own, then the merchant, so it cannot meet a lock taken for any other purpose.
CREATION_LOCK_NAMESPACE = 58_001


@dataclass(frozen=True)
class MerchantFacts:
    name: str
    website: str | None


@dataclass(frozen=True)
class OfferRow:
    """An offer as the role reads it. ``past_end`` is judged by the database clock."""

    id: str
    title: str
    slug: str
    summary: str
    description: list[Any]
    category_id: str
    merchant_id: str
    offer_type: str
    status: str
    featured: bool
    image_tint: str
    image_label: str
    image_alt: str
    source_url: str | None
    postage_kind: str
    postage_amount_minor: int | None
    postage_currency: str | None
    requires_card: bool
    subscription_terms: str | None
    eligibility: str
    availability: str
    steps: list[Any]
    start_at: datetime | None
    end_at: datetime | None
    last_verified_at: datetime | None
    freshness: str | None
    publisher_notes: str | None
    past_end: bool = False


@dataclass(frozen=True)
class LinkRow:
    id: str
    offer_id: str
    provider: str
    destination_url: str
    tracking_id: str | None
    active: bool


@dataclass(frozen=True)
class ImportRow:
    id: str
    offer_id: str
    source_hash: str
    applied_source_hash: str
    applied_content_hash: str
    applied_at: datetime
    source_changed_at: datetime | None


@dataclass(frozen=True)
class SubmissionRow:
    id: str
    merchant_id: str
    ref: str
    offer_id: str
    content_hash: str
    pending_hash: str | None
    key_id: str


@dataclass(frozen=True)
class ViewRow:
    """What the status of a submission is derived from."""

    ref: str
    title: str
    slug: str
    offer_status: str
    end_at: datetime | None
    past_end: bool
    ever_published: bool
    update_pending: bool
    submitted_at: datetime
    category_enabled: bool = True


def _past_end() -> ColumnElement[bool]:
    return func.coalesce(offers.c.end_at <= func.now(), False)


def _offer_select() -> Select[Any]:
    return select(
        *(offers.c[name] for name in OFFER_FIELDS), _past_end().label("past_end")
    )


def _offer_row(row: Any) -> OfferRow:
    return OfferRow(**row._mapping)


class PartnerSubmissionStore:
    """Reads and writes of one partner request, all scoped to the calling merchant."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def merchant_facts(self, merchant_id: str) -> MerchantFacts | None:
        row = (
            await self._session.execute(
                select(merchants.c.name, merchants.c.website).where(
                    merchants.c.id == merchant_id, merchants.c.active.is_(True)
                )
            )
        ).first()
        return None if row is None else MerchantFacts(row.name, row.website)

    async def find_submission(
        self, merchant_id: str, ref: str, *, lock: bool = False
    ) -> SubmissionRow | None:
        statement = select(
            *(partner_submissions.c[n] for n in SUBMISSION_FIELDS)
        ).where(
            partner_submissions.c.merchant_id == merchant_id,
            partner_submissions.c.ref == ref,
        )
        row = (
            await self._session.execute(
                statement.with_for_update(key_share=True) if lock else statement
            )
        ).first()
        return None if row is None else SubmissionRow(**row._mapping)

    async def lock_offer(self, merchant_id: str, offer_id: str) -> OfferRow | None:
        """The offer, row-locked FOR NO KEY UPDATE: the first lock of every write, as in the admin service.

        Weaker than the admin's ``FOR UPDATE`` on purpose: it excludes every writer but does not block the
        foreign-key check of a visitor's click on the offer.
        """
        row = (
            await self._session.execute(
                _offer_select()
                .where(offers.c.id == offer_id, offers.c.merchant_id == merchant_id)
                .with_for_update(key_share=True)
            )
        ).first()
        return None if row is None else _offer_row(row)

    async def links(self, merchant_id: str, offer_id: str) -> list[LinkRow]:
        rows = await self._session.execute(
            select(*(affiliate_links.c[n] for n in LINK_FIELDS))
            .join(offers, offers.c.id == affiliate_links.c.offer_id)
            .where(offers.c.id == offer_id, offers.c.merchant_id == merchant_id)
            .order_by(affiliate_links.c.id)
        )
        return [LinkRow(**row._mapping) for row in rows]

    async def import_row(
        self, merchant_id: str, offer_id: str, *, lock: bool = False
    ) -> ImportRow | None:
        statement = (
            select(*(offer_imports.c[n] for n in IMPORT_FIELDS))
            .join(offers, offers.c.id == offer_imports.c.offer_id)
            .where(
                offers.c.id == offer_id,
                offers.c.merchant_id == merchant_id,
                offer_imports.c.provider == PROVIDER,
            )
        )
        row = (
            await self._session.execute(
                statement.with_for_update(of=offer_imports, key_share=True)
                if lock
                else statement
            )
        ).first()
        return None if row is None else ImportRow(**row._mapping)

    # --- caps --------------------------------------------------------------------------------

    async def lock_creation(self, merchant_id: str) -> None:
        """Serialise the creation of submissions of one merchant until the transaction ends (the caps are counted next)."""
        await self._session.execute(
            select(
                func.pg_advisory_xact_lock(
                    CREATION_LOCK_NAMESPACE, func.hashtext(literal(merchant_id))
                )
            )
        )

    async def count_open(self, merchant_id: str) -> int:
        total = await self._session.scalar(
            select(func.count())
            .select_from(partner_submissions)
            .join(offers, offers.c.id == partner_submissions.c.offer_id)
            .where(
                partner_submissions.c.merchant_id == merchant_id,
                offers.c.merchant_id == merchant_id,
                offers.c.status == "DRAFT",
            )
        )
        return total or 0

    async def count_created_today(self, merchant_id: str) -> int:
        """New submissions since 00:00 UTC today, by the database clock."""
        midnight = func.timezone(
            "UTC", func.date_trunc("day", func.timezone("UTC", func.now()))
        )
        total = await self._session.scalar(
            select(func.count())
            .select_from(partner_submissions)
            .where(
                partner_submissions.c.merchant_id == merchant_id,
                partner_submissions.c.created_at >= midnight,
            )
        )
        return total or 0

    # --- writes ------------------------------------------------------------------------------

    async def insert_offer(self, merchant_id: str, values: dict[str, Any]) -> OfferRow:
        """A new offer. ``status`` is not named: the column default makes it a DRAFT, and the role cannot name it."""
        statement = (
            insert(offers)
            .values(id=new_id(), merchant_id=merchant_id, **values)
            .returning(
                *(offers.c[name] for name in OFFER_FIELDS),
                _past_end().label("past_end"),
            )
        )
        return _offer_row((await self._session.execute(statement)).one())

    async def update_offer(
        self, merchant_id: str, offer_id: str, values: dict[str, Any]
    ) -> OfferRow:
        statement = (
            update(offers)
            .where(offers.c.id == offer_id, offers.c.merchant_id == merchant_id)
            .values(**values, updated_at=func.now())
            .returning(
                *(offers.c[name] for name in OFFER_FIELDS),
                _past_end().label("past_end"),
            )
        )
        return _offer_row((await self._session.execute(statement)).one())

    async def end_offer(self, merchant_id: str, offer_id: str) -> bool:
        """Set ``end_at`` to the database clock unless the offer has already ended. True when it changed.

        A start date still in the future goes too: an offer that never started cannot have a window that ends
        before it begins (the editors' edit form would refuse it). ``status`` is never touched.
        """
        result = await self._session.execute(
            update(offers)
            .where(
                offers.c.id == offer_id,
                offers.c.merchant_id == merchant_id,
                offers.c.end_at.is_(None) | (offers.c.end_at > func.now()),
            )
            .values(
                end_at=func.now(),
                start_at=case(
                    (offers.c.start_at > func.now(), None), else_=offers.c.start_at
                ),
                updated_at=func.now(),
            )
            .returning(offers.c.id)
        )
        return result.first() is not None

    def _owned_offer(
        self, merchant_id: str, offer_id: str, *columns: Any
    ) -> Select[Any]:
        """``columns`` of the offer, but only when it is this merchant's: the source of an ``INSERT ... SELECT``."""
        return select(*columns).where(
            offers.c.id == offer_id, offers.c.merchant_id == merchant_id
        )

    async def insert_link(
        self, merchant_id: str, offer_id: str, destination_url: str
    ) -> LinkRow:
        """The offer's one link, inserted only if the offer is this merchant's (``NoResultFound`` otherwise)."""
        source = self._owned_offer(
            merchant_id,
            offer_id,
            literal(new_id(), Text),
            offers.c.id,
            literal(destination_url, Text),
        )
        row = (
            await self._session.execute(
                insert(affiliate_links)
                .from_select(["id", "offer_id", "destination_url"], source)
                .returning(*(affiliate_links.c[n] for n in LINK_FIELDS))
            )
        ).one()
        return LinkRow(**row._mapping)

    async def update_link(
        self, merchant_id: str, offer_id: str, link_id: str, destination_url: str
    ) -> LinkRow:
        owned = (
            select(offers.c.id)
            .where(offers.c.id == offer_id, offers.c.merchant_id == merchant_id)
            .scalar_subquery()
        )
        row = (
            await self._session.execute(
                update(affiliate_links)
                .where(
                    affiliate_links.c.id == link_id, affiliate_links.c.offer_id == owned
                )
                .values(destination_url=destination_url, updated_at=func.now())
                .returning(*(affiliate_links.c[n] for n in LINK_FIELDS))
            )
        ).one()
        return LinkRow(**row._mapping)

    async def insert_import(
        self,
        merchant_id: str,
        *,
        offer_id: str,
        external_id: str,
        digest: str,
        applied_content_hash: str,
    ) -> None:
        """The provenance row that puts the offer in the editors' review queue (a DRAFT with an import row).

        Inserted only if the offer is this merchant's (``NoResultFound`` otherwise).
        """
        source = self._owned_offer(
            merchant_id,
            offer_id,
            literal(new_id(), Text),
            literal(PROVIDER, Text),
            literal(external_id, Text),
            offers.c.id,
            literal(digest, Text),
            literal(digest, Text),
            literal(applied_content_hash, Text),
            func.now(),
        )
        result = await self._session.execute(
            insert(offer_imports)
            .from_select(
                [
                    "id",
                    "provider",
                    "external_id",
                    "offer_id",
                    "source_hash",
                    "applied_source_hash",
                    "applied_content_hash",
                    "applied_at",
                ],
                source,
            )
            .returning(offer_imports.c.id)
        )
        result.one()

    async def update_import(
        self, merchant_id: str, offer_id: str, values: dict[str, Any]
    ) -> None:
        owned = (
            select(offers.c.id)
            .where(offers.c.id == offer_id, offers.c.merchant_id == merchant_id)
            .scalar_subquery()
        )
        await self._session.execute(
            update(offer_imports)
            .where(
                offer_imports.c.offer_id == owned, offer_imports.c.provider == PROVIDER
            )
            .values(**values, updated_at=func.now())
        )

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
        """The submission of a draft, inserted only if the offer is this merchant's (``NoResultFound`` otherwise)."""
        source = self._owned_offer(
            merchant_id,
            offer_id,
            literal(submission_id, Text),
            literal(merchant_id, Text),
            literal(ref, Text),
            offers.c.id,
            literal(content_hash, Text),
            literal(key_id, Text),
        )
        result = await self._session.execute(
            insert(partner_submissions)
            .from_select(
                ["id", "merchant_id", "ref", "offer_id", "content_hash", "key_id"],
                source,
            )
            .returning(partner_submissions.c.id)
        )
        result.one()

    async def update_submission(
        self, merchant_id: str, submission_id: str, values: dict[str, Any]
    ) -> None:
        await self._session.execute(
            update(partner_submissions)
            .where(
                partner_submissions.c.id == submission_id,
                partner_submissions.c.merchant_id == merchant_id,
            )
            .values(**values, submitted_at=func.now(), updated_at=func.now())
        )

    async def audit(
        self,
        merchant_id: str,
        *,
        action: str,
        offer_id: str,
        metadata: dict[str, Any],
    ) -> None:
        """Append one audit row (the role may insert nothing else there). Ids, enums and hashes only: no copy."""
        await self._session.execute(
            insert(audit_logs).values(
                id=new_id(),
                action=action,
                resource="offers",
                resource_id=offer_id,
                metadata={"merchant_id": merchant_id, **metadata},
            )
        )

    # --- reads for the retailer --------------------------------------------------------------

    def _view_select(self, merchant_id: str) -> Select[Any]:
        return (
            select(
                partner_submissions.c.ref,
                offers.c.title,
                offers.c.slug,
                offers.c.status.label("offer_status"),
                offers.c.end_at,
                _past_end().label("past_end"),
                partner_submissions.c.first_published_at.is_not(None).label(
                    "ever_published"
                ),
                partner_submissions.c.pending_hash.is_not(None).label("update_pending"),
                partner_submissions.c.submitted_at,
                func.coalesce(categories.c.enabled, False).label("category_enabled"),
            )
            .join(offers, offers.c.id == partner_submissions.c.offer_id)
            .outerjoin(categories, categories.c.id == offers.c.category_id)
            .where(
                partner_submissions.c.merchant_id == merchant_id,
                offers.c.merchant_id == merchant_id,
            )
        )

    async def view(self, merchant_id: str, ref: str) -> ViewRow | None:
        row = (
            await self._session.execute(
                self._view_select(merchant_id).where(partner_submissions.c.ref == ref)
            )
        ).first()
        return None if row is None else ViewRow(**row._mapping)

    async def list_views(
        self, merchant_id: str, limit: int, offset: int
    ) -> tuple[list[ViewRow], int]:
        """A page, newest first, and the total counted over the same join as the rows."""
        rows = await self._session.execute(
            self._view_select(merchant_id)
            .order_by(
                partner_submissions.c.created_at.desc(), partner_submissions.c.id.desc()
            )
            .limit(limit)
            .offset(offset)
        )
        total = await self._session.scalar(
            select(func.count())
            .select_from(partner_submissions)
            .join(offers, offers.c.id == partner_submissions.c.offer_id)
            .where(
                partner_submissions.c.merchant_id == merchant_id,
                offers.c.merchant_id == merchant_id,
            )
        )
        return [ViewRow(**row._mapping) for row in rows], total or 0


class CategoryReader:
    """Reference data every merchant shares: the one partner reader with no tenant."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def enabled_category_id(self, slug: str) -> str | None:
        return await self._session.scalar(
            select(categories.c.id).where(
                categories.c.slug == slug, categories.c.enabled.is_(True)
            )
        )
