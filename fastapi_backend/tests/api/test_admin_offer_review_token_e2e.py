"""End to end: verify and publish fail with 409 when the offer changed after the editor loaded it.

Runs only with ``TEST_DATABASE_URL`` (see ``tests/catalogue_e2e_support.py``). The importer path is covered
in ``tests/commands/test_import_awin_offers_e2e.py`` and the in-flight race in
``test_admin_catalogue_locks_e2e.py``.
"""

import pytest
from sqlalchemy import text

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
LINK = {"provider": "awin", "destination_url": "https://www.awin1.com/c?x=1"}
CHANGED_CODE = (409, "offer_changed")


@pytest.fixture
async def world(engine, client):
    await seed_admin(engine, "editor", EDITOR)
    headers = as_user("editor")
    category_id, merchant_id = await support.create_references(client, headers)
    created = await client.post(
        OFFERS, json=offer_body(category_id, merchant_id), headers=headers
    )
    return headers, f"{OFFERS}/{created.json()['data']['id']}"


async def snapshot(engine):
    return (
        await rows(engine, "SELECT * FROM offers"),
        await rows(engine, "SELECT * FROM affiliate_links"),
        await rows(engine, "SELECT action FROM audit_logs ORDER BY created_at"),
    )


async def verified(client, headers, url):
    result = await approve(client, headers, url, "verify")
    assert result.status_code == 200
    return result


async def test_publish_with_the_token_loaded_after_the_last_change_succeeds(
    client, engine, world
):
    headers, url = world
    await verified(client, headers, url)

    published = await approve(client, headers, url, "publish")

    assert published.status_code == 200
    assert published.json()["data"]["status"] == "PUBLISHED"
    assert (await rows(engine, "SELECT status FROM offers"))[0][0] == "PUBLISHED"


async def test_an_admin_edit_after_loading_makes_publish_and_verify_409(
    client, engine, world
):
    headers, url = world
    await verified(client, headers, url)
    stale = await review_token(client, headers, url)
    await client.patch(url, json={"summary": "Quietly rewritten"}, headers=headers)
    before = await snapshot(engine)

    results = [
        await client.post(
            f"{url}/{step}", json={"review_token": stale}, headers=headers
        )
        for step in ("publish", "verify")
    ]

    assert [(r.status_code, r.json()["code"]) for r in results] == [CHANGED_CODE] * 2
    assert await snapshot(engine) == before
    assert (await rows(engine, "SELECT status FROM offers"))[0][0] == "DRAFT"


async def test_a_link_change_after_loading_makes_publish_409(client, engine, world):
    headers, url = world
    await verified(client, headers, url)
    link = await client.post(f"{url}/links", json=LINK, headers=headers)
    stale = await review_token(client, headers, url)
    await client.patch(
        f"{url}/links/{link.json()['data']['id']}",
        json={"destination_url": "https://www.awin1.com/c?x=2"},
        headers=headers,
    )
    before = await snapshot(engine)

    result = await client.post(
        f"{url}/publish", json={"review_token": stale}, headers=headers
    )

    assert (result.status_code, result.json()["code"]) == CHANGED_CODE
    assert await snapshot(engine) == before


async def test_a_new_link_after_loading_makes_publish_409(client, engine, world):
    headers, url = world
    await verified(client, headers, url)
    stale = await review_token(client, headers, url)
    await client.post(f"{url}/links", json=LINK, headers=headers)
    before = await snapshot(engine)

    result = await client.post(
        f"{url}/publish", json={"review_token": stale}, headers=headers
    )

    assert (result.status_code, result.json()["code"]) == CHANGED_CODE
    assert await snapshot(engine) == before


async def test_reading_the_offer_again_gives_a_token_that_works(client, engine, world):
    headers, url = world
    await verified(client, headers, url)
    stale = await review_token(client, headers, url)
    await client.patch(url, json={"title": "Edited"}, headers=headers)

    fresh = await review_token(client, headers, url)
    published = await client.post(
        f"{url}/publish", json={"review_token": fresh}, headers=headers
    )

    assert fresh != stale
    assert published.status_code == 200


async def test_the_token_does_not_move_when_only_bookkeeping_changes(
    client, engine, world
):
    headers, url = world
    first = await review_token(client, headers, url)
    async with engine.begin() as connection:
        await connection.execute(text("UPDATE offers SET updated_at = now()"))

    assert await review_token(client, headers, url) == first


async def test_a_missing_token_is_422_and_nothing_is_written(client, engine, world):
    headers, url = world
    await verified(client, headers, url)
    before = await snapshot(engine)

    results = [
        await client.post(f"{url}/publish", headers=headers),
        await client.post(f"{url}/publish", json={}, headers=headers),
        await client.post(f"{url}/verify", json={"note": "x"}, headers=headers),
    ]

    assert [r.status_code for r in results] == [422] * 3
    assert await snapshot(engine) == before


async def test_the_list_token_is_the_detail_token_and_works_for_verify(
    client, engine, world
):
    headers, url = world
    listed = (await client.get(OFFERS, headers=headers)).json()["data"]["offers"][0]
    detail_token = await review_token(client, headers, url)

    result = await client.post(
        f"{url}/verify", json={"review_token": listed["review_token"]}, headers=headers
    )

    assert listed["review_token"] == detail_token
    assert result.status_code == 200


async def test_taking_an_offer_down_needs_no_token_even_when_the_offer_changed(
    client, engine, world
):
    headers, url = world
    await verified(client, headers, url)
    await approve(client, headers, url, "publish")
    await client.patch(url, json={"summary": "Changed"}, headers=headers)

    result = await client.post(f"{url}/unpublish", headers=headers)

    assert result.status_code == 200
    assert result.json()["data"]["status"] == "DRAFT"
    actions = [r[1] for r in await audit_rows(engine)]
    assert "admin.offers.unpublish" in actions
