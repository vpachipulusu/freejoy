"""End to end: the alerts opt-in lifecycle and retention on a real Postgres.

Runs only when ``TEST_DATABASE_URL`` points at a throwaway local database whose name ends in "_test"
(same contract as the other ``*_e2e.py`` suites). Routes, service, repository, SQL, token codec and rate-limit
dependencies are production code; only the email provider (recording fake) and the limiter (in-memory) are
replaced, so no email is ever sent.
"""

import asyncio
import json
import os
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.api.deps import get_email_provider, get_rate_limiter, get_session_provider
from app.api.v1.subscribers import get_confirmation_delivery
from app.core.config import settings
from app.core.database import DefaultSessionProvider, build_engine
from app.core.email_provider import EmailProviderError, FakeEmailProvider
from app.core.ids import new_id
from app.core.subscriber_tokens import SubscriberTokens, TokenPurpose, token_digest
from app.main import create_app
from app.models.platform.email_subscriber import AlertFrequency
from app.repositories.subscribers import SubscriberRepository
from app.services.subscriber_delivery import PendingConfirmation
from app.services.subscribers import SubscribeRequest, SubscriberService
from commands.purge_subscribers import purge_subscribers
from tests.alembic_support import run_alembic
from tests.fakes import FakeRateLimiter, as_limiter
from tests.fakes_subscribers import (
    GatedEmailProvider,
    link_tokens,
    post_json_returning_at_response,
)

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
BACKEND_ROOT = Path(__file__).resolve().parents[2]
KEY = "test-only-internal-service-key-not-for-deployment"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
EMAIL = "sam@example.com"
BODY: dict[str, Any] = {
    "email": EMAIL,
    "categories": ["beauty", "pets"],
    "frequency": "WEEKLY",
    "consent": True,
    "consent_text": "I agree to receive freebie emails from Freejoy.",
    "consent_source": "alerts_page",
}

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="TEST_DATABASE_URL is not set"
)


def _migrate(url: str, *args: str) -> None:
    run_alembic(url, *args)


@pytest.fixture(scope="module")
def database_url() -> Iterator[str]:
    assert TEST_DATABASE_URL is not None
    target = make_url(TEST_DATABASE_URL)
    assert (target.database or "").endswith("_test"), (
        "refusing to rebuild a database whose name does not end in _test"
    )
    assert target.host in LOCAL_HOSTS, "refusing to rebuild a non-local database"
    _migrate(TEST_DATABASE_URL, "downgrade", "base")
    _migrate(TEST_DATABASE_URL, "upgrade", "head")
    yield TEST_DATABASE_URL


@pytest.fixture
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    built = build_engine(database_url)
    async with built.begin() as connection:
        await connection.execute(text("DELETE FROM email_subscribers"))
    yield built
    await built.dispose()


@pytest.fixture
def provider() -> FakeEmailProvider:
    return FakeEmailProvider()


@pytest.fixture
def limiter() -> FakeRateLimiter:
    return FakeRateLimiter()


@pytest.fixture
def app(
    engine: AsyncEngine, provider: FakeEmailProvider, limiter: FakeRateLimiter
) -> FastAPI:
    built = create_app()
    sessions = DefaultSessionProvider(
        async_sessionmaker(engine, expire_on_commit=False)
    )
    built.dependency_overrides[get_session_provider] = lambda: sessions
    built.dependency_overrides[get_email_provider] = lambda: provider
    built.dependency_overrides[get_rate_limiter] = lambda: as_limiter(limiter)
    return built


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(
        transport=transport, base_url="http://test", headers={"X-Internal-Key": KEY}
    ) as http:
        yield http


async def rows(engine: AsyncEngine) -> list[dict[str, Any]]:
    async with engine.connect() as connection:
        result = await connection.execute(
            text("SELECT * FROM email_subscribers ORDER BY created_at")
        )
        return [dict(row._mapping) for row in result]


async def only_row(engine: AsyncEngine) -> dict[str, Any]:
    (row,) = await rows(engine)
    return row


