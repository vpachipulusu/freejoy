"""End to end: the organisation billing routes, dependency chain and webhook hand-off on a real Postgres.

Same contract as ``test_stripe_webhook_e2e.py``: runs only when ``TEST_DATABASE_URL``
points at a throwaway local database whose name ends in "_test", and rebuilds its
schema with the real Alembic migrations. Only Firebase (the session identity comes from
an ``X-Test-Uid`` header) and Stripe (a recording fake) are replaced. The membership
check, user lookup, repositories, return-URL allow-list, transaction handling and
the Stripe webhook are the production code.
"""

import asyncio
import json
import os
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Annotated, Any

import pytest
from fastapi import Header, HTTPException
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.api.auth import get_session_identity
from app.api.deps import get_session_provider
from app.api.v1.org_billing import stripe_billing
from app.core.config import settings
from app.core.database import DefaultSessionProvider, build_engine
from app.core.firebase import VerifiedIdentity
from app.core.ids import new_id
from app.core.stripe_signature import compute_signature
from app.main import create_app
from tests.alembic_support import run_alembic
from tests.fakes_billing import FakeStripe

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
BACKEND_ROOT = Path(__file__).resolve().parents[2]
KEY = "test-only-internal-service-key-not-for-deployment"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
WEBHOOK_SECRET = "whsec_e2e_billing_only_not_real"
RETURN_URL = "https://app.example.com/settings/billing"
PRO_MONTHLY = "price_e2e_pro_m"
PRO_YEARLY = "price_e2e_pro_y"
T0 = 1_800_000_000

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
        await connection.execute(
            text("TRUNCATE users, organizations, stripe_events CASCADE")
        )
    yield built
    await built.dispose()


@pytest.fixture
def stripe() -> FakeStripe:
    return FakeStripe()


@pytest.fixture
async def client(
    engine: AsyncEngine, stripe: FakeStripe, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    monkeypatch.setattr(
        settings, "BILLING_RETURN_URL_ORIGINS", "https://app.example.com"
    )
    monkeypatch.setattr(settings, "STRIPE_PRICE_ID_PRO_MONTHLY", PRO_MONTHLY)
    monkeypatch.setattr(settings, "STRIPE_PRICE_ID_PRO_YEARLY", PRO_YEARLY)
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", SecretStr(WEBHOOK_SECRET))
    app = create_app()
    provider = DefaultSessionProvider(
        async_sessionmaker(engine, expire_on_commit=False)
    )
    app.dependency_overrides[get_session_provider] = lambda: provider
    app.dependency_overrides[stripe_billing] = lambda: stripe

    async def identity(
        x_test_uid: Annotated[str | None, Header()] = None,
        x_test_verified: Annotated[str, Header()] = "yes",
    ) -> VerifiedIdentity:
        if x_test_uid is None:
            raise HTTPException(401, "Invalid authentication")
        return VerifiedIdentity(
            x_test_uid,
            f"{x_test_uid}@example.com",
            x_test_verified == "yes",
            int(time.time()),
        )

    app.dependency_overrides[get_session_identity] = identity
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(
        transport=transport, base_url="http://test", headers={"X-Internal-Key": KEY}
    ) as http:
        yield http


async def seed_org(
    engine: AsyncEngine, name: str, members: dict[str, str], **columns: Any
) -> str:
    """Insert an organisation and members ``{user name: role}`` (firebase uid ``fb-<name>``)."""
    org_id = new_id()
    values = {
        "id": org_id,
        "name": name.title(),
        "slug": f"{name}-{org_id}",
        "customer": columns.get("customer"),
        "subscription": columns.get("subscription"),
        "price": columns.get("price"),
        "tier": columns.get("tier", "free"),
        "status": columns.get("status", "inactive"),
    }
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO organizations (id, name, slug, stripe_customer_id, "
                "stripe_subscription_id, stripe_price_id, subscription_tier, "
                "subscription_status) VALUES (:id, :name, :slug, :customer, "
                ":subscription, :price, :tier, :status)"
            ),
            values,
        )
        for user, role in members.items():
            found = (
                await connection.execute(
                    text("SELECT id FROM users WHERE firebase_uid = :uid"),
                    {"uid": f"fb-{user}"},
                )
            ).scalar_one_or_none()
            user_id = found or new_id()
            if not found:
                await connection.execute(
                    text(
                        "INSERT INTO users (id, firebase_uid, email, email_verified) "
                        "VALUES (:id, :uid, :email, true)"
                    ),
                    {
                        "id": user_id,
                        "uid": f"fb-{user}",
                        "email": f"{user}@example.com",
                    },
                )
            await connection.execute(
                text(
                    "INSERT INTO memberships (id, organization_id, user_id, role) "
                    "VALUES (:id, :org, :user, :role)"
                ),
                {"id": new_id(), "org": org_id, "user": user_id, "role": role},
            )
    return org_id


