"""End to end: signed Stripe payloads through the real ASGI app against a real Postgres.

Runs only when ``TEST_DATABASE_URL`` points at a throwaway database whose name
ends in "_test" on localhost (CI provides one, see .github/workflows/backend.yml). The fixture
rebuilds the schema with the real Alembic migrations (downgrade base, upgrade head),
so it destroys whatever that database holds. The default ``uv run pytest`` skips it.
"""

import asyncio
import json
import os
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.api.deps import get_session_provider
from app.core.config import settings
from app.core.database import DefaultSessionProvider, build_engine
from app.core.ids import new_id
from app.core.stripe_signature import compute_signature
from app.main import create_app
from app.repositories.billing import OrganizationBillingRepository
from tests.alembic_support import run_alembic

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
BACKEND_ROOT = Path(__file__).resolve().parents[2]
SECRET = "whsec_e2e_only_not_a_real_secret"
PRO_PRICE = "price_e2e_pro"
ENT_PRICE = "price_e2e_enterprise"
T0 = 1_800_000_000
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}

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
    yield built
    await built.dispose()


@pytest.fixture
async def client(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", SecretStr(SECRET))
    monkeypatch.setattr(settings, "STRIPE_PRICE_ID_PRO_MONTHLY", PRO_PRICE)
    monkeypatch.setattr(settings, "STRIPE_PRICE_ID_ENTERPRISE_MONTHLY", ENT_PRICE)
    app = create_app()
    provider = DefaultSessionProvider(
        async_sessionmaker(engine, expire_on_commit=False)
    )
    app.dependency_overrides[get_session_provider] = lambda: provider
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


@pytest.fixture
async def org(engine: AsyncEngine) -> dict[str, str]:
    ids = {
        "id": new_id(),
        "customer": f"cus_{new_id()}",
        "subscription": f"sub_{new_id()}",
    }
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO organizations (id, name, slug, stripe_customer_id) "
                "VALUES (:id, 'E2E Org', :slug, :customer)"
            ),
            {**ids, "slug": f"e2e-{ids['id']}"},
        )
    return ids


def subscription_event(
    org: dict[str, str],
    *,
    event_id: str | None = None,
    event_type: str = "customer.subscription.updated",
    created: int = T0,
    status: str = "active",
    price: str = PRO_PRICE,
) -> dict[str, Any]:
    return {
        "id": event_id or f"evt_{new_id()}",
        "type": event_type,
        "created": created,
        "data": {
            "object": {
                "id": org["subscription"],
                "customer": org["customer"],
                "status": status,
                "current_period_end": created + 2_592_000,
                "items": {"data": [{"price": {"id": price}}]},
            }
        },
    }


async def deliver(client: AsyncClient, event: dict[str, Any], secret: str = SECRET):
    body = json.dumps(event).encode()
    stamp = int(time.time())
    signature = compute_signature(secret, stamp, body)
    return await client.post(
        "/webhooks/stripe",
        content=body,
        headers={"Stripe-Signature": f"t={stamp},v1={signature}"},
    )


async def organization_row(engine: AsyncEngine, org_id: str):
    async with engine.connect() as connection:
        row = await connection.execute(
            text(
                "SELECT subscription_tier, subscription_status, stripe_subscription_id, "
                "stripe_price_id FROM organizations WHERE id = :id"
            ),
            {"id": org_id},
        )
        return row.one()


async def count_events(engine: AsyncEngine, event_id: str) -> int:
    async with engine.connect() as connection:
        result = await connection.execute(
            text("SELECT count(*) FROM stripe_events WHERE stripe_event_id = :id"),
            {"id": event_id},
        )
        return result.scalar_one()


async def test_valid_event_updates_the_organisation(client, engine, org):
    event = subscription_event(org)

    response = await deliver(client, event)

    assert response.status_code == 200
    row = await organization_row(engine, org["id"])
    assert tuple(row) == ("pro", "active", org["subscription"], PRO_PRICE)
    assert await count_events(engine, event["id"]) == 1


async def test_replayed_event_is_processed_only_once(client, engine, org):
    activate = subscription_event(org, created=T0)
    await deliver(client, activate)
    cancel = subscription_event(org, created=T0 + 10, status="canceled")
    await deliver(client, cancel)
    # Stripe redelivers the first event after the cancellation; it must not re-activate.

    replay = await deliver(client, activate)

    assert replay.status_code == 200
    assert (await organization_row(engine, org["id"]))[:2] == ("free", "canceled")
    assert await count_events(engine, activate["id"]) == 1


async def test_concurrent_deliveries_of_one_event_apply_it_once(client, engine, org):
    event = subscription_event(org)

    responses = await asyncio.gather(*(deliver(client, event) for _ in range(5)))

    assert [r.status_code for r in responses] == [200] * 5
    assert await count_events(engine, event["id"]) == 1
    assert (await organization_row(engine, org["id"]))[0] == "pro"


async def test_bad_signature_is_rejected_and_nothing_is_processed(client, engine, org):
    event = subscription_event(org)

    response = await deliver(client, event, secret="whsec_wrong")

    assert response.status_code == 400
    assert response.json() == {"success": False, "error": "Invalid signature"}
    assert await count_events(engine, event["id"]) == 0
    assert (await organization_row(engine, org["id"]))[0] == "free"


async def test_unknown_event_type_is_acknowledged_and_ignored(client, engine, org):
    event = subscription_event(org, event_type="invoice.paid")

    response = await deliver(client, event)

    assert response.status_code == 200
    assert await count_events(engine, event["id"]) == 0
    assert (await organization_row(engine, org["id"]))[0] == "free"


async def test_out_of_order_older_event_does_not_downgrade(client, engine, org):
    newer = subscription_event(org, created=T0 + 100, status="active", price=ENT_PRICE)
    older = subscription_event(org, created=T0, status="active", price=PRO_PRICE)
    await deliver(client, newer)

    response = await deliver(client, older)

    assert response.status_code == 200
    row = await organization_row(engine, org["id"])
    assert (row[0], row[3]) == ("enterprise", ENT_PRICE)
    assert await count_events(engine, older["id"]) == 1


async def test_failed_handler_leaves_the_event_unprocessed_so_retry_applies_it(
    client, engine, org, monkeypatch
):
    event = subscription_event(org)

    async def boom(self: Any, organization_id: str, change: Any) -> None:
        raise RuntimeError("simulated database failure")

    with monkeypatch.context() as patch:
        patch.setattr(OrganizationBillingRepository, "apply", boom)
        failed = await deliver(client, event)

    assert failed.status_code == 500
    assert await count_events(engine, event["id"]) == 0
    assert (await organization_row(engine, org["id"]))[0] == "free"

    retried = await deliver(client, event)

    assert retried.status_code == 200
    assert await count_events(engine, event["id"]) == 1
    assert (await organization_row(engine, org["id"]))[0] == "pro"


async def test_event_for_an_unknown_customer_is_acknowledged_but_not_recorded(
    client, engine, org
):
    stranger = {**org, "customer": "cus_nobody", "subscription": "sub_nobody"}
    event = subscription_event(stranger)

    response = await deliver(client, event)

    assert response.status_code == 200
    assert await count_events(engine, event["id"]) == 0
    assert (await organization_row(engine, org["id"]))[0] == "free"
