"""End to end: the editor's side of a partner submission (FREEJ-58): the marker, the pending update, apply and dismiss."""

from typing import Any

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

import tests.api.partner.submissions_support as sub
import tests.catalogue_e2e_support as support
from tests.api.partner.submissions_support import (
    OFFERS,
    PARTNER_OFFERS,
    SITE,
    make_category,
    make_merchant,
    offer_body,
    partner_call,
    put_offer,
)
from tests.catalogue_e2e_support import approve, as_user, audit_rows, rows, seed_admin

pytestmark = support.requires_database
database_url, engine, client = support.database_url, support.engine, support.client
partner_role, partner_world, admin = sub.partner_role, sub.partner_world, sub.admin


async def waiting_update(
    client: AsyncClient, admin: dict[str, str]
) -> tuple[str, sub.Merchant, str, str]:
    """A draft an editor edited, and a retailer update waiting for them; returns (category, merchant, offer url, ref)."""
    category = await make_category(client, admin)
    merchant = await make_merchant(client, admin)
    await put_offer(merchant.key, "sku-1", offer_body(category))
    url = f"{OFFERS}/{await sub.offer_id_of(client, admin, 'Free')}"
    await client.patch(url, json={"title": "Editor title"}, headers=admin)
    return category, merchant, url, "sku-1"


async def read(client: AsyncClient, admin: dict[str, str], url: str) -> dict[str, Any]:
    return dict((await client.get(url, headers=admin)).json()["data"])


async def send_update(
    client: AsyncClient, admin: dict[str, str], url: str, step: str, **overrides: Any
) -> Any:
    data = await read(client, admin, url)
    body = {
        "review_token": data["review_token"],
        "pending_hash": data["partner_submission"]["pending"]["hash"],
        **overrides,
    }
    return await client.post(f"{url}/{step}", json=body, headers=admin)


async def test_the_offer_says_who_submitted_it_and_what_is_waiting(
    client: AsyncClient, admin: dict[str, str], partner_world: None
) -> None:
    category, merchant, url, _ = await waiting_update(client, admin)
    before = await read(client, admin, url)
    await put_offer(merchant.key, "sku-1", offer_body(category, title="Retailer title"))

    after = await read(client, admin, url)

    assert before["partner_submission"]["pending"] is None
    assert before["import_provider"] == "partner"
    partner = after["partner_submission"]
    assert (partner["ref"], partner["merchant_id"], partner["merchant_name"]) == (
        "sku-1",
        merchant.id,
        "Shop",
    )
    assert partner["pending"]["payload"]["title"] == "Retailer title"
    assert after["source_changed_at"] is not None
    assert after["title"] == "Editor title"


async def test_a_hand_made_offer_has_no_partner_block(
    client: AsyncClient, admin: dict[str, str]
) -> None:
    category = await client.post(
        "/v1/admin/categories", json=support.CATEGORY_BODY, headers=admin
    )
    merchant = await client.post(
        "/v1/admin/merchants", json={"name": "Acme"}, headers=admin
    )
    created = await client.post(
        OFFERS,
        json=support.offer_body(
            category.json()["data"]["id"], merchant.json()["data"]["id"]
        ),
        headers=admin,
    )

    assert created.json()["data"]["partner_submission"] is None


async def test_applying_writes_the_proposal_through_the_normal_edit_and_clears_the_queue_entry(
    client: AsyncClient, engine: AsyncEngine, admin: dict[str, str], partner_world: None
) -> None:
    category, merchant, url, ref = await waiting_update(client, admin)
    await put_offer(
        merchant.key,
        ref,
        offer_body(
            category,
            title="Retailer title",
            url=f"{SITE}/new-page",
            eligibility="Everyone",
        ),
    )

    applied = await send_update(client, admin, url, "apply-partner-update")

    data = applied.json()["data"]
    assert applied.status_code == 200
    assert (data["title"], data["eligibility"]) == ("Retailer title", "Everyone")
    assert data["links"][0]["destination_url"] == f"{SITE}/new-page"
    assert data["partner_submission"]["pending"] is None
    assert data["source_changed_at"] is None
    assert (data["status"], data["last_verified_at"], data["featured"]) == (
        "DRAFT",
        None,
        False,
    )
    actions = [r.action for r in await audit_rows(engine)]
    assert "admin.offers.partner_update_applied" in actions
    assert "admin.offers.update" in actions
    assert "admin.offer_links.update" in actions
    view = await partner_call(merchant.key, "GET", f"{PARTNER_OFFERS}/{ref}")
    assert view.json()["data"]["update_pending"] is False
    again = await put_offer(
        merchant.key,
        ref,
        offer_body(
            category,
            title="Retailer title",
            url=f"{SITE}/new-page",
            eligibility="Everyone",
        ),
    )
    assert again.json()["data"]["outcome"] == "unchanged"