async def org_row(engine: AsyncEngine, org_id: str) -> tuple[Any, ...]:
    async with engine.connect() as connection:
        row = await connection.execute(
            text(
                "SELECT stripe_customer_id, stripe_subscription_id, stripe_price_id, "
                "subscription_tier, subscription_status FROM organizations WHERE id = :id"
            ),
            {"id": org_id},
        )
        return tuple(row.one())


def as_user(name: str, **extra: str) -> dict[str, str]:
    return {"X-Test-Uid": f"fb-{name}", **extra}


def checkout_body(**overrides: str) -> dict[str, str]:
    return {
        "plan": "pro",
        "interval": "monthly",
        "success_url": RETURN_URL,
        "cancel_url": RETURN_URL,
        **overrides,
    }


async def test_unauthenticated_request_is_401(client, engine):
    org = await seed_org(engine, "acme", {"alice": "OWNER"})

    result = await client.get(f"/v1/organizations/{org}/billing")

    assert result.status_code == 401


async def test_unverified_email_is_403(client, engine, stripe):
    org = await seed_org(engine, "acme", {"alice": "OWNER"})

    result = await client.get(
        f"/v1/organizations/{org}/billing",
        headers=as_user("alice", **{"X-Test-Verified": "no"}),
    )

    assert result.status_code == 403
    assert stripe.calls == []


async def test_owner_reads_the_plan_and_subscription_state(client, engine):
    org = await seed_org(
        engine,
        "acme",
        {"alice": "OWNER"},
        customer="cus_a",
        subscription="sub_a",
        tier="pro",
        status="active",
    )

    result = await client.get(
        f"/v1/organizations/{org}/billing", headers=as_user("alice")
    )

    assert result.status_code == 200
    data = result.json()["data"]
    assert data["plan"]["key"] == "pro"
    assert data["plan"]["max_members"] == 5
    assert data["subscription_status"] == "active"
    assert "cus_a" not in result.text


async def test_user_without_membership_cannot_see_another_organisation(
    client, engine, stripe
):
    org_a = await seed_org(
        engine,
        "orga",
        {"alice": "OWNER"},
        customer="cus_a",
        subscription="sub_a",
        tier="pro",
        status="active",
    )
    await seed_org(engine, "orgb", {"bob": "OWNER"})
    before = await org_row(engine, org_a)

    base = f"/v1/organizations/{org_a}/billing"
    results = [
        await client.get(base, headers=as_user("bob")),
        await client.get(f"{base}/invoices", headers=as_user("bob")),
        await client.get(f"{base}/payment-methods", headers=as_user("bob")),
        await client.post(
            f"{base}/checkout", json=checkout_body(), headers=as_user("bob")
        ),
        await client.post(
            f"{base}/portal", json={"return_url": RETURN_URL}, headers=as_user("bob")
        ),
        await client.post(
            f"{base}/plan",
            json={"plan": "pro", "interval": "yearly"},
            headers=as_user("bob"),
        ),
        await client.post(f"{base}/cancel", headers=as_user("bob")),
        await client.post(f"{base}/resume", headers=as_user("bob")),
    ]

    assert [r.status_code for r in results] == [404] * 8
    assert stripe.calls == []
    assert await org_row(engine, org_a) == before