async def insert_row(engine: AsyncEngine, **values: Any) -> str:
    row = {
        "id": new_id(),
        "email": f"{new_id()}@example.com",
        "status": "PENDING",
        "frequency": "DAILY",
        "consent_text": "wording",
        "consent_at": datetime.now(UTC),
        "consent_source": "alerts_page",
        **values,
    }
    async with engine.begin() as connection:
        await connection.execute(
            text(
                f"INSERT INTO email_subscribers ({', '.join(row)}) "
                f"VALUES ({', '.join(':' + name for name in row)})"
            ),
            row,
        )
    return str(row["id"])


def last_tokens(provider: FakeEmailProvider) -> dict[str, str]:
    return link_tokens(provider.sent[-1].text.replace("freejoy.test", "freejoy.test"))


@pytest.fixture(autouse=True)
def site_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "PUBLIC_SITE_URL", "https://freejoy.test")


async def test_the_whole_opt_in_lifecycle(
    client: AsyncClient, engine: AsyncEngine, provider: FakeEmailProvider
) -> None:
    subscribed = await client.post("/v1/subscribers", json=BODY)
    pending = await only_row(engine)
    tokens = last_tokens(provider)
    confirmed_response = await client.post(
        "/v1/subscribers/confirm", json={"token": tokens["confirm"]}
    )
    confirmed = await only_row(engine)
    unsubscribed_response = await client.post(
        "/v1/subscribers/unsubscribe", json={"token": tokens["unsubscribe"]}
    )
    unsubscribed = await only_row(engine)

    assert subscribed.status_code == 202
    assert pending["status"] == "PENDING"
    assert pending["email"] == EMAIL
    assert pending["categories"] == ["beauty", "pets"]
    assert pending["frequency"] == "WEEKLY"
    assert pending["consent_text"] == BODY["consent_text"]
    assert pending["consent_source"] == "alerts_page"
    assert pending["consent_at"] is not None
    assert pending["confirmation_sent_at"] is not None
    assert pending["confirmed_at"] is None
    assert confirmed_response.status_code == 200
    assert confirmed["status"] == "CONFIRMED"
    assert confirmed["confirmed_at"] is not None
    assert confirmed["confirmation_token_hash"] is None
    assert confirmed["provider_subscriber_id"] == "fake-1"
    assert unsubscribed_response.status_code == 200
    assert unsubscribed["status"] == "UNSUBSCRIBED"
    assert unsubscribed["unsubscribed_at"] is not None
    assert provider.deleted == [("fake-1", EMAIL)]
    assert unsubscribed["categories"] == []
    assert unsubscribed["provider_subscriber_id"] is None


async def test_only_a_hash_of_the_token_reaches_the_database(
    client: AsyncClient, engine: AsyncEngine, provider: FakeEmailProvider
) -> None:
    await client.post("/v1/subscribers", json=BODY)

    stored = await only_row(engine)
    token = last_tokens(provider)["confirm"]
    assert stored["confirmation_token_hash"] == token_digest(token)
    assert token not in " ".join(str(value) for value in stored.values())


async def test_mixed_case_input_is_stored_lower_case_once(
    client: AsyncClient, engine: AsyncEngine, provider: FakeEmailProvider
) -> None:
    await client.post("/v1/subscribers", json={**BODY, "email": "Sam@Example.COM"})
    await client.post("/v1/subscribers", json={**BODY, "email": " SAM@example.com "})

    assert (await only_row(engine))["email"] == EMAIL
    assert len(provider.sent) == 1


async def test_a_replayed_confirm_token_is_rejected_and_syncs_nothing_twice(
    client: AsyncClient, provider: FakeEmailProvider
) -> None:
    await client.post("/v1/subscribers", json=BODY)
    token = last_tokens(provider)["confirm"]

    first = await client.post("/v1/subscribers/confirm", json={"token": token})
    replay = await client.post("/v1/subscribers/confirm", json={"token": token})

    assert (first.status_code, replay.status_code) == (200, 400)
    assert replay.json() == {"success": False, "error": "Invalid or expired link"}
    assert len(provider.upserts) == 1


