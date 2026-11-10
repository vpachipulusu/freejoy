"""Write validated network report rows into ``network_report_rows``, safely and idempotently.

``ReportImporter.import_batch`` handles one batch (at most one page of a report) inside one transaction: it
takes the run lock, reads the stored rows for the batch in **one query**, then for each row compares the
row's ``report_hash`` with the stored one:

==========================================  =================================================
state                                       result
==========================================  =================================================
never seen                                  insert, ``first_seen_at`` = ``last_seen_at`` = now   -> inserted
seen, same hash                             only ``last_seen_at`` moves                          -> unchanged
seen, different hash (a status moved, an    status, amounts, date, click reference and
amount was amended)                         ``last_seen_at`` are rewritten                       -> updated
==========================================  =================================================

Matching ties a row to the catalogue. It is only computed when a row is inserted, or changes **and has no
match yet or reports a different click reference**: a stored match is never erased or moved by a later status
change (a link deactivated or a second link added since must not rewrite which offer earned a commission).

* the **merchant** is what ``merchant_imports`` maps the reported advertiser id to (never a name);
* the **affiliate link and offer** are those of the one active ``awin`` link whose ``tracking_id`` equals the
  reported click reference, else of the one active ``awin`` link carrying that value as its ``clickref`` (slot
  1 only: slots 2 to 6 are neither reported nor matched) in its stored destination
  (``awin_tracking.passive_values``, so there is one parser);
* a link is a candidate only if its destination is a recognised Awin click link **for the advertiser the
  transaction was reported for** (``awin_tracking.link_advertiser_id``). A reference carried only by links of
  another advertiser is ``merchant_mismatch`` and matches nothing;
* nothing matches when the row has no click reference, no link carries it, or **more than one** link does
  (ambiguous: a wrong attribution is worse than none), and a reference carried by **more links than
  ``MAX_CANDIDATES``** is ``too_many_candidates``: the list may be cut short, so it is not trusted.

An unmatched row is kept with null offer and link. Matches and merchants are looked up once per advertiser
and reference per batch. Rejected rows are not written, but one whose transaction id is already stored is
counted (``rejected_known``) so a stored row that later fails validation does not go unnoticed.

Revenue is attributed from these rows and never inferred from clicks. Nothing here logs or returns a
field of a row. The caller opens the session, passes the store and committer, and a failure leaves the
batch uncommitted.
"""

import enum
import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Protocol

from app.repositories.network_reports import (
    MAX_CANDIDATES,
    LinkCandidate,
    ReportRowRecord,
)
from app.services.admin_catalogue_common import Clock, utc_now
from app.services.awin_offer_import import Committer
from app.services.awin_tracking import link_advertiser_id, passive_values
from app.services.awin_transactions import PROVIDER, ReportRow, SkipReason, report_hash

logger = logging.getLogger("freejoy.network_reports")
CLICKREF_SLOT_ONE = frozenset({"clickref"})


class MatchProblem(enum.StrEnum):
    """Why a click reference that something carries still matched nothing (counted, never logged with data)."""

    MERCHANT_MISMATCH = "merchant_mismatch"
    TOO_MANY_CANDIDATES = "too_many_candidates"


@dataclass(frozen=True)
class ReportTally:
    """Counts by outcome (and skip reason). Immutable: ``skip`` and ``merged`` return new tallies.

    ``unmatched`` counts rows (inserted, updated or unchanged) that ended with no offer;
    ``match_problems`` says why some of them did. ``rejected_known`` counts rejected rows whose
    transaction id is already stored.
    """

    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    unmatched: int = 0
    rejected_known: int = 0
    skipped: Mapping[str, int] = field(default_factory=dict)
    match_problems: Mapping[str, int] = field(default_factory=dict)

    @property
    def skipped_total(self) -> int:
        return sum(self.skipped.values())

    @property
    def written(self) -> int:
        return self.inserted + self.updated + self.unchanged

    def skip(self, reason: SkipReason) -> "ReportTally":
        return replace(self, skipped=_counted(self.skipped, reason.value))

    def merged(self, other: "ReportTally") -> "ReportTally":
        return ReportTally(
            self.inserted + other.inserted,
            self.updated + other.updated,
            self.unchanged + other.unchanged,
            self.unmatched + other.unmatched,
            self.rejected_known + other.rejected_known,
            _sum_counts(self.skipped, other.skipped),
            _sum_counts(self.match_problems, other.match_problems),
        )


def _counted(counts: Mapping[str, int], key: str, by: int = 1) -> dict[str, int]:
    return {**counts, key: counts.get(key, 0) + by}


def _sum_counts(first: Mapping[str, int], second: Mapping[str, int]) -> dict[str, int]:
    merged = dict(first)
    for key, count in second.items():
        merged[key] = merged.get(key, 0) + count
    return merged


class ReportStore(Protocol):
    async def lock_report_run(self) -> None: ...
    async def find_merchant_id(self, provider: str, external_id: str) -> str | None: ...
    async def links_by_tracking_id(
        self, provider: str, tracking_id: str
    ) -> list[LinkCandidate]: ...
    async def links_with_text(
        self, provider: str, fragment: str
    ) -> list[LinkCandidate]: ...
    async def find_rows(
        self, provider: str, external_ids: Sequence[str]
    ) -> dict[str, ReportRowRecord]: ...
    async def insert_row(self, values: dict[str, Any]) -> None: ...
    async def update_row(self, row_id: str, values: dict[str, Any]) -> None: ...
    async def touch_row(self, row_id: str, seen_at: datetime) -> None: ...


