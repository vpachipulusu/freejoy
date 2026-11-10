"""End to end: dismissing a source change over HTTP against Postgres (permission, token, audit, the one column).

Runs only with ``TEST_DATABASE_URL`` (see ``tests/catalogue_e2e_support.py``). The importer's side of the
story (the next run neither re-flags the same source nor misses a newer one) is in
``tests/commands/test_import_awin_offers_e2e.py``.
"""

import pytest
from sqlalchemy import text

import tests.catalogue_e2e_support as support
from app.core.ids import new_id
from tests.catalogue_e2e_support import (
    EDITOR,
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
HASH = "b" * 64
INSERT_IMPORT = (
    "INSERT INTO offer_imports (id, provider, external_id, offer_id, source_hash,"
    " applied_source_hash, applied_content_hash, applied_at, source_changed_at)"
    " VALUES (:id, 'awin', '77', :offer, :new, :old, :old, now(),"
    " CASE WHEN :marked THEN now() END)"
)


async def import_row(engine, offer_id: str, *, marked: bool) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(INSERT_IMPORT),
            {
                "id": new_id(),
                "offer": offer_id,
                "new": HASH,
                "old": "a" * 64,
                "marked": marked,
            },
        )


async def make_offer(client, headers, refs: tuple[str, str] | None = None) -> str:
    category_id, merchant_id = refs or await support.create_references(client, headers)
    created = await client.post(
        OFFERS,
        json=offer_body(category_id, merchant_id, title=f"Offer {new_id()}"),
        headers=headers,
    )
    return str(created.json()["data"]["id"])


@pytest.fixture
async def world(engine, client):
    await seed_admin(engine, "editor", EDITOR)
    await seed_admin(engine, "reader", ["offers.read"])
    headers = as_user("editor")
    offer_id = await make_offer(client, headers)
    await import_row(engine, offer_id, marked=True)
    return headers, f"{OFFERS}/{offer_id}", offer_id


async def stored(engine):
    return await rows(
        engine,
        "SELECT source_hash, applied_source_hash, applied_content_hash, applied_at,"
        " source_changed_at FROM offer_imports",
    )


async def test_dismissing_clears_only_the_marker_and_audits_before_and_after(
    client, engine, world
):
    headers, url, offer_id = world
    token = await review_token(client, headers, url)
    before = (await stored(engine))[0]
    offers_before = await rows(engine, "SELECT * FROM offers")

    result = await client.post(
        f"{url}/dismiss-source-change", json={"review_token": token}, headers=headers
    )

    data = result.json()["data"]
    assert result.status_code == 200
    assert data["review_token"] == data["previous_review_token"] == token
    assert data["source_changed_at"] is None and data["import_provider"] == "awin"
    assert data["provenance"]["external_id"] == "77"
    after = (await stored(engine))[0]
    assert after[4] is None and before[4] is not None
    assert after[:4] == before[:4]  # hashes and applied_at are exactly as they were
    assert await rows(engine, "SELECT * FROM offers") == offers_before
    (audit,) = await audit_rows(engine, "admin.offers.source_change_dismissed")
    assert (audit.resource, audit.resource_id) == ("offers", offer_id)
    assert audit.metadata["before"]["source_changed_at"] is not None
    assert audit.metadata["after"]["source_changed_at"] is None
    assert audit.metadata["details"] == {"provider": "awin", "external_id": "77"}


async def test_dismissing_leaves_the_queue_for_a_published_offer(client, engine, world):
    headers, url, offer_id = world
    await support.approve(client, headers, url, "verify")
    published = await support.approve(client, headers, url, "publish")
    assert published.status_code == 200

    queue = await client.get(f"{OFFERS}?needs_review=true", headers=headers)
    assert [o["id"] for o in queue.json()["data"]["offers"]] == [offer_id]

    result = await client.post(
        f"{url}/dismiss-source-change",
        json={"review_token": await review_token(client, headers, url)},
        headers=headers,
    )

    assert result.status_code == 200
    queue = await client.get(f"{OFFERS}?needs_review=true", headers=headers)
    assert queue.json()["data"]["offers"] == []
    assert queue.json()["data"]["total_count"] == 0


async def test_a_second_dismiss_and_a_never_imported_offer_are_409_illegal_transition(
    client, engine, world
):
    headers, url, _ = world
    token = await review_token(client, headers, url)
    await client.post(
        f"{url}/dismiss-source-change", json={"review_token": token}, headers=headers
    )
    category = str((await rows(engine, "SELECT id FROM categories"))[0][0])
    merchant = str((await rows(engine, "SELECT id FROM merchants"))[0][0])
    plain = await make_offer(client, headers, (category, merchant))

    again = await client.post(
        f"{url}/dismiss-source-change", json={"review_token": token}, headers=headers
    )
    plain_url = f"{OFFERS}/{plain}"
    hand_made = await client.post(
        f"{plain_url}/dismiss-source-change",
        json={"review_token": await review_token(client, headers, plain_url)},
        headers=headers,
    )

    for result in (again, hand_made):
        assert (result.status_code, result.json()["code"]) == (
            409,
            "illegal_transition",
        )
    assert len(await audit_rows(engine, "admin.offers.source_change_dismissed")) == 1


async def test_a_stale_token_is_409_offer_changed_and_the_marker_stays(
    client, engine, world
):
    headers, url, _ = world
    stale = await review_token(client, headers, url)
    await client.patch(url, json={"summary": "Rewritten by somebody"}, headers=headers)
    before = await stored(engine)

    result = await client.post(
        f"{url}/dismiss-source-change", json={"review_token": stale}, headers=headers
    )

    assert (result.status_code, result.json()["code"]) == (409, "offer_changed")
    assert await stored(engine) == before
    assert await audit_rows(engine, "admin.offers.source_change_dismissed") == []


async def test_an_admin_without_offers_publish_is_403_and_nothing_changes(
    client, engine, world
):
    headers, url, _ = world
    token = await review_token(client, headers, url)
    before = await stored(engine)

    result = await client.post(
        f"{url}/dismiss-source-change",
        json={"review_token": token},
        headers=as_user("reader"),
    )

    assert result.status_code == 403
    assert await stored(engine) == before