async def test_staff_member_cannot_manage_billing(client, engine, stripe):
    org = await seed_org(
        engine,
        "acme",
        {"alice": "OWNER", "sam": "STAFF"},
        customer="cus_a",
        subscription="sub_a",
        tier="pro",
        status="active",
    )
    base = f"/v1/organizations/{org}/billing"

    results = [
        await client.get(base, headers=as_user("sam")),
        await client.post(
            f"{base}/checkout", json=checkout_body(), headers=as_user("sam")
        ),
        await client.post(f"{base}/cancel", headers=as_user("sam")),
    ]

    assert [r.status_code for r in results] == [403] * 3
    assert stripe.calls == []


async def test_admin_can_manage_billing(client, engine, stripe):
    org = await seed_org(
        engine,
        "acme",
        {"alice": "OWNER", "adam": "ADMIN"},
        customer="cus_1",
        subscription="sub_1",
        price=PRO_MONTHLY,
        tier="pro",
        status="active",
    )

    result = await client.post(
        f"/v1/organizations/{org}/billing/cancel", headers=as_user("adam")
    )

    assert result.status_code == 200
    assert stripe.last("set_cancel_at_period_end")["cancel"] is True


async def test_checkout_links_the_customer_but_does_not_write_subscription_state(
    client, engine, stripe
):
    org = await seed_org(engine, "acme", {"alice": "OWNER"})

    result = await client.post(
        f"/v1/organizations/{org}/billing/checkout",
        json=checkout_body(interval="yearly"),
        headers=as_user("alice", **{"Idempotency-Key": "e2e-key-0001"}),
    )

    assert result.status_code == 200
    assert result.json()["data"]["url"].startswith("https://checkout.stripe.test/")
    assert await org_row(engine, org) == ("cus_new", None, None, "free", "inactive")
    sent = stripe.last("create_checkout_session")
    assert sent["customer_id"] == "cus_new"
    assert sent["price_id"] == PRO_YEARLY
    assert sent["organization_id"] == org
    assert sent["idempotency_key"] == f"sc-checkout-{org}-e2e-key-0001"
    assert stripe.last("create_customer")["idempotency_key"] == f"sc-customer-{org}"


async def test_second_checkout_reuses_the_saved_customer(client, engine, stripe):
    org = await seed_org(engine, "acme", {"alice": "OWNER"})
    url = f"/v1/organizations/{org}/billing/checkout"

    await client.post(url, json=checkout_body(), headers=as_user("alice"))
    await client.post(url, json=checkout_body(), headers=as_user("alice"))

    assert stripe.names().count("create_customer") == 1
    assert stripe.names().count("create_checkout_session") == 2


async def test_concurrent_first_checkouts_keep_one_customer(client, engine, stripe):
    org = await seed_org(engine, "acme", {"alice": "OWNER"})
    url = f"/v1/organizations/{org}/billing/checkout"

    results = await asyncio.gather(
        *(
            client.post(url, json=checkout_body(), headers=as_user("alice"))
            for _ in range(4)
        )
    )

    assert [r.status_code for r in results] == [200] * 4
    assert (await org_row(engine, org))[0] == "cus_new"


async def test_checkout_with_a_foreign_return_url_is_rejected_before_stripe(
    client, engine, stripe
):
    org = await seed_org(engine, "acme", {"alice": "OWNER"})

    results = [
        await client.post(
            f"/v1/organizations/{org}/billing/checkout",
            json=checkout_body(success_url=bad),
            headers=as_user("alice"),
        )
        for bad in ("https://evil.example.net/x", "javascript:alert(1)", "//evil.test")
    ]

    assert [r.status_code for r in results] == [422] * 3
    assert stripe.calls == []
    assert (await org_row(engine, org))[0] is None


async def test_stripe_failure_is_generic_and_leaves_the_organisation_unchanged(
    client, engine, stripe
):
    org = await seed_org(engine, "acme", {"alice": "OWNER"})
    stripe.fail = True

    result = await client.post(
        f"/v1/organizations/{org}/billing/checkout",
        json=checkout_body(),
        headers=as_user("alice"),
    )

    assert result.status_code == 502
    assert result.json() == {"success": False, "error": "Billing provider unavailable"}
    assert await org_row(engine, org) == (None, None, None, "free", "inactive")


