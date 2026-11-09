"""End to end: the admin offers routes, permissions, lifecycle and audit trail on real Postgres.

Fixtures and the safety contract (local ``*_test`` database only) are in
``tests/catalogue_e2e_support.py``.
"""

import json

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

import tests.catalogue_e2e_support as support
from tests.catalogue_e2e_support import (
    EDITOR,
    approve,
    as_user,
    audit_rows,
    offer_body,
    rows,
    seed_admin,
)

pytestmark = support.requires_database
database_url, engine, client = support.database_url, support.engine, support.client

OFFERS = "/v1/admin/offers"


@pytest.fixture
async def editor(engine, client):
    """An editor with every catalogue permission, plus a category and a merchant."""
    await seed_admin(engine, "editor", EDITOR)
    headers = as_user("editor")
    category_id, merchant_id = await support.create_references(client, headers)
    return headers, category_id, merchant_id


async def make_offer(client, editor, **changes):
    headers, category_id, merchant_id = editor
    created = await client.post(
        OFFERS, json=offer_body(category_id, merchant_id, **changes), headers=headers
    )
    assert created.status_code == 201, created.text
    return created.json()["data"]


async def test_unauthenticated_request_is_401(client, engine):
    assert (await client.get(OFFERS)).status_code == 401


async def test_user_without_an_admin_record_is_403_and_writes_nothing(client, engine):
    await seed_admin(engine, "plain")

    read = await client.get(OFFERS, headers=as_user("plain"))
    write = await client.post(OFFERS, json={}, headers=as_user("plain"))

    assert (read.status_code, write.status_code) == (403, 403)
    assert await audit_rows(engine) == []


@pytest.mark.parametrize(
    ("method", "path", "needs"),
    [
        ("POST", OFFERS, "offers.write"),
        ("PATCH", f"{OFFERS}/x", "offers.write"),
        ("POST", f"{OFFERS}/x/publish", "offers.publish"),
        ("POST", f"{OFFERS}/x/unpublish", "offers.publish"),
        ("POST", f"{OFFERS}/x/expire", "offers.publish"),
        ("POST", f"{OFFERS}/x/archive", "offers.publish"),
        ("POST", f"{OFFERS}/x/verify", "offers.publish"),
        ("GET", OFFERS, "offers.read"),
        ("POST", "/v1/admin/categories", "categories.write"),
        ("POST", "/v1/admin/merchants", "merchants.write"),
    ],
)
async def test_admin_missing_the_permission_gets_403_and_changes_nothing(
    client, engine, method, path, needs
):
    await seed_admin(engine, "partial", [p for p in EDITOR if p != needs])

    result = await client.request(method, path, json={}, headers=as_user("partial"))

    assert result.status_code == 403
    assert await audit_rows(engine) == []
    assert await rows(engine, "SELECT 1 FROM offers") == []


async def test_create_persists_a_draft_and_audits_in_the_same_transaction(
    client, engine, editor
):
    data = await make_offer(client, editor)

    stored = await rows(
        engine,
        "SELECT slug, status, last_verified_at, source_url FROM offers WHERE id = :id",
        id=data["id"],
    )
    audit = await audit_rows(engine, "admin.offers.create")
    assert stored[0] == (
        "free-sample-box",
        "DRAFT",
        None,
        "https://shop.example.com/box",
    )
    assert len(audit) == 1
    assert audit[0][2:4] == ("offers", data["id"])
    assert audit[0][4]["after"]["status"] == "DRAFT"


async def test_a_failing_audit_write_rolls_the_change_back(
    client, engine, editor, monkeypatch
):
    async def boom(*_args, **_kwargs):
        raise RuntimeError("audit store down")

    monkeypatch.setattr("app.services.audit.AuditService.record_admin_action", boom)
    headers, category_id, merchant_id = editor

    result = await client.post(
        OFFERS, json=offer_body(category_id, merchant_id), headers=headers
    )

    assert result.status_code == 500
    assert await rows(engine, "SELECT 1 FROM offers") == []