async def test_tampered_and_expired_confirm_tokens_are_rejected(
    client: AsyncClient, engine: AsyncEngine, provider: FakeEmailProvider
) -> None:
    await client.post("/v1/subscribers", json=BODY)
    token = last_tokens(provider)["confirm"]
    payload, signature = token.split(".")
    stored = await only_row(engine)
    expired = SubscriberTokens(
        settings.SUBSCRIBER_TOKEN_SECRET.get_secret_value().encode()  # type: ignore[union-attr]
    ).issue(TokenPurpose.CONFIRM, stored["id"], datetime.now(UTC) - timedelta(hours=1))
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE email_subscribers SET confirmation_token_hash = :h"),
            {"h": token_digest(expired)},
        )

    responses = [
        await client.post("/v1/subscribers/confirm", json={"token": candidate})
        for candidate in (f"{payload}.{signature[:-2]}AA", expired, "garbage")
    ]

    assert [r.status_code for r in responses] == [400, 400, 400]
    assert len({r.text for r in responses}) == 1
    assert (await only_row(engine))["status"] == "PENDING"


async def test_a_confirmation_is_resent_at_most_once_an_hour(
    client: AsyncClient, engine: AsyncEngine, provider: FakeEmailProvider
) -> None:
    await client.post("/v1/subscribers", json=BODY)
    first_token = last_tokens(provider)["confirm"]
    await client.post("/v1/subscribers", json=BODY)
    assert len(provider.sent) == 1

    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE email_subscribers "
                "SET confirmation_sent_at = now() - interval '61 minutes'"
            )
        )
    await client.post("/v1/subscribers", json=BODY)

    assert len(provider.sent) == 2
    old = await client.post("/v1/subscribers/confirm", json={"token": first_token})
    new = await client.post(
        "/v1/subscribers/confirm", json={"token": last_tokens(provider)["confirm"]}
    )
    assert (old.status_code, new.status_code) == (400, 200)


async def test_new_pending_confirmed_and_unsubscribed_addresses_get_identical_answers(
    client: AsyncClient, provider: FakeEmailProvider
) -> None:
    new = await client.post("/v1/subscribers", json=BODY)
    pending = await client.post("/v1/subscribers", json=BODY)
    tokens = last_tokens(provider)
    await client.post("/v1/subscribers/confirm", json={"token": tokens["confirm"]})
    confirmed = await client.post("/v1/subscribers", json=BODY)
    await client.post(
        "/v1/subscribers/unsubscribe", json={"token": tokens["unsubscribe"]}
    )
    unsubscribed = await client.post("/v1/subscribers", json=BODY)

    for response in (pending, confirmed, unsubscribed):
        assert (response.status_code, response.text) == (new.status_code, new.text)
        assert response.headers["cache-control"] == new.headers["cache-control"]


async def test_a_confirmed_subscriber_is_untouched_by_a_later_signup_form(
    client: AsyncClient, engine: AsyncEngine, provider: FakeEmailProvider
) -> None:
    await client.post("/v1/subscribers", json=BODY)
    await client.post(
        "/v1/subscribers/confirm", json={"token": last_tokens(provider)["confirm"]}
    )
    before = await only_row(engine)

    await client.post(
        "/v1/subscribers",
        json={**BODY, "categories": ["other"], "frequency": "DAILY"},
    )

    assert await only_row(engine) == before
    assert len(provider.sent) == 1


async def test_an_unsubscribed_address_is_never_reopened_by_a_form_submission(
    client: AsyncClient, engine: AsyncEngine, provider: FakeEmailProvider
) -> None:
    await client.post("/v1/subscribers", json=BODY)
    tokens = last_tokens(provider)
    await client.post(
        "/v1/subscribers/unsubscribe", json={"token": tokens["unsubscribe"]}
    )
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE email_subscribers "
                "SET confirmation_sent_at = now() - interval '2 hours'"
            )
        )
    before = await only_row(engine)

    again = await client.post("/v1/subscribers", json=BODY)

    assert again.status_code == 202
    assert await only_row(engine) == before
    assert before["status"] == "UNSUBSCRIBED"
    assert len(provider.sent) == 1


async def test_a_resent_confirmation_keeps_the_first_consent_record(
    client: AsyncClient, engine: AsyncEngine, provider: FakeEmailProvider
) -> None:
    await client.post("/v1/subscribers", json=BODY)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE email_subscribers SET consent_at = now() - interval '5 days', "
                "confirmation_sent_at = now() - interval '2 hours'"
            )
        )
    first = await only_row(engine)

    await client.post(
        "/v1/subscribers",
        json={**BODY, "frequency": "DAILY", "consent_text": "newer wording"},
    )

    row = await only_row(engine)
    assert len(provider.sent) == 2
    assert row["consent_at"] == first["consent_at"]
    assert row["consent_text"] == first["consent_text"]
    assert row["frequency"] == "DAILY"