async def test_checkout_for_a_subscribed_organisation_is_409(client, engine, stripe):
    org = await seed_org(
        engine,
        "acme",
        {"alice": "OWNER"},
        customer="cus_1",
        subscription="sub_1",
        price=PRO_MONTHLY,
        tier="pro",
        status="active",
    )

    result = await client.post(
        f"/v1/organizations/{org}/billing/checkout",
        json=checkout_body(),
        headers=as_user("alice"),
    )

    assert result.status_code == 409
    assert stripe.calls == []


async def test_portal_plan_change_cancel_resume_invoices_and_methods(
    client, engine, stripe
):
    org = await seed_org(
        engine,
        "acme",
        {"alice": "OWNER"},
        customer="cus_1",
        subscription="sub_1",
        price=PRO_MONTHLY,
        tier="pro",
        status="active",
    )
    base = f"/v1/organizations/{org}/billing"
    before = await org_row(engine, org)

    portal = await client.post(
        f"{base}/portal", json={"return_url": RETURN_URL}, headers=as_user("alice")
    )
    change = await client.post(
        f"{base}/plan",
        json={"plan": "pro", "interval": "yearly"},
        headers=as_user("alice"),
    )
    cancel = await client.post(f"{base}/cancel", headers=as_user("alice"))
    resume = await client.post(f"{base}/resume", headers=as_user("alice"))
    invoices = await client.get(f"{base}/invoices", headers=as_user("alice"))
    methods = await client.get(f"{base}/payment-methods", headers=as_user("alice"))

    assert [
        r.status_code for r in (portal, change, cancel, resume, invoices, methods)
    ] == [200] * 6
    assert portal.json()["data"]["url"].startswith("https://portal.stripe.test/")
    assert stripe.last("change_subscription_price")["price_id"] == PRO_YEARLY
    assert invoices.json()["data"][0]["number"] == "A-0001"
    assert methods.json()["data"][0]["last4"] == "4242"
    # The webhook is the only writer of subscription state.
    assert await org_row(engine, org) == before


async def test_portal_with_a_foreign_return_url_is_rejected(client, engine, stripe):
    org = await seed_org(
        engine,
        "acme",
        {"alice": "OWNER"},
        customer="cus_1",
        tier="pro",
        status="active",
    )

    result = await client.post(
        f"/v1/organizations/{org}/billing/portal",
        json={"return_url": "https://evil.example.net/"},
        headers=as_user("alice"),
    )

    assert result.status_code == 422
    assert stripe.calls == []


async def test_webhook_after_checkout_upgrades_the_linked_organisation(
    client, engine, stripe
):
    org = await seed_org(engine, "acme", {"alice": "OWNER"})
    await client.post(
        f"/v1/organizations/{org}/billing/checkout",
        json=checkout_body(),
        headers=as_user("alice"),
    )
    event = {
        "id": f"evt_{new_id()}",
        "type": "customer.subscription.created",
        "created": T0,
        "data": {
            "object": {
                "id": "sub_new",
                "customer": "cus_new",
                "status": "active",
                "current_period_end": T0 + 2_592_000,
                "items": {"data": [{"price": {"id": PRO_MONTHLY}}]},
            }
        },
    }
    body = json.dumps(event).encode()
    stamp = int(time.time())
    signature = compute_signature(WEBHOOK_SECRET, stamp, body)

    delivered = await client.post(
        "/webhooks/stripe",
        content=body,
        headers={"Stripe-Signature": f"t={stamp},v1={signature}"},
    )

    assert delivered.status_code == 200
    assert await org_row(engine, org) == (
        "cus_new",
        "sub_new",
        PRO_MONTHLY,
        "pro",
        "active",
    )
    state = await client.get(
        f"/v1/organizations/{org}/billing", headers=as_user("alice")
    )
    assert state.json()["data"]["plan"]["key"] == "pro"