@dataclass(frozen=True)
class _Match:
    link_id: str | None = None
    offer_id: str | None = None
    problem: MatchProblem | None = None


class ReportImporter:
    """One instance per transaction: its caches live exactly as long as the unit of work."""

    def __init__(
        self, store: ReportStore, session: Committer, clock: Clock = utc_now
    ) -> None:
        self.store, self.session, self.clock = store, session, clock
        self._merchants: dict[int, str | None] = {}
        self._matches: dict[tuple[int, str], _Match] = {}

    async def import_batch(
        self, rows: Sequence[ReportRow], rejected_ids: Sequence[str] = ()
    ) -> ReportTally:
        """Import every row in one transaction and commit once (a failure commits nothing).

        ``rejected_ids`` are the transaction ids of rows that failed validation; those already stored are
        counted, nothing else is done with them.
        """
        await self.store.lock_report_run()
        known = await self.store.find_rows(
            PROVIDER, [*(row.external_id for row in rows), *rejected_ids]
        )
        tally = ReportTally(rejected_known=len({i for i in rejected_ids if i in known}))
        for row in rows:
            tally = tally.merged(
                await self._import_one(row, known.get(row.external_id))
            )
        await self.session.commit()
        return tally

    async def _import_one(
        self, row: ReportRow, known: ReportRowRecord | None
    ) -> ReportTally:
        now = self.clock()
        digest = report_hash(row)
        if known is not None and known.report_hash == digest:
            await self.store.touch_row(known.id, now)
            return ReportTally(unchanged=1, unmatched=int(known.offer_id is None))
        keep = (
            known is not None
            and known.offer_id is not None
            and known.click_ref == row.click_ref
        )
        match = (
            _Match(known.affiliate_link_id, known.offer_id)
            if keep and known is not None
            else await self._match(row)
        )
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
        else:
            await self.store.update_row(known.id, {**values, "last_seen_at": now})
        problems = {match.problem.value: 1} if match.problem else {}
        return ReportTally(
            inserted=int(known is None),
            updated=int(known is not None),
            unmatched=int(match.offer_id is None),
            match_problems=problems,
        )

    async def _values(
        self, row: ReportRow, digest: str, match: _Match
    ) -> dict[str, Any]:
        return {
            "offer_id": match.offer_id,
            "affiliate_link_id": match.link_id,
            "merchant_external_id": str(row.advertiser_id),
            "merchant_id": await self._merchant_id(row.advertiser_id),
            "transaction_at": row.transaction_at,
            "commission_status": row.status.value,
            "commission_status_raw": row.status_raw,
            "commission_amount_minor": row.commission_amount_minor,
            "sale_amount_minor": row.sale_amount_minor,
            "currency": row.currency,
            "click_ref": row.click_ref,
            "report_hash": digest,
        }

    async def _merchant_id(self, advertiser_id: int) -> str | None:
        if advertiser_id not in self._merchants:
            self._merchants[advertiser_id] = await self.store.find_merchant_id(
                PROVIDER, str(advertiser_id)
            )
        return self._merchants[advertiser_id]

    async def _match(self, row: ReportRow) -> _Match:
        if row.click_ref is None:
            return _Match()
        key = (row.advertiser_id, row.click_ref)
        if key not in self._matches:
            self._matches[key] = await self._find(*key)
        return self._matches[key]

    async def _find(self, advertiser_id: int, click_ref: str) -> _Match:
        by_id = await self.store.links_by_tracking_id(PROVIDER, click_ref)
        if len(by_id) > MAX_CANDIDATES:
            return _Match(problem=MatchProblem.TOO_MANY_CANDIDATES)
        eligible = _awin_links(by_id)
        if not eligible:  # a tracking id that is claimed by an Awin link settles it, one way or the other
            in_text = await self.store.links_with_text(PROVIDER, click_ref)
            if len(in_text) > MAX_CANDIDATES:
                return _Match(problem=MatchProblem.TOO_MANY_CANDIDATES)
            eligible = _awin_links(
                link
                for link in in_text
                if click_ref in passive_values(link.destination_url, CLICKREF_SLOT_ONE)
            )
        return _decide(eligible, advertiser_id)


def _awin_links(links: Iterable[LinkCandidate]) -> list[tuple[LinkCandidate, int]]:
    """The links whose destination is a recognised Awin click link, with the advertiser each carries."""
    found = ((link, link_advertiser_id(link.destination_url)) for link in links)
    return [(link, advertiser) for link, advertiser in found if advertiser is not None]


def _decide(
    eligible: Sequence[tuple[LinkCandidate, int]], advertiser_id: int
) -> _Match:
    """The match when exactly one link of the reported advertiser claims the reference."""
    ours = {
        (link.link_id, link.offer_id) for link, adv in eligible if adv == advertiser_id
    }
    if not ours:
        return _Match(problem=MatchProblem.MERCHANT_MISMATCH) if eligible else _Match()
    if len(ours) > 1:
        logger.warning(
            "network report click reference is ambiguous: links=%d", len(ours)
        )
        return _Match()
    link_id, offer_id = next(iter(ours))
    return _Match(link_id, offer_id)
