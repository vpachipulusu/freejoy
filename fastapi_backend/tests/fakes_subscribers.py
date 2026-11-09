"""Test doubles for the alerts sign-up: an in-memory store, a committer, a mutable clock and helpers."""

import asyncio
import json
import re
from collections.abc import AsyncIterator, Awaitable, Callable, MutableMapping
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.email_provider import EmailMessage, FakeEmailProvider
from app.core.ids import new_id
from app.core.subscriber_tokens import SubscriberTokens
from app.models.platform.email_subscriber import AlertFrequency, SubscriberStatus
from app.repositories.subscribers import SubscriberRecord
from app.services.subscriber_delivery import ConfirmationDelivery, PendingConfirmation
from app.services.subscribers import SubscribeRequest, SubscriberService

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
SECRET = b"test-only-subscriber-token-secret-0123456789"
SITE = "https://freejoy.test"
LINK = re.compile(r"https://freejoy\.test/alerts/([a-z/-]+)\?token=([A-Za-z0-9_.-]+)")


class FakeClock:
    def __init__(self, now: datetime = NOW) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


class FakeCommitter:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


class FakeSubscriberStore:
    """Dict-backed store with the repository's behaviour. A rollback restores the last commit."""

    def __init__(self, committer: FakeCommitter | None = None) -> None:
        self.rows: dict[str, SubscriberRecord] = {}
        self.committer = committer or FakeCommitter()
        self._committed: dict[str, SubscriberRecord] = {}

    async def insert_pending(self, values: dict[str, Any]) -> str | None:
        if any(row.email == values["email"] for row in self.rows.values()):
            return None
        subscriber_id = new_id()
        self.rows[subscriber_id] = SubscriberRecord(
            id=subscriber_id,
            email=values["email"],
            status=SubscriberStatus.PENDING,
            categories=tuple(values["categories"]),
            frequency=values["frequency"],
            consent_at=values["consent_at"],
            confirmed_at=None,
            unsubscribed_at=None,
            provider_subscriber_id=None,
            confirmation_token_hash=None,
            confirmation_sent_at=None,
        )
        return subscriber_id

    async def get(
        self, subscriber_id: str, *, lock: bool = False
    ) -> SubscriberRecord | None:
        return self.rows.get(subscriber_id)

    async def get_by_email(
        self, email: str, *, lock: bool = False
    ) -> SubscriberRecord | None:
        return next((r for r in self.rows.values() if r.email == email), None)

    async def update(self, subscriber_id: str, values: dict[str, Any]) -> None:
        changes = dict(values)
        if "categories" in changes:
            changes["categories"] = tuple(changes["categories"])
        changes.pop("consent_text", None)
        changes.pop("consent_source", None)
        self.rows[subscriber_id] = replace(self.rows[subscriber_id], **changes)

    async def clear_confirmation(self, subscriber_id: str, token_hash: str) -> None:
        row = self.rows.get(subscriber_id)
        if row is not None and row.confirmation_token_hash == token_hash:
            self.rows[subscriber_id] = replace(
                row, confirmation_token_hash=None, confirmation_sent_at=None
            )


class CommitTrackingStore(FakeSubscriberStore):
    """Makes ``rollback`` discard uncommitted changes, like a database transaction."""

    def __init__(self) -> None:
        committer = _TransactionCommitter(self)
        super().__init__(committer)


class _TransactionCommitter(FakeCommitter):
    def __init__(self, store: FakeSubscriberStore) -> None:
        super().__init__()
        self._store = store

    async def commit(self) -> None:
        await super().commit()
        self._store._committed = dict(self._store.rows)

    async def rollback(self) -> None:
        await super().rollback()
        self._store.rows = dict(self._store._committed)


class GatedEmailProvider(FakeEmailProvider):
    """A provider whose ``send`` waits for ``release``: proves nothing awaits the provider too early."""

    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def send(self, message: EmailMessage) -> None:
        self.started.set()
        await self.release.wait()
        await super().send(message)