async def deliver(client, event_id: str, created: int, **subscription: Any) -> int:
    """Post a signed customer.subscription.updated event through the real webhook route."""
    event = {
        "id": event_id,
        "type": "customer.subscription.updated",
        "created": created,
        "data": {
            "object": {
                "id": "sub_1",
                "customer": "cus_1",
                "status": "active",
                "current_period_end": T0 + 2_592_000,
                "items": {"data": [{"price": {"id": PRO_YEARLY}}]},
                **subscription,
            }
        },
    }
    body = json.dumps(event).encode()
    stamp = int(time.time())
    signature = compute_signature(WEBHOOK_SECRET, stamp, body)
    result = await client.post(
        "/webhooks/stripe",
        content=body,
        headers={"Stripe-Signature": f"t={stamp},v1={signature}"},
    )
    return result.status_code


async def seed_subscribed(engine, members, **columns) -> str:
    return await seed_org(
        engine,
        "acme",
        members,
        customer="cus_1",
        subscription="sub_1",
        price=PRO_MONTHLY,
        tier="pro",
        status="active",
        **columns,
    )


async def test_organisation_subscribed_before_the_migration_reads_as_renewing(
    client, engine
):
    org = await seed_subscribed(engine, {"alice": "OWNER"})

    result = await client.get(
        f"/v1/organizations/{org}/billing", headers=as_user("alice")
    )

    data = result.json()["data"]
    assert (data["cancel_at_period_end"], data["interval"]) == (False, "monthly")


async def test_webhook_stores_the_flag_and_billing_reports_it_with_the_interval(
    client, engine
):
    org = await seed_subscribed(engine, {"alice": "OWNER"})
    url = f"/v1/organizations/{org}/billing"

    scheduled = await deliver(client, "evt_cancel", T0, cancel_at_period_end=True)
    after_cancel = (await client.get(url, headers=as_user("alice"))).json()["data"]
    resumed = await deliver(client, "evt_resume", T0 + 10, cancel_at_period_end=False)
    after_resume = (await client.get(url, headers=as_user("alice"))).json()["data"]
    dated = await deliver(client, "evt_dated", T0 + 20, cancel_at=T0 + 99_999)
    after_dated = (await client.get(url, headers=as_user("alice"))).json()["data"]
    stale = await deliver(client, "evt_stale", T0 + 5, cancel_at_period_end=True)
    after_stale = (await client.get(url, headers=as_user("alice"))).json()["data"]

    assert (scheduled, resumed, dated, stale) == (200, 200, 200, 200)
    assert after_cancel["cancel_at_period_end"] is True
    assert after_cancel["interval"] == "yearly"
    assert after_resume["cancel_at_period_end"] is False
    assert after_dated["cancel_at_period_end"] is True
    assert after_stale["cancel_at_period_end"] is True  # older event ignored


async def test_interval_is_null_when_the_stored_price_is_not_in_the_catalogue(
    client, engine
):
    org = await seed_org(
        engine,
        "acme",
        {"alice": "OWNER"},
        customer="cus_1",
        subscription="sub_1",
        price="price_rotated_away",
        tier="pro",
        status="active",
    )

    result = await client.get(
        f"/v1/organizations/{org}/billing", headers=as_user("alice")
    )

    assert result.json()["data"]["interval"] is None


async def test_payment_methods_report_the_default_card(client, engine, stripe):
    org = await seed_subscribed(engine, {"alice": "OWNER"})

    result = await client.get(
        f"/v1/organizations/{org}/billing/payment-methods", headers=as_user("alice")
    )

    assert [(m["id"], m["is_default"]) for m in result.json()["data"]] == [
        ("pm_1", True)
    ]


