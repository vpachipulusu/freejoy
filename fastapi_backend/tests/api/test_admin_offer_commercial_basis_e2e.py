"""End to end: the commercial basis is stored, audited, reviewed, gates a direct-only publish and reaches
the public API only as ``sponsored``.

Runs only with ``TEST_DATABASE_URL`` (see ``tests/catalogue_e2e_support.py``). The backfill itself is
covered in ``tests/models/test_offer_commercial_basis_migration_e2e.py``.
"""

import pytest

import tests.catalogue_e2e_support as support
from tests.catalogue_e2e_support import (
    EDITOR,
    approve,
    as_user,
    audit_rows,
    offer_body,
    review_token,
    rows,
    seed_admin,
)

pytestmark = support.requires_database
database_url, engine, client = support.database_url, support.engine, support.client

OFFERS = "/v1/admin/offers"
WEBSITE = "https://www.acme.example"
DIRECT_LINK = {"provider": "direct", "destination_url": f"{WEBSITE}/free-box"}


@pytest.fixture
async def world(engine, client):
    await seed_admin(engine, "editor", EDITOR)
    headers = as_user("editor")
    category_id, merchant_id = await support.create_references(client, headers)
    await client.patch(
        f"/v1/admin/merchants/{merchant_id}", json={"website": WEBSITE}, headers=headers
    )
    return headers, category_id, merchant_id


async def create(client, world, **changes):
    headers, category_id, merchant_id = world
    created = await client.post(
        OFFERS,
        json=offer_body(category_id, merchant_id, **changes),
        headers=headers,
    )
    assert created.status_code == 201, created.text
    return created.json()["data"]


async def stored_basis(engine):
    return [r[0] for r in await rows(engine, "SELECT commercial_basis FROM offers")]


@pytest.mark.parametrize("basis", ["EDITORIAL", "AFFILIATE", "SPONSORED"])
async def test_each_basis_is_stored_and_read_back(client, engine, world, basis):
    offer = await create(client, world, commercial_basis=basis)

    read = await client.get(f"{OFFERS}/{offer['id']}", headers=world[0])

    assert offer["commercial_basis"] == read.json()["data"]["commercial_basis"] == basis
    assert await stored_basis(engine) == [basis]


async def test_an_offer_without_a_basis_is_refused_and_not_stored(
    client, engine, world
):
    headers, category_id, merchant_id = world
    body = offer_body(category_id, merchant_id)
    del body["commercial_basis"]

    refused = await client.post(OFFERS, json=body, headers=headers)

    assert refused.status_code == 422
    assert await stored_basis(engine) == []


async def test_creating_and_changing_the_basis_is_audited(client, engine, world):
    offer = await create(client, world, commercial_basis="AFFILIATE")
    await client.patch(
        f"{OFFERS}/{offer['id']}",
        json={
            "commercial_basis": "SPONSORED",
            "review_token": await review_token(
                client, world[0], f"{OFFERS}/{offer['id']}"
            ),
        },
        headers=world[0],
    )

    created = (await audit_rows(engine, "admin.offers.create"))[0]
    updated = (await audit_rows(engine, "admin.offers.update"))[0]

    assert created.metadata["after"]["commercial_basis"] == "AFFILIATE"
    assert updated.metadata["before"] == {"commercial_basis": "AFFILIATE"}
    assert updated.metadata["after"] == {"commercial_basis": "SPONSORED"}
    assert updated.metadata["details"] == {"fields": ["commercial_basis"]}


async def test_a_basis_change_after_loading_makes_publish_409(client, engine, world):
    headers = world[0]
    offer = await create(client, world)
    url = f"{OFFERS}/{offer['id']}"
    assert (await approve(client, headers, url, "verify")).status_code == 200
    stale = await review_token(client, headers, url)
    await client.patch(
        url,
        json={"commercial_basis": "SPONSORED", "review_token": stale},
        headers=headers,
    )

    result = await client.post(
        f"{url}/publish", json={"review_token": stale}, headers=headers
    )

    assert (result.status_code, result.json()["code"]) == (409, "offer_changed")
    assert (await rows(engine, "SELECT status FROM offers"))[0][0] == "DRAFT"


async def test_publishing_direct_only_needs_the_editor_to_confirm_the_basis(
    client, engine, world
):
    headers = world[0]
    offer = await create(client, world, source_url=None, commercial_basis="SPONSORED")
    url = f"{OFFERS}/{offer['id']}"
    await client.post(f"{url}/links", json=DIRECT_LINK, headers=headers)
    assert (await approve(client, headers, url, "verify")).status_code == 200

    unconfirmed = await approve(client, headers, url, "publish")
    wrong = await approve(client, headers, url, "publish", commercial_basis="EDITORIAL")
    still_draft = (await rows(engine, "SELECT status FROM offers"))[0][0]
    confirmed = await approve(
        client, headers, url, "publish", commercial_basis="SPONSORED"
    )

    assert (unconfirmed.status_code, unconfirmed.json()["code"]) == (
        422,
        "publish_blocked",
    )
    assert "commercial basis" in unconfirmed.json()["error"]
    assert (wrong.status_code, wrong.json()["code"]) == (409, "offer_changed")
    assert still_draft == "DRAFT"
    assert confirmed.status_code == 200
    assert confirmed.json()["data"]["status"] == "PUBLISHED"