async def test_a_pending_row_past_the_retention_period_is_restarted_by_a_resubmission(
    client: AsyncClient, engine: AsyncEngine, provider: FakeEmailProvider
) -> None:
    await client.post("/v1/subscribers", json=BODY)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE email_subscribers SET consent_at = now() - interval '31 days', "
                "confirmation_sent_at = now() - interval '31 days'"
            )
        )

    await client.post("/v1/subscribers", json={**BODY, "consent_text": "newer wording"})

    row = await only_row(engine)
    assert len(provider.sent) == 2
    assert row["consent_text"] == "newer wording"
    assert datetime.now(UTC) - row["consent_at"] < timedelta(minutes=5)


async def test_resubmitting_does_not_extend_a_pending_signups_retention(
    client: AsyncClient, engine: AsyncEngine
) -> None:
    await client.post("/v1/subscribers", json=BODY)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE email_subscribers SET consent_at = now() - interval '29 days', "
                "confirmation_sent_at = now() - interval '2 hours'"
            )
        )
    await client.post("/v1/subscribers", json=BODY)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async with maker() as session:
        done = await purge_subscribers(
            SubscriberRepository(session),
            session,
            FakeEmailProvider(),
            datetime.now(UTC) + timedelta(days=2),
            dry_run=False,
        )

    assert done.deleted == 1
    assert await rows(engine) == []


async def test_simultaneous_submissions_make_one_row_and_one_email(
    client: AsyncClient, engine: AsyncEngine, provider: FakeEmailProvider
) -> None:
    responses = await asyncio.gather(
        *(client.post("/v1/subscribers", json=BODY) for _ in range(5))
    )

    assert {r.status_code for r in responses} == {202}
    assert len(await rows(engine)) == 1
    assert len(provider.sent) == 1


async def test_simultaneous_confirms_of_one_token_succeed_once(
    client: AsyncClient, provider: FakeEmailProvider
) -> None:
    await client.post("/v1/subscribers", json=BODY)
    token = last_tokens(provider)["confirm"]

    responses = await asyncio.gather(
        *(
            client.post("/v1/subscribers/confirm", json={"token": token})
            for _ in range(4)
        )
    )

    assert sorted(r.status_code for r in responses) == [200, 400, 400, 400]
    assert len(provider.upserts) == 1


async def test_the_response_is_complete_before_the_provider_is_called(
    app: FastAPI, engine: AsyncEngine
) -> None:
    gated = GatedEmailProvider()
    app.dependency_overrides[get_email_provider] = lambda: gated

    status, finished = await post_json_returning_at_response(
        app, "/v1/subscribers", BODY, {"X-Internal-Key": KEY}
    )

    committed = await only_row(engine)
    assert status == 202
    assert gated.sent == []
    assert committed["confirmation_sent_at"] is not None
    await asyncio.wait_for(gated.started.wait(), timeout=5)
    gated.release.set()
    await finished
    assert committed["confirmation_token_hash"] == token_digest(
        link_tokens(gated.sent[0].text)["confirm"]
    )
    assert await only_row(engine) == committed


async def test_the_mail_lands_in_the_outbox_once_the_response_is_done(
    app: FastAPI, client: AsyncClient, tmp_path: Path
) -> None:
    outbox = tmp_path / "outbox.jsonl"
    app.dependency_overrides[get_email_provider] = lambda: FakeEmailProvider(outbox)

    response = await client.post("/v1/subscribers", json=BODY)

    (line,) = outbox.read_text(encoding="utf-8").splitlines()
    assert response.status_code == 202
    assert json.loads(line)["to"] == EMAIL


async def test_a_send_failure_withdraws_the_claim_and_the_next_try_works(
    client: AsyncClient, engine: AsyncEngine, provider: FakeEmailProvider
) -> None:
    provider.error = EmailProviderError("send", 500)
    failed = await client.post("/v1/subscribers", json=BODY)
    row = await only_row(engine)
    assert failed.status_code == 202
    assert failed.json() == {"success": True, "data": {"status": "accepted"}}
    assert row["status"] == "PENDING"
    assert row["confirmation_sent_at"] is None
    assert row["confirmation_token_hash"] is None
    assert provider.sent == []

    provider.error = None
    retried = await client.post("/v1/subscribers", json=BODY)

    assert retried.status_code == 202
    assert len(provider.sent) == 1
    assert (await only_row(engine))["confirmation_token_hash"] == token_digest(
        last_tokens(provider)["confirm"]
    )
    confirmed = await client.post(
        "/v1/subscribers/confirm", json={"token": last_tokens(provider)["confirm"]}
    )
    assert confirmed.status_code == 200


