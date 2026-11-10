"""Fakes for the Awin importer: a client that serves canned pages and a clock that never sleeps.

Nothing here opens a socket. ``FakeAwinClient`` is what the importer and the command tests use;
``tests/core/test_awin_client.py`` drives the real ``HttpxAwinClient`` over ``httpx.MockTransport``.
"""

from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any

from app.core.awin_client import (
    AwinError,
    PromotionFilters,
    PromotionsPage,
    TransactionsPage,
)


class FakeAwinClient:
    """Serves ``pages`` in order; a page may be an ``AwinError`` to raise. Records every request."""

    def __init__(
        self,
        pages: Sequence[Sequence[Any] | AwinError] = (),
        *,
        total_pages: int | None = None,
        on_fetch: Callable[[], None] | None = None,
        transaction_pages: Sequence[Sequence[Any] | AwinError] = (),
    ) -> None:
        self.pages = list(pages)
        self.transaction_pages = list(transaction_pages)
        self.transaction_requests: list[tuple[datetime, datetime, int, int]] = []
        self.total_pages = total_pages
        self.on_fetch = on_fetch
        self.requests: list[tuple[PromotionFilters, int, int]] = []
        self.closed = False

    async def fetch_promotions(
        self, filters: PromotionFilters, *, page: int, page_size: int
    ) -> PromotionsPage:
        self.requests.append((filters, page, page_size))
        if self.on_fetch is not None:
            self.on_fetch()
        index = page - 1
        served = self.pages[index] if index < len(self.pages) else []
        if isinstance(served, AwinError):
            raise served
        # a total only comes with the {"data": ..., "pagination": ...} envelope
        object_envelope = self.total_pages is not None
        return PromotionsPage(
            tuple(served),
            self.total_pages,
            "object" if object_envelope else "list",
            object_envelope,
        )

    async def fetch_transactions(
        self, start: datetime, end: datetime, *, page: int, page_size: int
    ) -> TransactionsPage:
        """Serves ``transaction_pages`` in order (one list per request, whatever the window)."""
        self.transaction_requests.append((start, end, page, page_size))
        if self.on_fetch is not None:
            self.on_fetch()
        index = len(self.transaction_requests) - 1
        served = (
            self.transaction_pages[index] if index < len(self.transaction_pages) else []
        )
        if isinstance(served, AwinError):
            raise served
        return TransactionsPage(tuple(served), self.total_pages)

    async def aclose(self) -> None:
        self.closed = True


class FakeTime:
    """A monotonic clock that only moves when something sleeps."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds
