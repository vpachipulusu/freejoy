"""Fakes for the offer health check: no socket, no database, no Sentry."""

import asyncio
import socket
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from app.core.url_probe import ProbeReason, ProbeResult, ProbeStatus
from app.models.platform.offer import OfferStatus
from app.repositories.offer_health import (
    DirectLinkTarget,
    DueOffer,
    SourceUrlTarget,
)


class FakeResolver:
    """Answers from a table; a host that is missing fails like a real DNS lookup."""

    def __init__(self, table: Mapping[str, Sequence[str]] | None = None) -> None:
        self.table = dict(table or {})
        self.lookups: list[str] = []

    async def resolve(self, host: str) -> Sequence[str]:
        self.lookups.append(host)
        if host not in self.table:
            raise socket.gaierror(socket.EAI_NONAME, "no such host")
        return self.table[host]


class FakeUrlProbe:
    """Records every URL it is asked about and answers from a table (default: reachable)."""

    def __init__(
        self,
        results: Mapping[str, ProbeResult] | None = None,
        *,
        raises: Exception | None = None,
    ) -> None:
        self.results = dict(results or {})
        self.raises = raises
        self.urls: list[str] = []
        self.in_flight = 0
        self.peak_in_flight = 0

    async def check(self, url: str) -> ProbeResult:
        self.urls.append(url)
        if self.raises is not None:
            raise self.raises
        self.in_flight += 1
        self.peak_in_flight = max(self.peak_in_flight, self.in_flight)
        try:
            await _yield_to_other_tasks()
            return self.results.get(
                url, ProbeResult(ProbeStatus.REACHABLE, ProbeReason.OK)
            )
        finally:
            self.in_flight -= 1


async def _yield_to_other_tasks() -> None:
    await asyncio.sleep(0)
    await asyncio.sleep(0)


class FakeAuditor:
    """Stands in for ``AuditService.record_system_action`` and keeps what it was given."""

    def __init__(self, *, fail_on: int | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._fail_on = fail_on

    async def record_system_action(
        self,
        source: str,
        *,
        action: str,
        resource: str,
        resource_id: str | None = None,
        before: Any = None,
        after: Any = None,
        details: Any = None,
    ) -> None:
        if self._fail_on is not None and len(self.calls) + 1 == self._fail_on:
            raise RuntimeError("audit write failed")
        self.calls.append(
            {
                "source": source,
                "action": action,
                "resource": resource,
                "resource_id": resource_id,
                "before": before,
                "after": after,
                "details": details,
            }
        )


class FakeCommitter:
    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1


class FakeHealthStore:
    """In-memory offers and links with the selection rules of the real repository."""

    def __init__(
        self,
        offers: Sequence[DueOffer] = (),
        sources: Sequence[SourceUrlTarget] = (),
        links: Sequence[DirectLinkTarget] = (),
    ) -> None:
        self.status = {offer.id: offer.status for offer in offers}
        self.end_at = {offer.id: offer.end_at for offer in offers}
        self.sources = list(sources)
        self.links = list(links)
        self.expired_batches: list[list[str]] = []
        self.lock_requests: list[tuple[datetime, int]] = []

    async def lock_due_offers(self, now: datetime, limit: int) -> list[DueOffer]:
        self.lock_requests.append((now, limit))
        due = [
            DueOffer(offer_id, status, self.end_at[offer_id])
            for offer_id, status in self.status.items()
            if status is OfferStatus.PUBLISHED
            and self.end_at[offer_id] is not None
            and self.end_at[offer_id] <= now  # type: ignore[operator]
        ]
        return sorted(due, key=lambda row: (row.end_at, row.id))[:limit]  # type: ignore[arg-type,return-value]

    async def mark_expired(self, offer_ids: Sequence[str]) -> int:
        self.expired_batches.append(list(offer_ids))
        for offer_id in offer_ids:
            self.status[offer_id] = OfferStatus.EXPIRED
        return len(offer_ids)

    async def list_source_url_targets(
        self, now: datetime, limit: int
    ) -> list[SourceUrlTarget]:
        return self.sources[:limit]

    async def list_direct_link_targets(
        self, now: datetime, limit: int
    ) -> list[DirectLinkTarget]:
        return self.links[:limit]