async def committed_pending(engine: AsyncEngine) -> PendingConfirmation:
    """Run the service's subscribe step alone: the row is committed, the email is not sent."""
    maker = async_sessionmaker(engine, expire_on_commit=False)
    secret = settings.SUBSCRIBER_TOKEN_SECRET
    assert secret is not None
    async with maker() as session:
        service = SubscriberService(
            SubscriberRepository(session),
            session,
            FakeEmailProvider(),
            SubscriberTokens(secret.get_secret_value().encode()),
            settings.PUBLIC_SITE_URL,
            "Freejoy",
        )
        pending = await service.subscribe(
            SubscribeRequest(
                email=EMAIL,
                categories=("beauty", "pets"),
                frequency=AlertFrequency.WEEKLY,
                consent_text=str(BODY["consent_text"]),
                consent_source="alerts_page",
            )
        )
    assert pending is not None
    return pending


async def test_a_late_failed_send_does_not_withdraw_a_newer_token(
    client: AsyncClient, engine: AsyncEngine, provider: FakeEmailProvider
) -> None:
    first = await committed_pending(engine)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE email_subscribers "
                "SET confirmation_sent_at = now() - interval '2 hours'"
            )
        )
    await client.post("/v1/subscribers", json=BODY)
    newer = await only_row(engine)
    provider.error = EmailProviderError("send", 503)
    sessions = DefaultSessionProvider(
        async_sessionmaker(engine, expire_on_commit=False)
    )

    await get_confirmation_delivery(provider, sessions).deliver(first)

    assert newer["confirmation_token_hash"] != first.token_hash
    assert await only_row(engine) == newer


async def test_a_sync_failure_on_confirm_keeps_the_subscriber_pending(
    client: AsyncClient, engine: AsyncEngine, provider: FakeEmailProvider
) -> None:
    await client.post("/v1/subscribers", json=BODY)
    token = last_tokens(provider)["confirm"]
    provider.error = EmailProviderError("upsert", 500)
    failed = await client.post("/v1/subscribers/confirm", json={"token": token})
    assert failed.status_code == 503
    assert (await only_row(engine))["status"] == "PENDING"

    provider.error = None
    retried = await client.post("/v1/subscribers/confirm", json={"token": token})

    assert retried.status_code == 200
    assert (await only_row(engine))["status"] == "CONFIRMED"


async def test_a_sync_failure_on_unsubscribe_still_records_it(
    client: AsyncClient, engine: AsyncEngine, provider: FakeEmailProvider
) -> None:
    await client.post("/v1/subscribers", json=BODY)
    tokens = last_tokens(provider)
    provider.error = EmailProviderError("remove", 500)

    failed = await client.post(
        "/v1/subscribers/unsubscribe", json={"token": tokens["unsubscribe"]}
    )

    assert failed.status_code == 503
    assert (await only_row(engine))["status"] == "UNSUBSCRIBED"


async def test_rate_limits_key_on_ip_and_a_hash_not_the_address(
    client: AsyncClient, limiter: FakeRateLimiter
) -> None:
    await client.post("/v1/subscribers", json=BODY)

    identifiers = [identifier for identifier, _ in limiter.hits]
    assert [":".join(i.split(":")[:2]) for i in identifiers] == [
        "subscribe:ip",
        "subscribe_email:email",
    ]
    assert all(EMAIL not in identifier for identifier in identifiers)


async def test_the_database_refuses_an_upper_case_address(engine: AsyncEngine) -> None:
    with pytest.raises(Exception, match="email_lowercase"):
        await insert_row(engine, email="Sam@Example.com")


