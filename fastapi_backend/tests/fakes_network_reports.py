"""An in-memory store for the network report ingest tests: rows, links, merchants, audit rows. No SQL.

``FakeReportDatabase.unit()`` hands out a unit of work like the real one: a working copy of the committed
state that becomes the committed state only when the unit's session commits, so a test sees what a failed
page leaves behind. The real SQL is covered by ``tests/commands/test_import_awin_reports_e2e.py``.
"""

import dataclasses
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from app.repositories.network_reports import LinkCandidate, ReportRowRecord
from app.services.network_report_run import ReportUnit


class FakeDriverError(Exception):
    """What a failed statement looks like to ``failure_cause``: an ``orig`` carrying an SQLSTATE."""

    def __init__(self, sqlstate: str) -> None:
        super().__init__("simulated driver failure")
        self.orig = type("Orig", (), {"sqlstate": sqlstate})()


@dataclass(frozen=True)
class FakeLink:
    link_id: str
    offer_id: str
    destination_url: str
    tracking_id: str | None = None
    provider: str = "awin"
    active: bool = True


@dataclass
class State:
    rows: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    links: list[FakeLink] = field(default_factory=list)
    merchants: dict[tuple[str, str], str] = field(default_factory=dict)
    audits: list[dict[str, Any]] = field(default_factory=list)
    counter: int = 0
    run_locks: int = 0

    def copy(self) -> "State":
        return dataclasses.replace(
            self,
            rows={key: dict(value) for key, value in self.rows.items()},
            links=list(self.links),
            merchants=dict(self.merchants),
            audits=list(self.audits),
        )


class FakeSession:
    def __init__(self, database: "FakeReportDatabase", working: State) -> None:
        self._database, self._working = database, working

    async def commit(self) -> None:
        self._database.commits += 1
        self._database.committed = self._working.copy()


class FakeAuditor:
    def __init__(self, working: State) -> None:
        self._working = working

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
        self._working.audits.append(
            {
                "source": source,
                "action": action,
                "resource": resource,
                "resource_id": resource_id,
                "details": details,
            }
        )


def _record(values: dict[str, Any]) -> ReportRowRecord:
    return ReportRowRecord(
        id=values["id"],
        external_id=values["external_id"],
        report_hash=values["report_hash"],
        offer_id=values["offer_id"],
        affiliate_link_id=values["affiliate_link_id"],
    )


class FakeReportStore:
    def __init__(self, working: State, database: "FakeReportDatabase") -> None:
        self.s, self._database = working, database

    async def lock_report_run(self) -> None:
        if self._database.lock_error is not None:
            raise self._database.lock_error
        self.s.run_locks += 1

    async def find_merchant_id(self, provider: str, external_id: str) -> str | None:
        return self.s.merchants.get((provider, external_id))

    async def links_by_tracking_id(
        self, provider: str, tracking_id: str
    ) -> list[LinkCandidate]:
        return [
            LinkCandidate(link.link_id, link.offer_id, link.destination_url)
            for link in self.s.links
            if link.active
            and link.provider == provider
            and link.tracking_id == tracking_id
        ]

    async def links_with_text(
        self, provider: str, fragment: str
    ) -> list[LinkCandidate]:
        return [
            LinkCandidate(link.link_id, link.offer_id, link.destination_url)
            for link in self.s.links
            if link.active
            and link.provider == provider
            and fragment in link.destination_url
        ]

    async def find_row(self, provider: str, external_id: str) -> ReportRowRecord | None:
        values = self.s.rows.get((provider, external_id))
        return _record(values) if values else None

    async def insert_row(self, values: dict[str, Any]) -> None:
        self._database.row_inserts += 1
        if self._database.fail_insert_at == self._database.row_inserts:
            raise RuntimeError("simulated database failure")
        self.s.counter += 1
        self.s.rows[(values["provider"], values["external_id"])] = {
            "id": f"row-{self.s.counter}",
            **values,
        }

    async def update_row(self, row_id: str, values: dict[str, Any]) -> None:
        for stored in self.s.rows.values():
            if stored["id"] == row_id:
                stored.update(values)
                return
        raise LookupError(row_id)

    async def touch_row(self, row_id: str, seen_at: Any) -> None:
        await self.update_row(row_id, {"last_seen_at": seen_at})


class FakeReportDatabase:
    """Committed state plus the units of work that change it."""

    def __init__(
        self,
        *,
        fail_insert_at: int | None = None,
        lock_error: Exception | None = None,
    ) -> None:
        self.committed = State()
        self.commits = 0
        self.units_opened = 0
        self.row_inserts = 0
        self.fail_insert_at = fail_insert_at
        self.lock_error = lock_error

    @asynccontextmanager
    async def unit(self) -> AsyncIterator[ReportUnit]:
        self.units_opened += 1
        working = self.committed.copy()
        yield ReportUnit(
            FakeReportStore(working, self),
            FakeSession(self, working),
            FakeAuditor(working),
        )

    # --- helpers for tests -----------------------------------------------------------------

    def add_link(self, link: FakeLink) -> None:
        self.committed.links.append(link)

    def map_merchant(self, advertiser_id: int, merchant_id: str) -> None:
        self.committed.merchants[("awin", str(advertiser_id))] = merchant_id

    def row(self, external_id: str) -> dict[str, Any]:
        return self.committed.rows[("awin", external_id)]
