"""Write validated network report rows into ``network_report_rows``, safely and idempotently.

``ReportImporter.import_batch`` handles one batch (one page of a report) inside one transaction: it takes
the run lock, then for each row compares the row's ``report_hash`` with the stored one:

==========================================  =================================================
state                                       result
==========================================  =================================================
never seen                                  insert, ``first_seen_at`` = ``last_seen_at`` = now   -> inserted
seen, same hash                             only ``last_seen_at`` moves                          -> unchanged
seen, different hash (a status moved, an    status, amounts, date, click reference, matching
amount was amended)                         and ``last_seen_at`` are rewritten                   -> updated
==========================================  =================================================

Matching ties a row to the catalogue and is only ever computed when a row is inserted or changes:

* the **merchant** is what ``merchant_imports`` maps the reported advertiser id to (never a name);
* the **affiliate link and offer** are those of the one active ``awin`` link whose ``tracking_id`` equals
  the reported click reference, else of the one active ``awin`` link carrying that value as ``clickref`` or
  ``clickref2`` to ``clickref6`` in its stored destination (``awin_tracking.passive_values`` reads it, so
  there is one parser). Whole values only: ``ref-1`` never matches ``ref-12``. Nothing matches when no link
  does, when the row has no click reference, or when **more than one** link does (ambiguous: a wrong
  attribution is worse than none). An unmatched row is kept with null offer and link.

Revenue is attributed from these rows and never inferred from clicks. Nothing here logs or returns a
field of a row. The caller opens the session, passes the store and committer, and a failure leaves the
batch uncommitted.
"""

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Protocol

from app.repositories.network_reports import LinkCandidate, ReportRowRecord
from app.services.admin_catalogue_common import Clock, utc_now
from app.services.awin_offer_import import Committer
from app.services.awin_tracking import passive_values
from app.services.awin_transactions import PROVIDER, ReportRow, SkipReason, report_hash

logger = logging.getLogger("freejoy.network_reports")


@dataclass(frozen=True)
class ReportTally:
    """Counts by outcome (and skip reason). Immutable: ``skip`` and ``merged`` return new tallies.

    ``unmatched`` counts rows (inserted, updated or unchanged) that ended with no offer.
    """

    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    unmatched: int = 0
    skipped: Mapping[str, int] = field(default_factory=dict)

    @property
    def skipped_total(self) -> int:
        return sum(self.skipped.values())

    @property
    def written(self) -> int:
        return self.inserted + self.updated + self.unchanged

    def skip(self, reason: SkipReason) -> "ReportTally":
        counts = {**self.skipped, reason.value: self.skipped.get(reason.value, 0) + 1}
        return replace(self, skipped=counts)

    def merged(self, other: "ReportTally") -> "ReportTally":
        skipped = dict(self.skipped)
        for reason, count in other.skipped.items():
            skipped[reason] = skipped.get(reason, 0) + count
        return ReportTally(
            self.inserted + other.inserted,
            self.updated + other.updated,
            self.unchanged + other.unchanged,
            self.unmatched + other.unmatched,
            skipped,
        )


class ReportStore(Protocol):
    async def lock_report_run(self) -> None: ...
    async def find_merchant_id(self, provider: str, external_id: str) -> str | None: ...
    async def links_by_tracking_id(
        self, provider: str, tracking_id: str
    ) -> list[LinkCandidate]: ...
    async def links_with_text(
        self, provider: str, fragment: str
    ) -> list[LinkCandidate]: ...
    async def find_row(
        self, provider: str, external_id: str
    ) -> ReportRowRecord | None: ...
    async def insert_row(self, values: dict[str, Any]) -> None: ...
    async def update_row(self, row_id: str, values: dict[str, Any]) -> None: ...
    async def touch_row(self, row_id: str, seen_at: datetime) -> None: ...


@dataclass(frozen=True)
class _Match:
    link_id: str | None = None
    offer_id: str | None = None


class ReportImporter:
    def __init__(
        self, store: ReportStore, session: Committer, clock: Clock = utc_now
    ) -> None:
        self.store, self.session, self.clock = store, session, clock

    async def import_batch(self, rows: Sequence[ReportRow]) -> ReportTally:
        """Import every row in one transaction and commit once (a failure commits nothing)."""
        await self.store.lock_report_run()
        tally = ReportTally()
        for row in rows:
            tally = tally.merged(await self._import_one(row))
        await self.session.commit()
        return tally

    async def _import_one(self, row: ReportRow) -> ReportTally:
        now = self.clock()
        digest = report_hash(row)
        known = await self.store.find_row(PROVIDER, row.external_id)
        if known is not None and known.report_hash == digest:
            await self.store.touch_row(known.id, now)
            return ReportTally(unchanged=1, unmatched=int(known.offer_id is None))
        match = await self._match(row.click_ref)
        values = await self._values(row, digest, match)
        if known is None:
            await self.store.insert_row(
                {
                    "provider": PROVIDER,
                    "external_id": row.external_id,
                    "first_seen_at": now,
                    "last_seen_at": now,
                    **values,
                }
            )
            return ReportTally(inserted=1, unmatched=int(match.offer_id is None))
        await self.store.update_row(known.id, {**values, "last_seen_at": now})
        return ReportTally(updated=1, unmatched=int(match.offer_id is None))

    async def _values(
        self, row: ReportRow, digest: str, match: _Match
    ) -> dict[str, Any]:
        merchant_id = await self.store.find_merchant_id(
            PROVIDER, str(row.advertiser_id)
        )
        return {
            "offer_id": match.offer_id,
            "affiliate_link_id": match.link_id,
            "merchant_external_id": str(row.advertiser_id),
            "merchant_id": merchant_id,
            "transaction_at": row.transaction_at,
            "commission_status": row.status.value,
            "commission_status_raw": row.status_raw,
            "commission_amount_minor": row.commission_amount_minor,
            "sale_amount_minor": row.sale_amount_minor,
            "currency": row.currency,
            "click_ref": row.click_ref,
            "report_hash": digest,
        }

    async def _match(self, click_ref: str | None) -> _Match:
        if click_ref is None:
            return _Match()
        by_id = await self.store.links_by_tracking_id(PROVIDER, click_ref)
        if by_id:  # a tracking id that is claimed settles it, one way or the other
            return _only(by_id)
        in_url = [
            link
            for link in await self.store.links_with_text(PROVIDER, click_ref)
            if click_ref in passive_values(link.destination_url)
        ]
        return _only(in_url)


def _only(candidates: Sequence[LinkCandidate]) -> _Match:
    """The match when exactly one link claims the reference, else none."""
    claimed = {(link.link_id, link.offer_id) for link in candidates}
    if len(claimed) != 1:
        if claimed:
            logger.warning(
                "network report click reference is ambiguous: links=%d", len(claimed)
            )
        return _Match()
    (link_id, offer_id) = next(iter(claimed))
    return _Match(link_id, offer_id)