async def test_retention_removes_stale_pending_and_old_unsubscribed_rows_only(
    engine: AsyncEngine,
) -> None:
    now = datetime.now(UTC)
    old_pending = await insert_row(engine, consent_at=now - timedelta(days=31))
    fresh_pending = await insert_row(engine, consent_at=now - timedelta(days=29))
    old_unsub = await insert_row(
        engine,
        status="UNSUBSCRIBED",
        confirmed_at=now - timedelta(days=500),
        unsubscribed_at=now - timedelta(days=366),
        provider_subscriber_id="p-old",
    )
    fresh_unsub = await insert_row(
        engine, status="UNSUBSCRIBED", unsubscribed_at=now - timedelta(days=364)
    )
    ancient_confirmed = await insert_row(
        engine,
        status="CONFIRMED",
        consent_at=now - timedelta(days=900),
        confirmed_at=now - timedelta(days=899),
    )
    provider = FakeEmailProvider()
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async with maker() as session:
        dry = await purge_subscribers(
            SubscriberRepository(session), session, provider, now, dry_run=True
        )
    assert (dry.pending, dry.unsubscribed, dry.deleted) == (1, 1, 0)
    assert len(await rows(engine)) == 5

    async with maker() as session:
        done = await purge_subscribers(
            SubscriberRepository(session), session, provider, now, dry_run=False
        )

    remaining = {row["id"] for row in await rows(engine)}
    assert done.deleted == 2
    assert remaining == {fresh_pending, fresh_unsub, ancient_confirmed}
    assert old_pending not in remaining and old_unsub not in remaining
    assert provider.deleted == [("p-old", None)]


async def test_retention_keeps_a_row_whose_provider_erase_fails(
    engine: AsyncEngine,
) -> None:
    now = datetime.now(UTC)
    kept = await insert_row(
        engine,
        status="UNSUBSCRIBED",
        unsubscribed_at=now - timedelta(days=400),
        provider_subscriber_id="p-1",
    )
    provider = FakeEmailProvider()
    provider.error = EmailProviderError("delete", 500)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async with maker() as session:
        done = await purge_subscribers(
            SubscriberRepository(session), session, provider, now, dry_run=False
        )

    assert (done.deleted, done.provider_failures) == (0, 1)
    assert [row["id"] for row in await rows(engine)] == [kept]


async def test_delete_many_applies_the_retention_test_again(
    engine: AsyncEngine,
) -> None:
    now = datetime.now(UTC)
    confirmed = await insert_row(
        engine,
        status="CONFIRMED",
        consent_at=now - timedelta(days=900),
        confirmed_at=now - timedelta(days=899),
    )
    expired = await insert_row(engine, consent_at=now - timedelta(days=31))
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async with maker() as session:
        count = await SubscriberRepository(session).delete_many(
            [confirmed, expired], now - timedelta(days=30), now - timedelta(days=365)
        )
        await session.commit()

    assert count == 1
    assert [row["id"] for row in await rows(engine)] == [confirmed]


@pytest.fixture
def statements(engine: AsyncEngine) -> Iterator[list[str]]:
    seen: list[str] = []

    def record(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        seen.append(statement.split()[0].upper())

    event.listen(engine.sync_engine, "before_cursor_execute", record)
    yield seen
    event.remove(engine.sync_engine, "before_cursor_execute", record)


async def test_the_statements_each_address_state_runs_match_the_documented_table(
    client: AsyncClient,
    engine: AsyncEngine,
    provider: FakeEmailProvider,
    statements: list[str],
) -> None:
    """Pins the residual timing difference: no path waits on the provider, the SQL differs by a few statements."""
    counts: dict[str, list[str]] = {}

    async def post(state: str) -> None:
        statements.clear()
        await client.post("/v1/subscribers", json=BODY)
        counts[state] = list(statements)

    await post("new")
    await post("recent")
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE email_subscribers "
                "SET confirmation_sent_at = now() - interval '2 hours'"
            )
        )
    await post("resend")
    tokens = last_tokens(provider)
    await client.post("/v1/subscribers/confirm", json={"token": tokens["confirm"]})
    await post("confirmed")
    await client.post(
        "/v1/subscribers/unsubscribe", json={"token": tokens["unsubscribe"]}
    )
    await post("unsubscribed")

    assert counts == {
        "new": ["INSERT", "UPDATE"],
        "recent": ["INSERT", "SELECT"],
        "resend": ["INSERT", "SELECT", "UPDATE"],
        "confirmed": ["INSERT", "SELECT"],
        "unsubscribed": ["INSERT", "SELECT"],
    }
