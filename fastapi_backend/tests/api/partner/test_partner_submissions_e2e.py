"""End to end on Postgres: a retailer submits offers through the partner API and they only ever become drafts (FREEJ-58).

Real database, the real restricted role, the real app. The admin side (an editor reviewing, verifying and publishing)
is the normal internal API, so each rule is shown against the system it has to coexist with.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
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
    put_many,
    put_offer,
)
from tests.catalogue_e2e_support import approve, rows

pytestmark = support.requires_database
database_url, engine, client = support.database_url, support.engine, support.client
partner_role, partner_world, admin = sub.partner_role, sub.partner_world, sub.admin


async def offer_rows(engine: AsyncEngine) -> list[Any]:
    return await rows(
        engine,
        "SELECT id, title, status, featured, last_verified_at, commercial_basis, end_at, start_at, description, "
        "steps, summary FROM offers ORDER BY created_at",
    )


async def setup(client: AsyncClient, admin: dict[str, str]) -> tuple[str, sub.Merchant]:
    category = await make_category(client, admin)
    return category, await make_merchant(client, admin)


# ---------------------------------------------------------------------------------------------- creating


async def test_a_new_ref_creates_an_unverified_unfeatured_draft_with_one_direct_link(
    client: AsyncClient, engine: AsyncEngine, admin: dict[str, str], partner_world: None
) -> None:
    category, merchant = await setup(client, admin)

    response = await put_offer(merchant.key, "sku-1", offer_body(category))

    assert response.status == 201
    data = response.json()["data"]
    assert (data["ref"], data["status"], data["outcome"]) == (
        "sku-1",
        "pending_review",
        "created",
    )
    assert data["public_url"] is None and data["update_pending"] is False
    (offer,) = await offer_rows(engine)
    assert (offer.status, offer.featured, offer.last_verified_at) == (
        "DRAFT",
        False,
        None,
    )
    assert (
        offer.commercial_basis == "SPONSORED"
    )  # the placeholder, the most cautious label; an editor states the real basis at publish
    (link,) = await rows(
        engine, "SELECT provider, destination_url, active FROM affiliate_links"
    )
    assert tuple(link) == ("direct", f"{SITE}/free-sample", True)
    (imported,) = await rows(
        engine, "SELECT provider, source_changed_at FROM offer_imports"
    )
    assert tuple(imported) == ("partner", None)


async def test_a_new_submission_is_in_the_review_queue_with_no_change_to_the_query(
    client: AsyncClient, admin: dict[str, str], partner_world: None
) -> None:
    category, merchant = await setup(client, admin)

    await put_offer(merchant.key, "sku-1", offer_body(category))
    queue = await client.get(f"{OFFERS}?needs_review=true", headers=admin)

    (queued,) = queue.json()["data"]["offers"]
    assert queued["title"] == "Free sample box"
    assert queued["import_provider"] == "partner"
    assert queued["partner_ref"] == "sku-1"
    assert queued["status"] == "DRAFT"


async def test_the_audit_row_names_the_key_and_the_action_and_holds_no_copy(
    client: AsyncClient, engine: AsyncEngine, admin: dict[str, str], partner_world: None
) -> None:
    category, merchant = await setup(client, admin)

    await put_offer(
        merchant.key, "sku-1", offer_body(category, title="Secret title text")
    )
    (audit,) = await rows(
        engine,
        "SELECT user_id, action, resource, metadata FROM audit_logs WHERE action LIKE 'partner.%'",
    )

    assert audit.user_id is None
    assert audit.action == "partner.offers.submit"
    assert audit.resource == "offers"
    assert audit.metadata["key_id"] == merchant.key_id
    assert audit.metadata["merchant_id"] == merchant.id
    assert audit.metadata["ref"] == "sku-1"
    assert "Secret title text" not in str(audit.metadata)
    assert merchant.key not in str(audit.metadata)


# ------------------------------------------------------------------------------------------ idempotency


async def test_the_same_put_twice_is_one_submission_and_says_it_changed_nothing(
    client: AsyncClient, engine: AsyncEngine, admin: dict[str, str], partner_world: None
) -> None:
    category, merchant = await setup(client, admin)
    body = offer_body(category)

    first = await put_offer(merchant.key, "sku-1", body)
    second = await put_offer(merchant.key, "sku-1", body)

    assert (first.status, second.status) == (201, 200)
    assert second.json()["data"]["outcome"] == "unchanged"
    assert len(await offer_rows(engine)) == 1
    (count,) = await rows(
        engine, "SELECT count(*) FROM audit_logs WHERE action LIKE 'partner.%'"
    )
    assert count[0] == 1  # no write, no audit row


async def test_concurrent_first_puts_on_one_ref_create_one_submission_and_one_offer(
    client: AsyncClient, engine: AsyncEngine, admin: dict[str, str], partner_world: None
) -> None:
    category, merchant = await setup(client, admin)
    body = offer_body(category)

    responses = await put_many(merchant.key, [("race", body)] * 6)

    outcomes = sorted(r.json()["data"]["outcome"] for r in responses)
    assert outcomes == ["created"] + ["unchanged"] * 5
    assert len(await offer_rows(engine)) == 1
    (submissions,) = await rows(engine, "SELECT count(*) FROM partner_submissions")
    assert submissions[0] == 1


async def test_concurrent_first_puts_with_different_content_end_as_one_offer(
    client: AsyncClient, engine: AsyncEngine, admin: dict[str, str], partner_world: None
) -> None:
    category, merchant = await setup(client, admin)
    jobs = [("race", offer_body(category, title=f"Variant {n}")) for n in range(5)]

    responses = await put_many(merchant.key, jobs)

    assert {r.status for r in responses} <= {200, 201}
    assert sum(r.status == 201 for r in responses) == 1
    assert len(await offer_rows(engine)) == 1


# ----------------------------------------------------------------------------- refresh versus pending


async def test_an_update_of_an_untouched_draft_refreshes_it(
    client: AsyncClient, engine: AsyncEngine, admin: dict[str, str], partner_world: None
) -> None:
    category, merchant = await setup(client, admin)
    await put_offer(merchant.key, "sku-1", offer_body(category))

    response = await put_offer(
        merchant.key,
        "sku-1",
        offer_body(category, title="Better title", url=f"{SITE}/other"),
    )

    assert response.json()["data"]["outcome"] == "refreshed"
    (offer,) = await offer_rows(engine)
    assert offer.title == "Better title"
    (link,) = await rows(engine, "SELECT destination_url FROM affiliate_links")
    assert link[0] == f"{SITE}/other"
    (pending,) = await rows(
        engine, "SELECT pending_hash, content_hash FROM partner_submissions"
    )
    assert pending.pending_hash is None


async def test_the_fingerprint_stored_for_a_partner_draft_is_the_one_the_admin_side_computes(
    client: AsyncClient, engine: AsyncEngine, admin: dict[str, str], partner_world: None
) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.repositories.admin_offers import AdminOfferRepository
    from app.services.offer_fingerprint import fingerprint

    category, merchant = await setup(client, admin)
    await put_offer(merchant.key, "sku-1", offer_body(category))
    await put_offer(merchant.key, "sku-1", offer_body(category, title="Changed once"))
    (stored,) = await rows(
        engine, "SELECT offer_id, applied_content_hash FROM offer_imports"
    )

    async with AsyncSession(engine) as session:
        repository = AdminOfferRepository(session)
        offer = await repository.get(stored.offer_id)
        links = await repository.list_links(stored.offer_id)
        assert offer is not None
        assert fingerprint(offer, links) == stored.applied_content_hash


async def test_an_edited_draft_is_not_overwritten_the_update_waits_for_an_editor(
    client: AsyncClient, engine: AsyncEngine, admin: dict[str, str], partner_world: None
) -> None:
    category, merchant = await setup(client, admin)
    await put_offer(merchant.key, "sku-1", offer_body(category))
    offer_id = await sub.offer_id_of(client, admin, "Free")
    await client.patch(
        f"{OFFERS}/{offer_id}", json={"title": "Editor's title"}, headers=admin
    )

    response = await put_offer(
        merchant.key, "sku-1", offer_body(category, title="Retailer title")
    )
    view = await partner_call(merchant.key, "GET", f"{PARTNER_OFFERS}/sku-1")

    assert response.json()["data"]["outcome"] == "pending_update"
    assert view.json()["data"]["update_pending"] is True
    (offer,) = await offer_rows(engine)
    assert offer.title == "Editor's title"
    (submission,) = await rows(
        engine, "SELECT pending_payload, pending_hash FROM partner_submissions"
    )
    assert submission.pending_payload["title"] == "Retailer title"
    (imported,) = await rows(engine, "SELECT source_changed_at FROM offer_imports")
    assert imported.source_changed_at is not None


async def test_a_live_offer_keeps_its_published_content_when_the_retailer_updates_it(
    client: AsyncClient, engine: AsyncEngine, admin: dict[str, str], partner_world: None
) -> None:
    category, merchant = await setup(client, admin)
    await put_offer(merchant.key, "sku-1", offer_body(category))
    offer_id = await sub.offer_id_of(client, admin, "Free")
    url = f"{OFFERS}/{offer_id}"
    assert (await approve(client, admin, url, "verify")).status_code == 200
    published = await approve(
        client, admin, url, "publish", commercial_basis="EDITORIAL"
    )
    assert published.status_code == 200
    before = (await client.get(url, headers=admin)).json()["data"]

    response = await put_offer(
        merchant.key,
        "sku-1",
        offer_body(category, title="Retailer wants this", summary="New summary"),
    )
    after = (await client.get(url, headers=admin)).json()["data"]
    view = await partner_call(merchant.key, "GET", f"{PARTNER_OFFERS}/sku-1")

    assert response.json()["data"]["outcome"] == "pending_update"
    assert (after["title"], after["summary"], after["status"]) == (
        before["title"],
        before["summary"],
        "PUBLISHED",
    )
    assert (
        after["review_token"] == before["review_token"]
    )  # the page an editor reviewed has not moved
    assert view.json()["data"]["status"] == "live"
    assert (
        view.json()["data"]["public_url"]
        == f"http://localhost:3000/freebies/{before['slug']}"
    )
    assert view.json()["data"]["update_pending"] is True


async def test_sending_back_what_the_offer_already_says_clears_the_waiting_update(
    client: AsyncClient, engine: AsyncEngine, admin: dict[str, str], partner_world: None
) -> None:
    category, merchant = await setup(client, admin)
    original = offer_body(category)
    await put_offer(merchant.key, "sku-1", original)
    offer_id = await sub.offer_id_of(client, admin, "Free")
    await client.patch(
        f"{OFFERS}/{offer_id}", json={"title": "Editor's title"}, headers=admin
    )
    await put_offer(merchant.key, "sku-1", offer_body(category, title="Retailer title"))

    response = await put_offer(merchant.key, "sku-1", original)

    assert response.json()["data"]["outcome"] == "unchanged"
    assert response.json()["data"]["update_pending"] is False
    (imported,) = await rows(engine, "SELECT source_changed_at FROM offer_imports")
    assert imported.source_changed_at is None


async def test_an_archived_offer_refuses_updates_and_the_retailer_sees_declined(
    client: AsyncClient, admin: dict[str, str], partner_world: None
) -> None:
    category, merchant = await setup(client, admin)
    await put_offer(merchant.key, "sku-1", offer_body(category))
    offer_id = await sub.offer_id_of(client, admin, "Free")
    archived = await client.post(f"{OFFERS}/{offer_id}/archive", headers=admin)

    view = await partner_call(merchant.key, "GET", f"{PARTNER_OFFERS}/sku-1")
    refused = await put_offer(
        merchant.key, "sku-1", offer_body(category, title="Again")
    )

    assert archived.status_code == 200
    assert view.json()["data"]["status"] == "declined"
    assert (refused.status, refused.json()) == (
        409,
        {"success": False, "error": "Conflict", "code": "offer_closed"},
    )


async def test_an_offer_that_was_live_and_is_then_archived_is_ended_not_declined(
    client: AsyncClient, admin: dict[str, str], partner_world: None
) -> None:
    category, merchant = await setup(client, admin)
    await put_offer(merchant.key, "sku-1", offer_body(category))
    offer_id = await sub.offer_id_of(client, admin, "Free")
    url = f"{OFFERS}/{offer_id}"
    await approve(client, admin, url, "verify")
    await approve(client, admin, url, "publish", commercial_basis="EDITORIAL")
    await client.post(f"{url}/archive", headers=admin)

    view = await partner_call(merchant.key, "GET", f"{PARTNER_OFFERS}/sku-1")

    assert view.json()["data"]["status"] == "ended"


async def test_an_offer_an_editor_unpublishes_goes_back_to_pending_review(
    client: AsyncClient, admin: dict[str, str], partner_world: None
) -> None:
    category, merchant = await setup(client, admin)
    await put_offer(merchant.key, "sku-1", offer_body(category))
    offer_id = await sub.offer_id_of(client, admin, "Free")
    url = f"{OFFERS}/{offer_id}"
    await approve(client, admin, url, "verify")
    await approve(client, admin, url, "publish", commercial_basis="EDITORIAL")
    await client.post(f"{url}/unpublish", headers=admin)

    view = await partner_call(merchant.key, "GET", f"{PARTNER_OFFERS}/sku-1")

    assert view.json()["data"]["status"] == "pending_review"


async def test_a_direct_only_draft_still_needs_the_editors_confirmation_of_the_basis_to_publish(
    client: AsyncClient, admin: dict[str, str], partner_world: None
) -> None:
    category, merchant = await setup(client, admin)
    await put_offer(merchant.key, "sku-1", offer_body(category))
    offer_id = await sub.offer_id_of(client, admin, "Free")
    url = f"{OFFERS}/{offer_id}"
    await approve(client, admin, url, "verify")

    refused = await approve(client, admin, url, "publish")
    confirmed = await approve(
        client, admin, url, "publish", commercial_basis="EDITORIAL"
    )

    assert refused.status_code == 422
    assert "commercial basis" in refused.text
    assert confirmed.status_code == 200


async def test_a_partner_draft_still_needs_the_basis_confirmed_after_an_editor_adds_a_network_link(
    client: AsyncClient, admin: dict[str, str], partner_world: None
) -> None:
    category, merchant = await setup(client, admin)
    await put_offer(merchant.key, "sku-1", offer_body(category))
    offer_id = await sub.offer_id_of(client, admin, "Free")
    url = f"{OFFERS}/{offer_id}"
    added = await client.post(
        f"{url}/links",
        json={
            "provider": "awin",
            "destination_url": "https://www.awin1.com/cread.php?x=1",
        },
        headers=admin,
    )
    await approve(client, admin, url, "verify")

    refused = await approve(client, admin, url, "publish")
    detail = (await client.get(url, headers=admin)).json()["data"]

    assert added.status_code == 201
    assert refused.status_code == 422 and "commercial basis" in refused.text
    assert detail["needs_basis_confirmation"] is True


# ------------------------------------------------------------------------------------------------- end


async def test_end_sets_the_end_date_now_without_touching_the_status_and_ending_twice_is_a_no_op(
    client: AsyncClient, engine: AsyncEngine, admin: dict[str, str], partner_world: None
) -> None:
    category, merchant = await setup(client, admin)
    await put_offer(merchant.key, "sku-1", offer_body(category))

    first = await partner_call(merchant.key, "POST", f"{PARTNER_OFFERS}/sku-1/end")
    (after_first,) = await rows(engine, "SELECT end_at, status FROM offers")
    second = await partner_call(merchant.key, "POST", f"{PARTNER_OFFERS}/sku-1/end")
    (after_second,) = await rows(engine, "SELECT end_at, status FROM offers")
    (audits,) = await rows(
        engine, "SELECT count(*) FROM audit_logs WHERE action = 'partner.offers.end'"
    )

    assert first.json()["data"]["changed"] is True
    assert first.json()["data"]["status"] == "ended"
    assert second.json()["data"]["changed"] is False
    assert after_first.status == "DRAFT" == after_second.status
    assert after_first.end_at is not None
    assert after_second.end_at == after_first.end_at
    assert audits[0] == 1


async def test_ending_a_live_offer_takes_effect_on_the_public_site_at_once(
    client: AsyncClient, admin: dict[str, str], partner_world: None
) -> None:
    category, merchant = await setup(client, admin)
    await put_offer(merchant.key, "sku-1", offer_body(category))
    offer_id = await sub.offer_id_of(client, admin, "Free")
    url = f"{OFFERS}/{offer_id}"
    await approve(client, admin, url, "verify")
    await approve(client, admin, url, "publish", commercial_basis="EDITORIAL")
    slug = (await client.get(url, headers=admin)).json()["data"]["slug"]
    live = await client.get(f"/v1/catalogue/offers/{slug}")

    ended = await partner_call(merchant.key, "POST", f"{PARTNER_OFFERS}/sku-1/end")
    stored = (await client.get(url, headers=admin)).json()["data"]

    assert live.status_code == 200
    assert ended.json()["data"]["status"] == "ended"
    assert stored["status"] == "PUBLISHED"  # the health job flips the status later
    assert stored["end_at"] is not None


async def test_ending_an_offer_whose_start_is_still_ahead_clears_the_start_so_the_window_stays_valid(
    client: AsyncClient, engine: AsyncEngine, admin: dict[str, str], partner_world: None
) -> None:
    category, merchant = await setup(client, admin)
    await put_offer(
        merchant.key,
        "sku-1",
        offer_body(
            category,
            start_at=(datetime.now(UTC) + timedelta(days=30)).isoformat(),
            end_at=(datetime.now(UTC) + timedelta(days=60)).isoformat(),
        ),
    )

    await partner_call(merchant.key, "POST", f"{PARTNER_OFFERS}/sku-1/end")
    (offer,) = await rows(engine, "SELECT start_at, end_at FROM offers")

    assert offer.start_at is None
    assert offer.end_at is not None


async def test_ending_an_offer_that_already_ended_changes_nothing_and_a_past_end_is_kept(
    client: AsyncClient, engine: AsyncEngine, admin: dict[str, str], partner_world: None
) -> None:
    category, merchant = await setup(client, admin)
    await put_offer(merchant.key, "sku-1", offer_body(category))
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE offers SET end_at = now() - interval '2 days'")
        )
    (before,) = await rows(engine, "SELECT end_at FROM offers")

    response = await partner_call(merchant.key, "POST", f"{PARTNER_OFFERS}/sku-1/end")
    (after,) = await rows(engine, "SELECT end_at FROM offers")

    assert response.json()["data"]["changed"] is False
    assert after.end_at == before.end_at


# ----------------------------------------------------------------------------- nothing is ever published


async def test_no_sequence_of_partner_calls_publishes_verifies_or_features_anything(
    client: AsyncClient, engine: AsyncEngine, admin: dict[str, str], partner_world: None
) -> None:
    category, merchant = await setup(client, admin)
    for step in range(3):
        for ref in ("a", "b"):
            body = offer_body(
                category, title=f"Offer {ref} {step}", summary=f"Summary {step}"
            )
            await put_offer(merchant.key, ref, body)
            await put_offer(merchant.key, ref, body)
            await partner_call(merchant.key, "GET", PARTNER_OFFERS)
            await partner_call(merchant.key, "GET", f"{PARTNER_OFFERS}/{ref}")
        await partner_call(merchant.key, "POST", f"{PARTNER_OFFERS}/a/end")
        await put_offer(merchant.key, "a", offer_body(category, title="After end"))
        # An editor works on the same draft in between (an edit, not a verification or a publication), so the
        # retailer's later calls meet an edited draft and queue instead of refreshing it.
        offer_a = await sub.offer_id_of(client, admin, "Offer a")
        edited = await client.patch(
            f"{OFFERS}/{offer_a}", json={"title": f"Offer a {step}"}, headers=admin
        )
        assert edited.status_code == 200
        # smuggling a status or a flag in the body is refused, and nothing is stored
        for sneaky in (
            {"status": "PUBLISHED"},
            {"featured": True},
            {"last_verified_at": "2026-01-01T00:00:00Z"},
        ):
            refused = await put_offer(
                merchant.key, "c", {**offer_body(category), **sneaky}
            )
            assert refused.status == 422

    stored = await offer_rows(engine)
    assert {o.status for o in stored} == {"DRAFT"}
    assert {o.featured for o in stored} == {False}
    assert {o.last_verified_at for o in stored} == {None}
    public = await client.get("/v1/catalogue/offers")
    assert public.json()["data"]["items"] == []


# ------------------------------------------------------------------------------------------- link host


@pytest.mark.parametrize(
    ("url", "accepted"),
    [
        ("https://shop.example.com/x", True),
        ("https://www.shop.example.com/x", True),
        ("https://SHOP.example.com/x?utm=1", True),
        ("https://evil.example.net/x", False),
        ("https://shop.example.com.evil.net/x", False),
        ("https://sub.shop.example.com/x", False),
        ("https://example.com/x", False),
        ("https://www.awin1.com/cread.php?x=1", False),
        ("http://shop.example.com/x", False),
        ("https://user@shop.example.com/x", False),
        ("//shop.example.com/x", False),
        ("javascript:alert(1)", False),
    ],
)
async def test_a_link_must_point_at_exactly_the_merchants_own_website_host(
    client: AsyncClient,
    engine: AsyncEngine,
    admin: dict[str, str],
    partner_world: None,
    url: str,
    accepted: bool,
) -> None:
    category, merchant = await setup(client, admin)

    response = await put_offer(merchant.key, "sku-1", offer_body(category, url=url))

    assert (response.status in (200, 201)) is accepted
    if not accepted:
        assert response.status == 422
        assert response.json()["code"] in {"link_host_not_allowed", "invalid_url"}
        assert await offer_rows(engine) == []
    (site,) = await rows(engine, "SELECT website FROM merchants")
    assert site.website == SITE  # the API never writes merchants.website


async def test_a_merchant_without_a_website_cannot_submit_a_link(
    client: AsyncClient, engine: AsyncEngine, admin: dict[str, str], partner_world: None
) -> None:
    category, merchant = await setup(client, admin)
    async with engine.begin() as connection:
        await connection.execute(text("UPDATE merchants SET website = NULL"))

    response = await put_offer(merchant.key, "sku-1", offer_body(category))

    assert (response.status, response.json()["code"]) == (422, "link_host_not_allowed")


# ------------------------------------------------------------------------------------------- the input


async def test_hostile_markup_and_control_characters_are_cleaned_before_anything_is_stored(
    client: AsyncClient, engine: AsyncEngine, admin: dict[str, str], partner_world: None
) -> None:
    category, merchant = await setup(client, admin)
    body = offer_body(
        category,
        title="Free <b>box</b><script>alert(1)</script> &lt;i&gt;now&lt;/i&gt;\u200b\x07",
        summary="<a href='javascript:x'>Click</a>" + chr(0x202E) + " here",
        description=[
            "<p>First</p><p>Second</p>",
            "<script>only</script>",
            "&amp;amp; kept",
        ],
        steps=[{"title": "<em>Go</em>", "detail": "Open\x00 the page"}],
    )

    response = await put_offer(merchant.key, "sku-1", body)
    (offer,) = await offer_rows(engine)

    assert response.status == 201
    assert offer.title == "Free box now"
    assert offer.summary == "Click here"
    assert offer.description == ["First Second", "&amp; kept"]
    assert offer.steps == [{"title": "Go", "detail": "Open the page"}]
    for value in (offer.title, offer.summary, *offer.description):
        assert "<" not in value and "\u200b" not in value and "\x07" not in value


@pytest.mark.parametrize(
    "changes",
    [
        {"title": "<script></script>"},
        {"title": "\u200b\u200b"},
        {"summary": "<br><br>"},
        {"eligibility": "<!-- nothing -->"},
        {"steps": [{"title": "<b></b>", "detail": "x"}]},
    ],
)
async def test_a_text_that_is_nothing_but_markup_is_refused_not_stored_empty(
    client: AsyncClient,
    engine: AsyncEngine,
    admin: dict[str, str],
    partner_world: None,
    changes: dict[str, Any],
) -> None:
    category, merchant = await setup(client, admin)

    response = await put_offer(merchant.key, "sku-1", offer_body(category, **changes))

    assert (response.status, response.json()["code"]) == (422, "empty_text")
    assert await offer_rows(engine) == []


@pytest.mark.parametrize(
    "changes",
    [
        {"title": "x" * 121},
        {"summary": "x" * 201},
        {"description": ["x"] * 6},
        {"description": ["x" * 601]},
        {"steps": [{"title": "t", "detail": "d"}] * 7},
        {"url": "https://shop.example.com/" + "a" * 2100},
        {"eligibility": "x" * 401},
        {"offer_type": "VOUCHER"},
        {"offer_type": "CASHBACK"},
        {"offer_type": "COMPETITION"},
        {"postage_kind": "SOMETIMES"},
        {"requires_card": "yes"},
        {"requires_card": 1},
        {"extra_field": "nope"},
        {
            "postage_amount_minor": 10**9,
            "postage_kind": "COST",
            "postage_currency": "GBP",
        },
        {"start_at": 1700000000},
        {"end_at": "2099-01-01T00:00:00"},
        {"title": ""},
        {"category": "Not A Slug"},
    ],
)
async def test_a_body_outside_the_documented_shape_is_a_plain_422_that_echoes_nothing(
    client: AsyncClient,
    engine: AsyncEngine,
    admin: dict[str, str],
    partner_world: None,
    changes: dict[str, Any],
) -> None:
    category, merchant = await setup(client, admin)

    response = await put_offer(merchant.key, "sku-1", offer_body(category, **changes))

    assert response.status == 422
    assert response.json() == {"success": False, "error": "Invalid request"}
    assert await offer_rows(engine) == []


@pytest.mark.parametrize(
    "missing",
    [
        "postage_kind",
        "requires_card",
        "subscription_terms",
        "eligibility",
        "availability",
        "start_at",
        "end_at",
    ],
)
async def test_postage_card_terms_eligibility_availability_and_dates_are_required(
    client: AsyncClient, admin: dict[str, str], partner_world: None, missing: str
) -> None:
    category, merchant = await setup(client, admin)
    body = offer_body(category)
    del body[missing]

    response = await put_offer(merchant.key, "sku-1", body)

    assert response.status == 422


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"offer_type": "FREE_TRIAL"}, "subscription_terms_required"),
        ({"postage_kind": "COST"}, "invalid_postage"),
        ({"postage_kind": "FREE", "postage_amount_minor": 5}, "invalid_postage"),
        (
            {"start_at": "2099-02-01T00:00:00Z", "end_at": "2099-01-01T00:00:00Z"},
            "invalid_window",
        ),
        ({"end_at": "2020-01-01T00:00:00Z"}, "invalid_window"),
        ({"category": "no-such-category"}, "unknown_category"),
        ({"url": "https://shop.example.com/a b"}, "invalid_url"),
    ],
)
async def test_a_value_the_rules_refuse_is_a_422_with_a_stable_code(
    client: AsyncClient,
    engine: AsyncEngine,
    admin: dict[str, str],
    partner_world: None,
    changes: dict[str, Any],
    code: str,
) -> None:
    category, merchant = await setup(client, admin)

    response = await put_offer(merchant.key, "sku-1", offer_body(category, **changes))

    assert response.status == 422
    assert response.json() == {
        "success": False,
        "error": "Invalid request",
        "code": code,
    }
    assert await offer_rows(engine) == []


async def test_a_trial_with_terms_and_a_costed_postage_are_accepted(
    client: AsyncClient, engine: AsyncEngine, admin: dict[str, str], partner_world: None
) -> None:
    category, merchant = await setup(client, admin)

    response = await put_offer(
        merchant.key,
        "trial-1",
        offer_body(
            category,
            offer_type="FREE_TRIAL",
            subscription_terms="Cancel any time before day 30 in your account.",
            postage_kind="COST",
            postage_amount_minor=199,
            postage_currency="GBP",
            requires_card=True,
        ),
    )

    assert response.status == 201


@pytest.mark.parametrize("ref", ["a b", "a/b", "a" * 65, "é", "a;b", "a%20b"])
async def test_a_ref_that_is_not_a_plain_identifier_is_refused(
    client: AsyncClient, admin: dict[str, str], partner_world: None, ref: str
) -> None:
    category, merchant = await setup(client, admin)

    response = await put_offer(merchant.key, ref, offer_body(category))

    # "a/b" is a path with another segment, so no route matches it; every other ref reaches the pattern.
    assert response.status == (404 if "/" in ref else 422)


# ---------------------------------------------------------------------------------------------- caps


async def test_the_open_submission_cap_refuses_a_new_ref_but_not_an_update(
    client: AsyncClient,
    engine: AsyncEngine,
    admin: dict[str, str],
    partner_world: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.partner.submission_service as service

    monkeypatch.setattr(service, "MAX_OPEN_SUBMISSIONS", 2)
    category, merchant = await setup(client, admin)
    await put_offer(merchant.key, "a", offer_body(category, title="A"))
    await put_offer(merchant.key, "b", offer_body(category, title="B"))

    refused = await put_offer(merchant.key, "c", offer_body(category, title="C"))
    update = await put_offer(merchant.key, "a", offer_body(category, title="A again"))

    assert (refused.status, refused.json()) == (
        429,
        {"success": False, "error": "Too many requests", "code": "open_submission_cap"},
    )
    assert update.status == 200
    assert len(await offer_rows(engine)) == 2


async def test_the_daily_cap_refuses_a_new_ref_and_a_new_day_starts_at_midnight_utc(
    client: AsyncClient,
    engine: AsyncEngine,
    admin: dict[str, str],
    partner_world: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.partner.submission_service as service

    monkeypatch.setattr(service, "MAX_NEW_SUBMISSIONS_PER_DAY", 2)
    category, merchant = await setup(client, admin)
    await put_offer(merchant.key, "a", offer_body(category, title="A"))
    await put_offer(merchant.key, "b", offer_body(category, title="B"))
    refused = await put_offer(merchant.key, "c", offer_body(category, title="C"))
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE partner_submissions SET created_at = now() - interval '2 days'"
            )
        )

    allowed = await put_offer(merchant.key, "c", offer_body(category, title="C"))

    assert refused.json()["code"] == "daily_submission_cap"
    assert allowed.status == 201


async def test_concurrent_new_refs_cannot_exceed_the_cap(
    client: AsyncClient,
    engine: AsyncEngine,
    admin: dict[str, str],
    partner_world: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.partner.submission_service as service

    monkeypatch.setattr(service, "MAX_OPEN_SUBMISSIONS", 3)
    category, merchant = await setup(client, admin)
    jobs = [(f"r{n}", offer_body(category, title=f"Offer {n}")) for n in range(8)]

    responses = await put_many(merchant.key, jobs)

    assert sorted(r.status for r in responses) == [
        201,
        201,
        201,
        429,
        429,
        429,
        429,
        429,
    ]
    assert len(await offer_rows(engine)) == 3


async def test_a_cap_of_one_merchant_does_not_block_another(
    client: AsyncClient,
    admin: dict[str, str],
    partner_world: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.partner.submission_service as service

    monkeypatch.setattr(service, "MAX_OPEN_SUBMISSIONS", 1)
    category, first = await setup(client, admin)
    second = await make_merchant(client, admin, "Other", "https://other.example.com")
    await put_offer(first.key, "a", offer_body(category))

    blocked = await put_offer(first.key, "b", offer_body(category, title="B"))
    free = await put_offer(
        second.key, "a", offer_body(category, url="https://other.example.com/x")
    )

    assert (blocked.status, free.status) == (429, 201)


# ----------------------------------------------------------------------------------------------- reads


async def test_list_pages_the_callers_submissions_newest_first(
    client: AsyncClient, admin: dict[str, str], partner_world: None
) -> None:
    category, merchant = await setup(client, admin)
    for n in range(5):
        await put_offer(merchant.key, f"r{n}", offer_body(category, title=f"Offer {n}"))

    page = await partner_call(merchant.key, "GET", f"{PARTNER_OFFERS}?limit=2&offset=1")
    too_big = await partner_call(merchant.key, "GET", f"{PARTNER_OFFERS}?limit=101")

    data = page.json()["data"]
    assert [o["ref"] for o in data["offers"]] == ["r3", "r2"]
    assert (data["total_count"], data["limit"], data["offset"]) == (5, 2, 1)
    assert too_big.status == 422


async def test_a_submission_view_has_no_internal_field(
    client: AsyncClient, admin: dict[str, str], partner_world: None
) -> None:
    category, merchant = await setup(client, admin)
    await put_offer(merchant.key, "sku-1", offer_body(category))

    view = await partner_call(merchant.key, "GET", f"{PARTNER_OFFERS}/sku-1")

    assert set(view.json()["data"]) == {
        "ref",
        "status",
        "title",
        "public_url",
        "update_pending",
        "submitted_at",
        "ends_at",
    }