def make_request(email: str = "sam@example.com", **overrides: Any) -> SubscribeRequest:
    base = SubscribeRequest(
        email=email,
        categories=("beauty", "pets"),
        frequency=AlertFrequency.WEEKLY,
        consent_text="I agree to receive freebie emails from Freejoy.",
        consent_source="alerts_page",
    )
    return replace(base, **overrides)


class Harness:
    """A service wired to fakes, plus the pieces a test inspects."""

    def __init__(self, provider: FakeEmailProvider | None = None) -> None:
        self.store = CommitTrackingStore()
        self.provider = provider or FakeEmailProvider()
        self.clock = FakeClock()
        self.tokens = SubscriberTokens(SECRET)
        self.service = SubscriberService(
            self.store,
            self.store.committer,
            self.provider,
            self.tokens,
            SITE,
            "Freejoy",
            self.clock,
        )
        self.units_opened = 0
        self.delivery = ConfirmationDelivery(self.provider, self._unit_of_work)

    @asynccontextmanager
    async def _unit_of_work(
        self,
    ) -> AsyncIterator[tuple[FakeSubscriberStore, FakeCommitter]]:
        """A fresh unit of work for the background step; here it shares the in-memory store."""
        self.units_opened += 1
        yield self.store, self.committer

    async def subscribe(self, request: SubscribeRequest) -> PendingConfirmation | None:
        """What the route does: subscribe, then (after the commit) deliver the confirmation."""
        pending = await self.service.subscribe(request)
        if pending is not None:
            await self.delivery.deliver(pending)
        return pending

    @property
    def committer(self) -> FakeCommitter:
        return self.store.committer

    def last_links(self) -> dict[str, str]:
        """``{"confirm": token, "unsubscribe": token, "unsubscribe/one-click": token}`` of the last mail."""
        return link_tokens(self.provider.sent[-1].text)


def link_tokens(text: str) -> dict[str, str]:
    return {kind: token for kind, token in LINK.findall(text)}


# Only turns a hang into a failure (a route that awaited the provider would never answer while the
# gate is closed). It is not a speed budget: a loaded machine finishes the request, just later.
HANG_GUARD_SECONDS = 60


async def post_json_returning_at_response(
    app: Callable[..., Awaitable[None]],
    path: str,
    payload: dict[str, Any],
    headers: dict[str, str],
) -> tuple[int, asyncio.Task[None]]:
    """Drive one POST through the raw ASGI interface and return as soon as the response is complete.

    ``httpx.ASGITransport`` waits for the whole app call, background tasks included, so it cannot show
    what happens *after* the response. The task returned finishes when the app does.
    """
    body = json.dumps(payload).encode()
    raw_headers = [
        (b"host", b"test"),
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode()),
        *((k.lower().encode(), v.encode()) for k, v in headers.items()),
    ]
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": raw_headers,
        "client": ("127.0.0.1", 50000),
        "server": ("test", 80),
    }
    response_complete = asyncio.Event()
    app_finished = asyncio.Event()
    status: list[int] = []
    sent_request = False

    async def receive() -> MutableMapping[str, Any]:
        nonlocal sent_request
        if not sent_request:
            sent_request = True
            return {"type": "http.request", "body": body, "more_body": False}
        await app_finished.wait()
        return {"type": "http.disconnect"}

    async def send(message: MutableMapping[str, Any]) -> None:
        if message["type"] == "http.response.start":
            status.append(message["status"])
        elif message["type"] == "http.response.body" and not message.get("more_body"):
            response_complete.set()

    async def run() -> None:
        try:
            await app(scope, receive, send)
        finally:
            app_finished.set()

    task = asyncio.create_task(run())
    done = asyncio.create_task(response_complete.wait())
    await asyncio.wait(
        {task, done}, timeout=HANG_GUARD_SECONDS, return_when=asyncio.FIRST_COMPLETED
    )
    done.cancel()
    if not response_complete.is_set():
        if task.done():
            task.result()  # the app failed before answering: show that error
        task.cancel()
        raise AssertionError("the response was not completed before the provider call")
    return status[0], task