async def test_duplicate_title_is_409_and_explicit_slug_resolves_it(
    client, engine, editor
):
    headers, category_id, merchant_id = editor
    await make_offer(client, editor)

    clash = await client.post(
        OFFERS, json=offer_body(category_id, merchant_id), headers=headers
    )
    explicit = await client.post(
        OFFERS,
        json=offer_body(category_id, merchant_id, slug="free-sample-box-2"),
        headers=headers,
    )

    assert clash.status_code == 409 and clash.json()["code"] == "slug_taken"
    assert explicit.status_code == 201
    assert explicit.json()["data"]["slug"] == "free-sample-box-2"
    assert len(await audit_rows(engine, "admin.offers.create")) == 2


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(document.cookie)",
        "data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==",
        "http://shop.example.com/box",
        "//evil.example.com",
        "https://shop.example.com@evil.example.com/",
        "https://evil.example.com\\@shop.example.com/",
        "https://shop.example.com/%0d%0aSet-Cookie:a=b",
        "https://127.0.0.1/admin",
        "https://0x7f.0.0.0x1/",
        "https://shop.example.com:8443/",
        "https://shop.example.com/ x",
    ],
)
async def test_unsafe_urls_are_422_and_nothing_is_stored(client, engine, editor, url):
    headers, category_id, merchant_id = editor
    host = await make_offer(client, editor, title="Host offer")

    created = await client.post(
        OFFERS,
        json=offer_body(category_id, merchant_id, source_url=url),
        headers=headers,
    )
    link = await client.post(
        f"{OFFERS}/{host['id']}/links",
        json={"provider": "awin", "destination_url": url},
        headers=headers,
    )

    assert (created.status_code, created.json()["code"]) == (422, "invalid_url")
    assert (link.status_code, link.json()["code"]) == (422, "invalid_url")
    assert await rows(engine, "SELECT 1 FROM affiliate_links") == []
    assert len(await rows(engine, "SELECT 1 FROM offers")) == 1


async def test_unknown_category_and_merchant_are_422_codes(client, engine, editor):
    headers, category_id, merchant_id = editor

    category = await client.post(
        OFFERS, json=offer_body("ghost", merchant_id), headers=headers
    )
    merchant = await client.post(
        OFFERS, json=offer_body(category_id, "ghost"), headers=headers
    )

    assert category.json()["code"] == "unknown_category"
    assert merchant.json()["code"] == "unknown_merchant"


async def test_postage_cost_rules_are_checked_before_the_database(
    client, engine, editor
):
    headers, category_id, merchant_id = editor
    half = offer_body(category_id, merchant_id, postage_kind="COST")
    full = offer_body(
        category_id,
        merchant_id,
        postage_kind="COST",
        postage_amount_minor=299,
        postage_currency="GBP",
        slug="with-postage",
    )

    incomplete = await client.post(OFFERS, json=half, headers=headers)
    complete = await client.post(OFFERS, json=full, headers=headers)

    assert (incomplete.status_code, incomplete.json()["code"]) == (
        422,
        "invalid_postage",
    )
    assert complete.json()["data"]["postage_amount_minor"] == 299


async def test_read_unknown_id_is_404_and_empty_list_is_an_empty_page(
    client, engine, editor
):
    headers, *_ = editor

    missing = await client.get(f"{OFFERS}/ghost", headers=headers)
    empty = await client.get(OFFERS, headers=headers)

    assert (missing.status_code, missing.json()["code"]) == (404, "not_found")
    assert (
        empty.json()["data"]["offers"] == []
        and empty.json()["data"]["total_count"] == 0
    )


async def test_list_search_status_category_filters_and_paging(client, engine, editor):
    headers, category_id, _merchant_id = editor
    await make_offer(client, editor, title="Alpha Sample")
    await make_offer(client, editor, title="Beta Trial", offer_type="FREE_TRIAL")
    await make_offer(client, editor, title="100% Gamma")

    def count(response):
        return response.json()["data"]["total_count"]

    by_search = await client.get(f"{OFFERS}?search=beta", headers=headers)
    wildcard = await client.get(f"{OFFERS}?search=%25", headers=headers)
    drafts = await client.get(f"{OFFERS}?status=DRAFT", headers=headers)
    published = await client.get(f"{OFFERS}?status=PUBLISHED", headers=headers)
    in_category = await client.get(
        f"{OFFERS}?category_id={category_id}", headers=headers
    )
    elsewhere = await client.get(f"{OFFERS}?category_id=nope", headers=headers)
    page = await client.get(f"{OFFERS}?limit=2&offset=2", headers=headers)

    assert [o["title"] for o in by_search.json()["data"]["offers"]] == ["Beta Trial"]
    assert count(wildcard) == 1  # "%" is literal: only the "100%" title matches
    assert (count(drafts), count(published)) == (3, 0)
    assert (count(in_category), count(elsewhere)) == (3, 0)
    assert count(page) == 3 and len(page.json()["data"]["offers"]) == 1