async def test_owner_and_admin_set_the_default_card_and_delete_a_spare_one(
    client, engine, stripe
):
    org = await seed_subscribed(engine, {"alice": "OWNER", "adam": "ADMIN"})
    stripe.payment_method_owners["pm_2"] = "cus_1"
    base = f"/v1/organizations/{org}/billing/payment-methods"
    key = {"Idempotency-Key": "e2e-pm-0001"}

    default = await client.post(f"{base}/pm_2/default", headers=as_user("adam", **key))
    stripe.default_payment_method = "pm_2"
    removed = await client.delete(f"{base}/pm_1", headers=as_user("alice", **key))

    assert (default.status_code, removed.status_code) == (200, 200)
    assert stripe.last("set_default_payment_method") == {
        "customer_id": "cus_1",
        "payment_method_id": "pm_2",
        "idempotency_key": f"sc-pm-default-{org}-e2e-pm-0001",
    }
    assert stripe.last("detach_payment_method")["payment_method_id"] == "pm_1"


async def test_deleting_the_default_card_while_subscribed_is_409(
    client, engine, stripe
):
    org = await seed_subscribed(engine, {"alice": "OWNER"})

    result = await client.delete(
        f"/v1/organizations/{org}/billing/payment-methods/pm_1",
        headers=as_user("alice", **{"Idempotency-Key": "e2e-pm-0002"}),
    )

    assert result.status_code == 409
    assert "detach_payment_method" not in stripe.names()


async def test_default_card_can_be_deleted_after_the_subscription_ended(
    client, engine, stripe
):
    org = await seed_org(
        engine, "acme", {"alice": "OWNER"}, customer="cus_1", status="canceled"
    )

    result = await client.delete(
        f"/v1/organizations/{org}/billing/payment-methods/pm_1",
        headers=as_user("alice", **{"Idempotency-Key": "e2e-pm-0003"}),
    )

    assert result.status_code == 200


async def test_another_organisations_card_is_a_404_identical_to_an_unknown_one(
    client, engine, stripe
):
    org_a = await seed_subscribed(engine, {"alice": "OWNER"})
    await seed_org(engine, "orgb", {"bob": "OWNER"}, customer="cus_b")
    stripe.payment_method_owners["pm_b"] = "cus_b"
    base = f"/v1/organizations/{org_a}/billing/payment-methods"
    key = as_user("alice", **{"Idempotency-Key": "e2e-pm-0004"})

    foreign = await client.delete(f"{base}/pm_b", headers=key)
    unknown = await client.delete(f"{base}/pm_missing", headers=key)
    foreign_default = await client.post(f"{base}/pm_b/default", headers=key)
    unknown_default = await client.post(f"{base}/pm_missing/default", headers=key)

    assert foreign.status_code == unknown.status_code == 404
    assert foreign.content == unknown.content
    assert foreign_default.content == unknown_default.content
    assert foreign_default.status_code == 404
    assert "detach_payment_method" not in stripe.names()
    assert "set_default_payment_method" not in stripe.names()


async def test_payment_method_changes_require_membership_role_and_key(
    client, engine, stripe
):
    org = await seed_subscribed(engine, {"alice": "OWNER", "sam": "STAFF"})
    await seed_org(engine, "orgb", {"bob": "OWNER"})
    base = f"/v1/organizations/{org}/billing/payment-methods/pm_1"
    key = {"Idempotency-Key": "e2e-pm-0005"}

    staff = await client.delete(base, headers=as_user("sam", **key))
    stranger = await client.post(f"{base}/default", headers=as_user("bob", **key))
    no_key = await client.delete(base, headers=as_user("alice"))

    assert (staff.status_code, stranger.status_code, no_key.status_code) == (
        403,
        404,
        422,
    )
    assert stripe.calls == []


# --- audit trail -----------------------------------------------------


async def audit_rows(engine: AsyncEngine, org_id: str) -> list[Any]:
    async with engine.connect() as connection:
        result = await connection.execute(
            text(
                "SELECT action, resource, resource_id, user_id, metadata "
                "FROM audit_logs WHERE organization_id = :org ORDER BY created_at"
            ),
            {"org": org_id},
        )
        return list(result.all())


async def seed_audit_subscribed(engine: AsyncEngine) -> str:
    return await seed_org(
        engine,
        "acme",
        {"alice": "OWNER"},
        customer="cus_1",
        subscription="sub_1",
        price=PRO_MONTHLY,
        tier="pro",
        status="active",
    )