async def test_applying_never_changes_the_status_the_verification_or_the_basis_of_a_live_offer(
    client: AsyncClient, admin: dict[str, str], partner_world: None
) -> None:
    category, merchant, url, ref = await waiting_update(client, admin)
    await approve(client, admin, url, "verify")
    assert (
        await approve(client, admin, url, "publish", commercial_basis="EDITORIAL")
    ).status_code == 200
    before = await read(client, admin, url)
    await put_offer(merchant.key, ref, offer_body(category, title="Retailer title"))

    applied = await send_update(client, admin, url, "apply-partner-update")

    data = applied.json()["data"]
    assert applied.status_code == 200
    assert data["title"] == "Retailer title"
    for field in ("status", "last_verified_at", "commercial_basis", "featured"):
        assert data[field] == before[field], field


async def test_applying_to_a_live_offer_needs_the_publish_permission(
    client: AsyncClient, engine: AsyncEngine, admin: dict[str, str], partner_world: None
) -> None:
    category, merchant, url, ref = await waiting_update(client, admin)
    await approve(client, admin, url, "verify")
    await approve(client, admin, url, "publish", commercial_basis="EDITORIAL")
    await put_offer(merchant.key, ref, offer_body(category, title="Retailer title"))
    await seed_admin(engine, "writer", ["offers.read", "offers.write"])
    writer = as_user("writer")
    data = await read(client, writer, url)

    refused = await client.post(
        f"{url}/apply-partner-update",
        json={
            "review_token": data["review_token"],
            "pending_hash": data["partner_submission"]["pending"]["hash"],
        },
        headers=writer,
    )

    assert refused.status_code == 403
    assert (await read(client, admin, url))["title"] == "Editor title"


async def test_a_stale_review_token_or_a_newer_proposal_is_a_409_and_nothing_is_written(
    client: AsyncClient, admin: dict[str, str], partner_world: None
) -> None:
    category, merchant, url, ref = await waiting_update(client, admin)
    await put_offer(merchant.key, ref, offer_body(category, title="First proposal"))
    shown = await read(client, admin, url)
    await client.patch(url, json={"summary": "Edited meanwhile"}, headers=admin)
    stale_token = await client.post(
        f"{url}/apply-partner-update",
        json={
            "review_token": shown["review_token"],
            "pending_hash": shown["partner_submission"]["pending"]["hash"],
        },
        headers=admin,
    )
    fresh = await read(client, admin, url)
    await put_offer(merchant.key, ref, offer_body(category, title="Second proposal"))
    stale_hash = await client.post(
        f"{url}/apply-partner-update",
        json={
            "review_token": (await read(client, admin, url))["review_token"],
            "pending_hash": fresh["partner_submission"]["pending"]["hash"],
        },
        headers=admin,
    )

    assert (
        stale_token.status_code == 409 and stale_token.json()["code"] == "offer_changed"
    )
    assert (
        stale_hash.status_code == 409 and stale_hash.json()["code"] == "offer_changed"
    )
    assert (await read(client, admin, url))["title"] == "Editor title"


async def test_a_link_that_no_longer_matches_the_merchants_site_blocks_the_whole_apply(
    client: AsyncClient, engine: AsyncEngine, admin: dict[str, str], partner_world: None
) -> None:
    category, merchant, url, ref = await waiting_update(client, admin)
    await put_offer(
        merchant.key,
        ref,
        offer_body(category, title="Retailer title", url=f"{SITE}/moved"),
    )
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE merchants SET website = 'https://elsewhere.example.org'")
        )

    refused = await send_update(client, admin, url, "apply-partner-update")

    assert refused.status_code == 422
    data = await read(client, admin, url)
    assert (
        data["title"] == "Editor title"
    )  # the content edit rolled back with the link refusal
    assert data["partner_submission"]["pending"] is not None