async def test_update_changes_fields_audits_names_only_and_keeps_the_slug(
    client, engine, editor
):
    headers, *_ = editor
    data = await make_offer(client, editor)

    updated = await client.patch(
        f"{OFFERS}/{data['id']}",
        json={
            "title": "Renamed",
            "summary": "private editorial wording",
            "featured": True,
        },
        headers=headers,
    )

    body = updated.json()["data"]
    audit = (await audit_rows(engine, "admin.offers.update"))[0][4]
    assert (body["title"], body["slug"], body["featured"]) == (
        "Renamed",
        "free-sample-box",
        True,
    )
    assert audit["details"]["fields"] == ["featured", "summary", "title"]
    assert "private editorial wording" not in json.dumps(audit)


async def test_publish_is_blocked_until_verified_and_has_a_destination(
    client, engine, editor
):
    headers, *_ = editor
    data = await make_offer(client, editor, source_url=None)
    url = f"{OFFERS}/{data['id']}"

    blocked = await approve(client, headers, url, "publish")
    verified = await approve(client, headers, url, "verify", note="Read the T&Cs")
    still_blocked = await approve(client, headers, url, "publish")
    link = await client.post(
        f"{url}/links",
        json={"provider": "awin", "destination_url": "https://www.awin1.com/c?x=1"},
        headers=headers,
    )
    published = await approve(client, headers, url, "publish")

    assert blocked.status_code == 422 and blocked.json()["code"] == "publish_blocked"
    assert "not been verified" in blocked.json()["error"]
    assert verified.json()["data"]["last_verified_at"] is not None
    assert "no active affiliate link" in still_blocked.json()["error"]
    assert link.status_code == 201
    assert published.json()["data"]["status"] == "PUBLISHED"
    stored = await rows(
        engine,
        "SELECT status, last_verified_at FROM offers WHERE id = :i",
        i=data["id"],
    )
    assert stored[0][0] == "PUBLISHED" and stored[0][1] is not None


async def test_publish_checks_category_merchant_and_end_date(client, engine, editor):
    headers, category_id, merchant_id = editor
    data = await make_offer(client, editor)
    url = f"{OFFERS}/{data['id']}"
    await approve(client, headers, url, "verify")

    await client.post(f"/v1/admin/categories/{category_id}/disable", headers=headers)
    await client.post(f"/v1/admin/merchants/{merchant_id}/deactivate", headers=headers)
    await client.patch(url, json={"end_at": "2020-01-01T00:00:00Z"}, headers=headers)
    blocked = await approve(client, headers, url, "publish")

    assert blocked.status_code == 422
    for phrase in (
        "category is disabled",
        "merchant is inactive",
        "end date has passed",
    ):
        assert phrase in blocked.json()["error"]
    assert (await rows(engine, "SELECT status FROM offers"))[0][0] == "DRAFT"


async def test_full_lifecycle_and_illegal_transitions_are_409(client, engine, editor):
    headers, *_ = editor
    data = await make_offer(client, editor)
    url = f"{OFFERS}/{data['id']}"
    await approve(client, headers, url, "verify")

    async def step(action):
        if action in ("publish", "verify"):
            result = await approve(client, headers, url, action)
        else:
            result = await client.post(f"{url}/{action}", headers=headers)
        body = result.json()
        return result.status_code, body["data"]["status"] if "data" in body else body[
            "code"
        ]

    assert await step("unpublish") == (409, "illegal_transition")
    assert await step("expire") == (409, "illegal_transition")
    assert await step("publish") == (200, "PUBLISHED")
    assert await step("publish") == (409, "illegal_transition")
    assert await step("expire") == (200, "EXPIRED")
    assert await step("publish") == (200, "PUBLISHED")
    assert await step("unpublish") == (200, "DRAFT")
    assert await step("archive") == (200, "ARCHIVED")
    assert await step("publish") == (409, "illegal_transition")
    assert await step("archive") == (409, "illegal_transition")
    assert await step("verify") == (409, "illegal_transition")
    edit = await client.patch(url, json={"title": "Nope"}, headers=headers)
    assert (edit.status_code, edit.json()["code"]) == (409, "illegal_transition")
    actions = [r[1] for r in await audit_rows(engine)]
    assert actions.count("admin.offers.publish") == 2
    assert {
        "admin.offers.expire",
        "admin.offers.unpublish",
        "admin.offers.archive",
    } <= set(actions)


async def test_published_offers_cannot_be_edited_into_an_unpublishable_state(
    client, engine, editor
):
    headers, *_ = editor
    data = await make_offer(client, editor)
    url = f"{OFFERS}/{data['id']}"
    await approve(client, headers, url, "verify")
    await approve(client, headers, url, "publish")

    past = await client.patch(
        url, json={"end_at": "2020-01-01T00:00:00Z"}, headers=headers
    )
    no_destination = await client.patch(url, json={"source_url": None}, headers=headers)

    assert past.json()["code"] == "publish_blocked"
    assert no_destination.json()["code"] == "publish_blocked"
    stored = await rows(engine, "SELECT end_at, source_url FROM offers")
    assert stored[0] == (None, "https://shop.example.com/box")