@pytest.mark.parametrize(
    ("method", "path", "body", "action"),
    [
        ("post", "plan", {"plan": "pro", "interval": "yearly"}, "billing.change_plan"),
        ("post", "cancel", None, "billing.cancel"),
        ("post", "resume", None, "billing.resume"),
        ("post", "checkout", "checkout", "billing.checkout"),
    ],
)
async def test_billing_change_writes_exactly_one_audit_entry(
    client, engine, stripe, method, path, body, action
):
    org = (
        await seed_org(engine, "acme", {"alice": "OWNER"})
        if path == "checkout"
        else await seed_audit_subscribed(engine)
    )

    result = await client.request(
        method.upper(),
        f"/v1/organizations/{org}/billing/{path}",
        json=checkout_body() if body == "checkout" else body,
        headers=as_user("alice"),
    )

    assert result.status_code == 200, result.text
    rows = await audit_rows(engine, org)
    assert [row.action for row in rows] == [action]
    assert rows[0].resource == "organization"
    assert rows[0].resource_id == org
    assert rows[0].user_id is not None
    assert "success_url" not in json.dumps(rows[0].metadata)


async def test_payment_method_changes_are_audited_without_card_data(
    client, engine, stripe
):
    org = await seed_audit_subscribed(engine)
    stripe.payment_method_owners["pm_2"] = "cus_1"

    headers = {**as_user("alice"), "Idempotency-Key": "k-12345678"}
    default = await client.post(
        f"/v1/organizations/{org}/billing/payment-methods/pm_2/default",
        headers=headers,
    )
    deleted = await client.delete(
        f"/v1/organizations/{org}/billing/payment-methods/pm_2",
        headers={**headers, "Idempotency-Key": "k-87654321"},
    )

    assert (default.status_code, deleted.status_code) == (200, 200)
    rows = await audit_rows(engine, org)
    assert [row.action for row in rows] == [
        "billing.set_default_payment_method",
        "billing.delete_payment_method",
    ]
    blob = json.dumps([row.metadata for row in rows])
    assert "4242" not in blob
    assert "cus_1" not in blob


async def test_refused_and_failed_billing_changes_write_no_audit_entry(
    client, engine, stripe
):
    org = await seed_audit_subscribed(engine)
    stripe.fail = True

    failed = await client.post(
        f"/v1/organizations/{org}/billing/cancel", headers=as_user("alice")
    )
    stripe.fail = False
    refused = await client.post(
        f"/v1/organizations/{org}/billing/plan",
        json={"plan": "free", "interval": "monthly"},
        headers=as_user("alice"),
    )

    assert failed.status_code == 502
    assert refused.status_code == 422
    conflict = await client.post(
        f"/v1/organizations/{org}/billing/checkout",
        json=checkout_body(),
        headers=as_user("alice"),
    )
    assert conflict.status_code == 409
    assert await audit_rows(engine, org) == []


async def test_billing_audit_row_stores_forwarded_ip_user_agent_and_request_id(
    client, engine, stripe
):
    org = await seed_audit_subscribed(engine)

    trusted = await client.post(
        f"/v1/organizations/{org}/billing/cancel",
        headers={
            **as_user("alice"),
            "X-Client-IP": "203.0.113.7",
            "User-Agent": "KAN193-agent/1.0",
        },
    )
    spoofed = await client.post(
        f"/v1/organizations/{org}/billing/resume",
        headers={**as_user("alice"), "X-Client-IP": "not-an-ip; DROP"},
    )

    assert trusted.status_code == 200 and spoofed.status_code == 200
    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                text(
                    "SELECT action, ip_address, user_agent, metadata "
                    "FROM audit_logs WHERE organization_id = :org ORDER BY created_at"
                ),
                {"org": org},
            )
        ).all()
    cancel, resume = rows
    assert (cancel.ip_address, cancel.user_agent) == ("203.0.113.7", "KAN193-agent/1.0")
    assert cancel.metadata["request_id"] == trusted.headers["x-request-id"]
    assert resume.ip_address == "127.0.0.1"  # malformed forwarded value: the peer
    assert "DROP" not in json.dumps([tuple(r) for r in rows])
