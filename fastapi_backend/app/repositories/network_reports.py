"""Queries behind the network report ingest (FREEJ-51). SQL only; no business rules live here.

Reads the catalogue just enough to match a reported transaction (an active affiliate link by tracking id
or by a fragment of its destination, the merchant an advertiser id stands for through ``merchant_imports``)
and writes ``network_report_rows``. The run lock is a Postgres advisory lock with its own key, so a report
run and an offers import never wait for each other.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.platform.affiliate_link import AffiliateLink
from app.models.platform.catalogue_import import MerchantImport
from app.models.platform.network_report import NetworkReportRow
from app.repositories.base import BaseRepository

# One constant for every report run: ``pg_advisory_xact_lock`` makes concurrent runs take turns.
REPORT_LOCK_KEY = 7_460_046_002
# Longest a page waits for the run lock before it fails (``lock_timeout``, SQLSTATE 55P03).
DEFAULT_LOCK_TIMEOUT_MS = 30_000
# More links than this holding one tracking id is already ambiguous, so there is no point reading further.
MAX_TRACKING_CANDIDATES = 5
# The destination search is only a pre-filter (the caller compares whole parameter values), so it reads
# widely; a reference that appears in more links than this cannot be matched reliably.
MAX_TEXT_CANDIDATES = 200


@dataclass(frozen=True)
class LinkCandidate:
    link_id: str
    offer_id: str
    destination_url: str


@dataclass(frozen=True)
class ReportRowRecord:
    id: str
    external_id: str
    report_hash: str
    offer_id: str | None
    affiliate_link_id: str | None


class NetworkReportRepository(BaseRepository):
    def __init__(
        self, session: AsyncSession, lock_timeout_ms: int = DEFAULT_LOCK_TIMEOUT_MS
    ) -> None:
        super().__init__(session)
        self._lock_timeout_ms = lock_timeout_ms

    async def lock_report_run(self) -> None:
        """Wait (at most ``lock_timeout_ms``) until no other report run holds the lock; released at commit."""
        await self._session.execute(
            text("SELECT set_config('lock_timeout', :ms, true)"),
            {"ms": str(self._lock_timeout_ms)},
        )
        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(:key)"), {"key": REPORT_LOCK_KEY}
        )

    # --- matching ---------------------------------------------------------------------------

    async def find_merchant_id(self, provider: str, external_id: str) -> str | None:
        return await self._session.scalar(
            select(MerchantImport.merchant_id).where(
                MerchantImport.provider == provider,
                MerchantImport.external_id == external_id,
            )
        )

    async def links_by_tracking_id(
        self, provider: str, tracking_id: str
    ) -> list[LinkCandidate]:
        return await self._candidates(
            provider,
            AffiliateLink.tracking_id == tracking_id,
            MAX_TRACKING_CANDIDATES,
        )

    async def links_with_text(
        self, provider: str, fragment: str
    ) -> list[LinkCandidate]:
        """Active links whose destination contains ``fragment`` (LIKE wildcards in it are escaped).

        Only a pre-filter: the caller parses each destination and compares whole parameter values.
        """
        return await self._candidates(
            provider,
            AffiliateLink.destination_url.contains(fragment, autoescape=True),
            MAX_TEXT_CANDIDATES,
        )

    async def _candidates(
        self, provider: str, condition: Any, limit: int
    ) -> list[LinkCandidate]:
        result = await self._session.execute(
            select(
                AffiliateLink.id, AffiliateLink.offer_id, AffiliateLink.destination_url
            )
            .where(
                AffiliateLink.provider == provider,
                AffiliateLink.active.is_(True),
                condition,
            )
            .order_by(AffiliateLink.id)
            .limit(limit)
        )
        return [LinkCandidate(*row) for row in result.all()]

    # --- rows -------------------------------------------------------------------------------

    async def find_row(self, provider: str, external_id: str) -> ReportRowRecord | None:
        row = await self._session.scalar(
            select(NetworkReportRow).where(
                NetworkReportRow.provider == provider,
                NetworkReportRow.external_id == external_id,
            )
        )
        if row is None:
            return None
        return ReportRowRecord(
            row.id,
            row.external_id,
            row.report_hash,
            row.offer_id,
            row.affiliate_link_id,
        )

    async def insert_row(self, values: dict[str, Any]) -> None:
        self._session.add(NetworkReportRow(**values))
        await self._session.flush()

    async def update_row(self, row_id: str, values: dict[str, Any]) -> None:
        await self._session.execute(
            update(NetworkReportRow)
            .where(NetworkReportRow.id == row_id)
            .values(**values)
        )

    async def touch_row(self, row_id: str, seen_at: datetime) -> None:
        """Move ``last_seen_at`` only: ``updated_at`` stays, so a rerun that changes nothing says so."""
        await self.update_row(
            row_id, {"last_seen_at": seen_at, "updated_at": NetworkReportRow.updated_at}
        )