async def test_dismissing_clears_the_proposal_and_the_marker_and_changes_nothing_else(
    client: AsyncClient, engine: AsyncEngine, admin: dict[str, str], partner_world: None
) -> None:
    category, merchant, url, ref = await waiting_update(client, admin)
    await put_offer(merchant.key, ref, offer_body(category, title="Retailer title"))
    before = await read(client, admin, url)

    dismissed = await send_update(client, admin, url, "dismiss-partner-update")

    data = dismissed.json()["data"]
    assert dismissed.status_code == 200
    assert (
        data["partner_submission"]["pending"] is None
        and data["source_changed_at"] is None
    )
    for field in ("title", "summary", "status", "links", "commercial_basis"):
        assert data[field] == before[field], field
    assert "admin.offers.partner_update_dismissed" in [
        r.action for r in await audit_rows(engine)
    ]
    view = await partner_call(merchant.key, "GET", f"{PARTNER_OFFERS}/{ref}")
    assert view.json()["data"]["update_pending"] is False
    same = await put_offer(
        merchant.key, ref, offer_body(category, title="Retailer title")
    )
    assert (
        same.json()["data"]["outcome"] == "unchanged"
    )  # a dismissed proposal is not re-queued by a sync job
    changed = await put_offer(
        merchant.key, ref, offer_body(category, title="Retailer title 2")
    )
    assert changed.json()["data"]["outcome"] == "pending_update"


async def test_there_is_nothing_to_apply_or_dismiss_without_a_proposal_or_for_a_hand_made_offer(
    client: AsyncClient, admin: dict[str, str], partner_world: None
) -> None:
    _, _, url, _ = await waiting_update(client, admin)
    data = await read(client, admin, url)
    body = {"review_token": data["review_token"], "pending_hash": "a" * 64}

    apply = await client.post(f"{url}/apply-partner-update", json=body, headers=admin)
    dismiss = await client.post(
        f"{url}/dismiss-partner-update", json=body, headers=admin
    )

    assert (apply.status_code, dismiss.status_code) == (409, 409)
    assert apply.json()["code"] == "illegal_transition"


async def test_the_generic_dismissal_of_a_source_change_does_not_clear_a_partner_proposal(
    client: AsyncClient, admin: dict[str, str], partner_world: None
) -> None:
    category, merchant, url, ref = await waiting_update(client, admin)
    await put_offer(merchant.key, ref, offer_body(category, title="Retailer title"))
    data = await read(client, admin, url)

    refused = await client.post(
        f"{url}/dismiss-source-change",
        json={
            "review_token": data["review_token"],
            "source_changed_at": data["source_changed_at"],
        },
        headers=admin,
    )

    assert refused.status_code == 409
    assert (await read(client, admin, url))["partner_submission"]["pending"] is not None


async def test_an_offer_the_retailer_ended_stays_ended_when_an_old_proposal_is_applied(
    client: AsyncClient, engine: AsyncEngine, admin: dict[str, str], partner_world: None
) -> None:
    category, merchant, url, ref = await waiting_update(client, admin)
    await put_offer(
        merchant.key, ref, offer_body(category, title="Retailer title")
    )  # end_at null
    await partner_call(merchant.key, "POST", f"{PARTNER_OFFERS}/{ref}/end")

    applied = await send_update(client, admin, url, "apply-partner-update")

    assert applied.status_code == 200
    assert applied.json()["data"]["title"] == "Retailer title"
    assert applied.json()["data"]["end_at"] is not None
    (row,) = await rows(engine, "SELECT end_at FROM offers")
    assert row.end_at is not None


async def test_an_applied_draft_stays_edited_so_a_later_update_waits_again(
    client: AsyncClient, admin: dict[str, str], partner_world: None
) -> None:
    category, merchant, url, ref = await waiting_update(client, admin)
    await put_offer(merchant.key, ref, offer_body(category, title="Retailer title"))
    await send_update(client, admin, url, "apply-partner-update")

    later = await put_offer(
        merchant.key, ref, offer_body(category, title="Retailer title 3")
    )

    assert later.json()["data"]["outcome"] == "pending_update"
