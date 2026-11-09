"""End to end: publish and category disable / merchant deactivate cannot interleave.

Publish reads the category and merchant under a share lock (FOR SHARE) and the disabling
write takes the row lock (FOR UPDATE), so one waits for the other's commit. Each test pauses
one transaction just before its commit (inside the audit call) and shows the other waits.
"""

import asyncio
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

import tests.catalogue_e2e_support as support
from app.core.permissions import AdminContext
from app.repositories.admin_categories import AdminCategoryRepository
from app.repositories.admin_merchants import AdminMerchantRepository
from app.repositories.admin_offers import AdminOfferRepository
from app.services.admin_catalogue_errors import OfferChanged, PublishBlocked
from app.services.admin_categories import AdminCategoryService
from app.services.admin_merchants import AdminMerchantService
from app.services.admin_offers import AdminOfferService
from app.services.offer_lifecycle import OfferAction
from tests.catalogue_e2e_support import EDITOR, as_user, offer_body, rows, seed_admin

pytestmark = support.requires_database
database_url, engine, client = support.database_url, support.engine, support.client

ACTOR = AdminContext("admin1", "admin", frozenset(EDITOR))
WAIT = 15
SETTLE = 0.7


class GatedAuditor:
    """Pauses inside ``record`` (after the write, before the commit) until released."""

    def __init__(self, gated: bool) -> None:
        self.gated = gated
        self.reached = asyncio.Event()
        self.release = asyncio.Event()

    async def record(self, admin: AdminContext, **fields: Any) -> None:
        if self.gated:
            self.reached.set()
            await asyncio.wait_for(self.release.wait(), WAIT)


@pytest.fixture
async def world(engine: AsyncEngine, client):
    await seed_admin(engine, "editor", EDITOR)
    headers = as_user("editor")
    category_id, merchant_id = await support.create_references(client, headers)
    created = await client.post(
        "/v1/admin/offers", json=offer_body(category_id, merchant_id), headers=headers
    )
    offer_id = created.json()["data"]["id"]
    await support.approve(client, headers, f"/v1/admin/offers/{offer_id}", "verify")
    return (
        async_sessionmaker(engine, expire_on_commit=False),
        offer_id,
        category_id,
        merchant_id,
    )


def publisher(factory, auditor):
    session = factory()
    return session, AdminOfferService(AdminOfferRepository(session), session, auditor)


def disabler(factory, kind, target_id, auditor):
    session = factory()
    if kind == "category":
        service = AdminCategoryService(
            AdminCategoryRepository(session), session, auditor
        )
        return session, lambda: service.set_enabled(ACTOR, target_id, False)
    merchants = AdminMerchantService(AdminMerchantRepository(session), session, auditor)
    return session, lambda: merchants.set_active(ACTOR, target_id, False)


@pytest.mark.parametrize("kind", ["category", "merchant"])
async def test_a_publish_in_flight_makes_the_disable_wait_for_its_commit(
    engine, world, kind
):
    factory, offer_id, category_id, merchant_id = world
    target = category_id if kind == "category" else merchant_id
    publishing, off = GatedAuditor(True), GatedAuditor(False)
    session_a, service_a = publisher(factory, publishing)
    session_b, disable = disabler(factory, kind, target, off)
    async with session_a, session_b:
        token = (await service_a.get_offer(offer_id)).review_token
        publish_task = asyncio.create_task(
            service_a.transition(
                ACTOR, offer_id, OfferAction.PUBLISH, review_token=token
            )
        )
        await asyncio.wait_for(publishing.reached.wait(), WAIT)
        disable_task = asyncio.create_task(disable())
        await asyncio.sleep(SETTLE)

        assert not disable_task.done(), "disable ran while a publish was uncommitted"
        publishing.release.set()
        await asyncio.wait_for(publish_task, WAIT)
        await asyncio.wait_for(disable_task, WAIT)

    status = await rows(engine, "SELECT status FROM offers")
    flags = await rows(engine, "SELECT enabled FROM categories")
    assert status == [("PUBLISHED",)] and flags == [(kind != "category",)]


@pytest.mark.parametrize("kind", ["category", "merchant"])
async def test_a_disable_in_flight_makes_the_publish_wait_and_then_refuse(
    engine, world, kind
):
    factory, offer_id, category_id, merchant_id = world
    target = category_id if kind == "category" else merchant_id
    disabling, off = GatedAuditor(True), GatedAuditor(False)
    session_b, disable = disabler(factory, kind, target, disabling)
    session_a, service_a = publisher(factory, off)
    async with session_a, session_b:
        token = (await service_a.get_offer(offer_id)).review_token
        disable_task = asyncio.create_task(disable())
        await asyncio.wait_for(disabling.reached.wait(), WAIT)
        publish_task = asyncio.create_task(
            service_a.transition(
                ACTOR, offer_id, OfferAction.PUBLISH, review_token=token
            )
        )
        await asyncio.sleep(SETTLE)

        assert not publish_task.done(), "publish ran while a disable was uncommitted"
        disabling.release.set()
        await asyncio.wait_for(disable_task, WAIT)
        with pytest.raises(PublishBlocked) as blocked:
            await asyncio.wait_for(publish_task, WAIT)

    expected = "category_disabled" if kind == "category" else "merchant_inactive"
    assert blocked.value.reasons == [expected]
    assert await rows(engine, "SELECT status FROM offers") == [("DRAFT",)]


@pytest.mark.parametrize("write", ["offer", "link"])
async def test_a_write_in_flight_makes_publish_wait_and_then_refuse_as_changed(
    engine, world, write
):
    """The token is compared under the row lock: an uncommitted edit cannot slip past the check."""
    factory, offer_id, _category_id, _merchant_id = world
    editing, off = GatedAuditor(True), GatedAuditor(False)
    session_a, service_a = publisher(factory, off)
    session_b, service_b = publisher(factory, editing)
    async with session_a, session_b:
        token = (await service_a.get_offer(offer_id)).review_token
        if write == "offer":
            edit = service_b.update_offer(ACTOR, offer_id, {"title": "Edited"})
        else:
            edit = service_b.create_link(
                ACTOR,
                offer_id,
                {
                    "provider": "awin",
                    "destination_url": "https://www.awin1.com/c?x=1",
                    "tracking_id": None,
                    "active": True,
                },
            )
        edit_task = asyncio.create_task(edit)
        await asyncio.wait_for(editing.reached.wait(), WAIT)
        publish_task = asyncio.create_task(
            service_a.transition(
                ACTOR, offer_id, OfferAction.PUBLISH, review_token=token
            )
        )
        await asyncio.sleep(SETTLE)

        assert not publish_task.done(), "publish ran while an edit was uncommitted"
        editing.release.set()
        await asyncio.wait_for(edit_task, WAIT)
        with pytest.raises(OfferChanged):
            await asyncio.wait_for(publish_task, WAIT)

    assert await rows(engine, "SELECT status FROM offers") == [("DRAFT",)]
    published = await rows(
        engine, "SELECT 1 FROM audit_logs WHERE action = 'admin.offers.publish'"
    )
    assert published == []