async def test_a_network_link_needs_no_confirmation(client, world):
    headers = world[0]
    offer = await create(client, world, source_url=None, commercial_basis="AFFILIATE")
    url = f"{OFFERS}/{offer['id']}"
    await client.post(
        f"{url}/links",
        json={"provider": "awin", "destination_url": "https://www.awin1.com/c?x=1"},
        headers=headers,
    )
    assert (await approve(client, headers, url, "verify")).status_code == 200

    published = await approve(client, headers, url, "publish")

    assert published.status_code == 200


async def test_the_public_api_says_sponsored_and_never_the_basis(client, world):
    headers = world[0]
    offers = {}
    for basis in ("EDITORIAL", "AFFILIATE", "SPONSORED"):
        offer = await create(
            client,
            world,
            title=f"Box {basis.lower()}",
            commercial_basis=basis,
            featured=basis == "SPONSORED",
        )
        url = f"{OFFERS}/{offer['id']}"
        assert (await approve(client, headers, url, "verify")).status_code == 200
        assert (await approve(client, headers, url, "publish")).status_code == 200
        offers[basis] = offer["slug"]

    listed = await client.get("/v1/catalogue/offers")
    detail = await client.get(f"/v1/catalogue/offers/{offers['SPONSORED']}")

    by_slug = {item["slug"]: item for item in listed.json()["data"]["items"]}
    assert {basis: by_slug[slug]["sponsored"] for basis, slug in offers.items()} == {
        "EDITORIAL": False,
        "AFFILIATE": False,
        "SPONSORED": True,
    }
    assert by_slug[offers["SPONSORED"]]["featured"] is True
    assert detail.json()["data"]["sponsored"] is True
    for text in (listed.text, detail.text):
        assert "commercial_basis" not in text
        for value in ("EDITORIAL", "AFFILIATE", "SPONSORED"):
            assert value not in text


async def test_a_stale_form_cannot_undo_a_sponsorship(client, engine, world):
    headers = world[0]
    offer = await create(client, world, commercial_basis="EDITORIAL")
    url = f"{OFFERS}/{offer['id']}"
    loaded_by_second_editor = await review_token(client, headers, url)
    first = await client.patch(
        url,
        json={
            "commercial_basis": "SPONSORED",
            "review_token": loaded_by_second_editor,
        },
        headers=headers,
    )
    audits_before = len(await audit_rows(engine, "admin.offers.update"))

    stale = await client.patch(
        url,
        json={"commercial_basis": "EDITORIAL", "review_token": loaded_by_second_editor},
        headers=headers,
    )
    missing = await client.patch(
        url, json={"commercial_basis": "EDITORIAL"}, headers=headers
    )

    assert first.status_code == 200
    assert [(r.status_code, r.json()["code"]) for r in (stale, missing)] == [
        (409, "offer_changed")
    ] * 2
    assert await stored_basis(engine) == ["SPONSORED"]
    assert len(await audit_rows(engine, "admin.offers.update")) == audits_before


async def test_only_an_editor_who_may_publish_changes_the_basis_of_a_live_offer(
    client, engine, world
):
    headers = world[0]
    await seed_admin(engine, "writer", ["offers.read", "offers.write"])
    writer = as_user("writer")
    offer = await create(client, world, commercial_basis="SPONSORED")
    url = f"{OFFERS}/{offer['id']}"
    assert (await approve(client, headers, url, "verify")).status_code == 200
    assert (await approve(client, headers, url, "publish")).status_code == 200

    refused = await client.patch(
        url,
        json={
            "commercial_basis": "EDITORIAL",
            "review_token": await review_token(client, writer, url),
        },
        headers=writer,
    )
    allowed = await client.patch(
        url,
        json={
            "commercial_basis": "EDITORIAL",
            "review_token": await review_token(client, headers, url),
        },
        headers=headers,
    )

    assert (refused.status_code, refused.json()["code"]) == (403, "permission_denied")
    assert allowed.status_code == 200
    assert await stored_basis(engine) == ["EDITORIAL"]


async def test_a_live_offer_cannot_lose_its_last_network_link_unconfirmed(
    client, engine, world
):
    headers = world[0]
    offer = await create(client, world, source_url=None, commercial_basis="AFFILIATE")
    url = f"{OFFERS}/{offer['id']}"
    network = await client.post(
        f"{url}/links",
        json={"provider": "awin", "destination_url": "https://www.awin1.com/c?x=1"},
        headers=headers,
    )
    await client.post(f"{url}/links", json=DIRECT_LINK, headers=headers)
    assert (await approve(client, headers, url, "verify")).status_code == 200
    assert (await approve(client, headers, url, "publish")).status_code == 200

    refused = await client.patch(
        f"{url}/links/{network.json()['data']['id']}",
        json={"active": False},
        headers=headers,
    )

    assert (refused.status_code, refused.json()["code"]) == (422, "publish_blocked")
    active = await rows(
        engine, "SELECT provider FROM affiliate_links WHERE active ORDER BY provider"
    )
    assert [r[0] for r in active] == ["awin", "direct"]


async def test_the_publish_audit_row_says_which_basis_went_live_and_if_it_was_confirmed(
    client, engine, world
):
    headers = world[0]
    offer = await create(client, world, source_url=None, commercial_basis="SPONSORED")
    url = f"{OFFERS}/{offer['id']}"
    await client.post(f"{url}/links", json=DIRECT_LINK, headers=headers)
    assert (await approve(client, headers, url, "verify")).status_code == 200
    await approve(client, headers, url, "publish", commercial_basis="SPONSORED")

    (row,) = await audit_rows(engine, "admin.offers.publish")

    assert row.metadata["after"] == {
        "status": "PUBLISHED",
        "commercial_basis": "SPONSORED",
    }
    assert row.metadata["details"] == {"basis_confirmed": True}