async def test_the_database_check_is_the_backstop_for_published_offers(
    client, engine, editor
):
    data = await make_offer(client, editor)

    with pytest.raises(IntegrityError, match="published_is_verified"):
        async with engine.begin() as connection:
            await connection.execute(
                text("UPDATE offers SET status = 'PUBLISHED' WHERE id = :i"),
                {"i": data["id"]},
            )


async def test_verify_records_the_note_in_the_audit_row_and_stamps_the_time(
    client, engine, editor
):
    headers, *_ = editor
    data = await make_offer(client, editor)

    result = await approve(
        client,
        headers,
        f"{OFFERS}/{data['id']}",
        "verify",
        note="Checked on the merchant site",
    )

    audit = (await audit_rows(engine, "admin.offers.verify"))[0][4]
    assert result.json()["data"]["last_verified_at"] is not None
    assert audit["details"] == {"note": "Checked on the merchant site"}
    assert audit["before"]["last_verified_at"] is None


async def test_links_are_audited_without_the_destination_url(client, engine, editor):
    headers, *_ = editor
    data = await make_offer(client, editor)
    secret_url = "https://www.awin1.com/c?aff=ABC123-PRIVATE"

    created = await client.post(
        f"{OFFERS}/{data['id']}/links",
        json={"provider": "awin", "destination_url": secret_url, "tracking_id": "t-9"},
        headers=headers,
    )
    link_id = created.json()["data"]["id"]
    updated = await client.patch(
        f"{OFFERS}/{data['id']}/links/{link_id}",
        json={"active": False},
        headers=headers,
    )
    missing = await client.patch(
        f"{OFFERS}/{data['id']}/links/ghost", json={"active": False}, headers=headers
    )

    audits = [
        json.dumps(r[4]) for r in await audit_rows(engine) if "offer_links" in r[1]
    ]
    assert created.json()["data"]["destination_url"] == secret_url
    assert updated.json()["data"]["active"] is False and missing.status_code == 404
    assert len(audits) == 2
    assert all("ABC123" not in entry and "t-9" not in entry for entry in audits)
    assert "www.awin1.com" in audits[0]


async def test_last_active_link_of_a_published_offer_cannot_be_deactivated(
    client, engine, editor
):
    headers, *_ = editor
    data = await make_offer(client, editor, source_url=None)
    url = f"{OFFERS}/{data['id']}"
    link = await client.post(
        f"{url}/links",
        json={"provider": "awin", "destination_url": "https://www.awin1.com/c"},
        headers=headers,
    )
    await approve(client, headers, url, "verify")
    await approve(client, headers, url, "publish")

    result = await client.patch(
        f"{url}/links/{link.json()['data']['id']}",
        json={"active": False},
        headers=headers,
    )

    assert (result.status_code, result.json()["code"]) == (422, "publish_blocked")
    assert (await rows(engine, "SELECT active FROM affiliate_links"))[0][0] is True


async def test_responses_carry_no_user_data_and_are_not_cacheable(
    client, engine, editor
):
    headers, *_ = editor
    data = await make_offer(client, editor)

    detail = await client.get(f"{OFFERS}/{data['id']}", headers=headers)

    assert "editor@example.com" not in detail.text
    assert detail.headers["Cache-Control"] == "no-store"


async def test_a_link_must_use_a_host_its_provider_may_use(client, engine, editor):
    headers, *_ = editor
    data = await make_offer(client, editor)
    path = f"{OFFERS}/{data['id']}/links"

    wrong_host = await client.post(
        path,
        json={"provider": "awin", "destination_url": "https://www.tradedoubler.com/c"},
        headers=headers,
    )
    unknown = await client.post(
        path,
        json={"provider": "mystery", "destination_url": "https://www.awin1.com/c"},
        headers=headers,
    )
    off_site = await client.post(
        path,
        json={"provider": "direct", "destination_url": "https://evil.example/c"},
        headers=headers,
    )

    assert wrong_host.status_code == 422
    assert wrong_host.json()["code"] == "destination_not_allowed"
    assert (
        wrong_host.json()["error"]
        == "Destination host is not allowed for this provider"
    )
    assert unknown.json()["code"] == "unknown_provider"
    assert off_site.json()["code"] == "destination_not_allowed"
    assert await rows(engine, "SELECT id FROM affiliate_links") == []
