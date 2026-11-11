"""End to end on Postgres: the cases the FREEJ-58 review round added (live offers, worst-case size, idempotency over
HTTP, the admin refusals and the migration's functions).

Real database, the real restricted role, the real app, like ``test_partner_submissions_e2e.py``.
"""

import json
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
    put_offer,
)
from tests.catalogue_e2e_support import approve, rows

pytestmark = support.requires_database
database_url, engine, client = support.database_url, support.engine, support.client
partner_role, partner_world, admin = sub.partner_role, sub.partner_world, sub.admin

EMOJI = "\U0001f600"  # four bytes in UTF-8, the widest a character gets


def in_days(days: int) -> str:
    return (datetime.now(UTC) + timedelta(days=days)).isoformat()


async def live_offer(
    client: AsyncClient, admin: dict[str, str], **changes: Any
) -> tuple[str, sub.Merchant, str]:
    """A partner offer an editor published; returns (category, merchant, offer url). The ref is ``sku-1``."""
    category = await make_category(client, admin)
    merchant = await make_merchant(client, admin)
    await put_offer(merchant.key, "sku-1", offer_body(category, **changes))
    url = f"{OFFERS}/{await sub.offer_id_of(client, admin, 'Free')}"
    await approve(client, admin, url, "verify")
    published = await approve(
        client, admin, url, "publish", commercial_basis="EDITORIAL"
    )
    assert published.status_code == 200
    return category, merchant, url


# ------------------------------------------------------------------------------------------------ H2: end


async def test_ending_a_live_offer_discards_a_waiting_update_so_nothing_can_reopen_it(
    client: AsyncClient, engine: AsyncEngine, admin: dict[str, str], partner_world: None
) -> None:
    category, merchant, url = await live_offer(client, admin)
    waiting = await put_offer(
        merchant.key,
        "sku-1",
        offer_body(category, title="Later title", end_at=in_days(30)),
    )
    assert waiting.json()["data"]["outcome"] == "pending_update"

    ended = await partner_call(merchant.key, "POST", f"{PARTNER_OFFERS}/sku-1/end")
    (offer,) = await rows(
        engine, "SELECT status, title, end_at <= now() AS over FROM offers"
    )
    (submission,) = await rows(
        engine, "SELECT pending_hash, pending_payload FROM partner_submissions"
    )

    assert ended.json()["data"]["update_pending"] is False
    assert ended.json()["data"]["status"] == "ended"
    assert (offer.status, offer.title, offer.over) == (
        "PUBLISHED",
        "Free sample box",
        True,
    )
    assert (submission.pending_hash, submission.pending_payload) == (None, None)
    detail = (await client.get(url, headers=admin)).json()["data"]
    assert detail["partner_submission"]["pending"] is None


async def test_an_update_sent_after_the_end_without_a_future_end_cannot_reopen_the_offer_when_applied(
    client: AsyncClient, engine: AsyncEngine, admin: dict[str, str], partner_world: None
) -> None:
    category, merchant, url = await live_offer(client, admin)
    await partner_call(merchant.key, "POST", f"{PARTNER_OFFERS}/sku-1/end")
    (ended,) = await rows(engine, "SELECT end_at FROM offers")

    late = await put_offer(
        merchant.key, "sku-1", offer_body(category, title="Later title", end_at=None)
    )
    applied = await apply_waiting_update(client, admin, url)
    (after,) = await rows(engine, "SELECT title, end_at FROM offers")

    assert late.json()["data"]["outcome"] == "pending_update"
    assert applied.status_code == 200
    assert after.title == "Later title"
    assert after.end_at == ended.end_at


async def test_an_update_sent_after_the_end_with_a_future_end_is_a_reopening_the_editor_approves(
    client: AsyncClient, engine: AsyncEngine, admin: dict[str, str], partner_world: None
) -> None:
    category, merchant, url = await live_offer(client, admin)
    await partner_call(merchant.key, "POST", f"{PARTNER_OFFERS}/sku-1/end")

    await put_offer(merchant.key, "sku-1", offer_body(category, end_at=in_days(30)))
    applied = await apply_waiting_update(client, admin, url)
    (after,) = await rows(engine, "SELECT end_at > now() AS open FROM offers")

    assert applied.status_code == 200
    assert after.open is True


async def apply_waiting_update(
    client: AsyncClient, admin: dict[str, str], url: str
) -> Any:
    detail = (await client.get(url, headers=admin)).json()["data"]
    return await client.post(
        f"{url}/apply-partner-update",
        json={
            "review_token": detail["review_token"],
            "pending_hash": detail["partner_submission"]["pending"]["hash"],
        },
        headers=admin,
    )


# ------------------------------------------------------------------------------------- M1: worst-case size


async def test_the_widest_offer_the_rules_allow_is_accepted_as_a_pending_update(
    client: AsyncClient, engine: AsyncEngine, admin: dict[str, str], partner_world: None
) -> None:
    category, merchant, _ = await live_offer(
        client, admin, offer_type="FREE_TRIAL", subscription_terms="Cancel any time"
    )
    widest = offer_body(
        category,
        title=EMOJI * 120,
        summary=EMOJI * 200,
        description=[EMOJI * 600] * 5,
        offer_type="FREE_TRIAL",
        subscription_terms=EMOJI * 600,
        eligibility=EMOJI * 400,
        availability=EMOJI * 300,
        steps=[{"title": EMOJI * 80, "detail": EMOJI * 300}] * 6,
        url=f"{SITE}/{'a' * 1900}",
    )

    response = await put_offer(merchant.key, "sku-1", widest)
    (size,) = await rows(
        engine,
        "SELECT octet_length(pending_payload::text) AS bytes FROM partner_submissions",
    )

    assert response.status == 200, response.body
    assert response.json()["data"]["outcome"] == "pending_update"
    assert size.bytes <= 49_152


# --------------------------------------------------------------------------------- M16: idempotency over HTTP


async def test_the_same_offer_in_another_key_order_is_unchanged(
    client: AsyncClient, admin: dict[str, str], partner_world: None
) -> None:
    category = await make_category(client, admin)
    merchant = await make_merchant(client, admin)
    body = offer_body(category)
    await put_offer(merchant.key, "sku-1", body)

    reordered = dict(reversed(list(body.items())))
    again = await partner_call(
        merchant.key,
        "PUT",
        f"{PARTNER_OFFERS}/sku-1",
        raw=json.dumps(reordered).encode(),
    )

    assert again.json()["data"]["outcome"] == "unchanged"


async def test_leaving_out_the_description_and_sending_an_empty_one_are_the_same_offer(
    client: AsyncClient, admin: dict[str, str], partner_world: None
) -> None:
    category = await make_category(client, admin)
    merchant = await make_merchant(client, admin)
    await put_offer(merchant.key, "sku-1", offer_body(category, description=[]))

    without = {k: v for k, v in offer_body(category).items() if k != "description"}
    again = await put_offer(merchant.key, "sku-1", without)

    assert again.json()["data"]["outcome"] == "unchanged"


async def test_the_same_instant_written_with_an_offset_is_the_same_offer(
    client: AsyncClient, admin: dict[str, str], partner_world: None
) -> None:
    category = await make_category(client, admin)
    merchant = await make_merchant(client, admin)
    end = datetime.now(UTC).replace(microsecond=0) + timedelta(days=30)
    in_utc = end.strftime("%Y-%m-%dT%H:%M:%SZ")
    one_hour_ahead = (end + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S+01:00")
    await put_offer(merchant.key, "sku-1", offer_body(category, end_at=in_utc))

    again = await put_offer(
        merchant.key, "sku-1", offer_body(category, end_at=one_hour_ahead)
    )

    assert again.json()["data"]["outcome"] == "unchanged"


# ------------------------------------------------------------------------------------------- the editor side


async def test_publishing_a_partner_offer_stores_the_basis_the_editor_chose(
    client: AsyncClient, engine: AsyncEngine, admin: dict[str, str], partner_world: None
) -> None:
    category = await make_category(client, admin)
    merchant = await make_merchant(client, admin)
    await put_offer(merchant.key, "sku-1", offer_body(category))
    url = f"{OFFERS}/{await sub.offer_id_of(client, admin, 'Free')}"
    await approve(client, admin, url, "verify")
    (before,) = await rows(engine, "SELECT commercial_basis FROM offers")

    await approve(client, admin, url, "publish", commercial_basis="AFFILIATE")
    (after,) = await rows(engine, "SELECT commercial_basis FROM offers")

    assert before.commercial_basis == "SPONSORED"
    assert after.commercial_basis == "AFFILIATE"


@pytest.mark.parametrize(
    ("basis", "earns_commission", "sponsored"),
    [
        ("AFFILIATE", True, False),
        ("EDITORIAL", False, False),
        ("SPONSORED", False, True),
    ],
)
async def test_a_published_partner_offer_with_a_direct_link_discloses_what_the_editor_stated(
    client: AsyncClient,
    admin: dict[str, str],
    partner_world: None,
    basis: str,
    earns_commission: bool,
    sponsored: bool,
) -> None:
    category = await make_category(client, admin)
    merchant = await make_merchant(client, admin)
    await put_offer(merchant.key, "sku-1", offer_body(category))
    url = f"{OFFERS}/{await sub.offer_id_of(client, admin, 'Free')}"
    await approve(client, admin, url, "verify")
    unconfirmed = await approve(client, admin, url, "publish")
    assert (
        unconfirmed.status_code == 422
    )  # the placeholder can never be published as it is

    await approve(client, admin, url, "publish", commercial_basis=basis)
    slug = (await client.get(url, headers=admin)).json()["data"]["slug"]
    public = (await client.get(f"/v1/catalogue/offers/{slug}")).json()["data"]

    assert public["earns_commission"] is earns_commission
    assert public["sponsored"] is sponsored


async def test_the_slug_follows_the_title_an_editor_published_for_a_never_published_draft(
    client: AsyncClient, engine: AsyncEngine, admin: dict[str, str], partner_world: None
) -> None:
    category = await make_category(client, admin)
    merchant = await make_merchant(client, admin)
    await put_offer(
        merchant.key, "sku-1", offer_body(category, title="Keyword stuffed words")
    )
    url = f"{OFFERS}/{await sub.offer_id_of(client, admin, 'Keyword')}"
    (first,) = await rows(engine, "SELECT slug FROM offers")
    await client.patch(url, json={"title": "Plain editor title"}, headers=admin)
    await approve(client, admin, url, "verify")

    await approve(client, admin, url, "publish", commercial_basis="EDITORIAL")
    (published,) = await rows(engine, "SELECT slug FROM offers")
    await put_offer(merchant.key, "sku-1", offer_body(category, title="A third title"))
    (after_update,) = await rows(engine, "SELECT slug FROM offers")

    assert "keyword" in first.slug
    assert published.slug.startswith("plain-editor-title-")
    assert after_update.slug == published.slug


async def test_an_editor_cannot_move_a_retailer_submission_to_another_merchant(
    client: AsyncClient, engine: AsyncEngine, admin: dict[str, str], partner_world: None
) -> None:
    category = await make_category(client, admin)
    merchant = await make_merchant(client, admin)
    other = await make_merchant(client, admin, "Other shop", "https://other.example")
    await put_offer(merchant.key, "sku-1", offer_body(category))
    url = f"{OFFERS}/{await sub.offer_id_of(client, admin, 'Free')}"

    refused = await client.patch(url, json={"merchant_id": other.id}, headers=admin)
    view = await partner_call(merchant.key, "GET", f"{PARTNER_OFFERS}/sku-1")
    (offer,) = await rows(engine, "SELECT merchant_id FROM offers")

    assert refused.status_code == 422
    assert "another merchant" in refused.text
    assert view.status == 200
    assert offer.merchant_id == merchant.id


# ------------------------------------------------------------------------------- L6: the migration's objects


async def test_the_migration_functions_pin_their_search_path_run_with_invoker_rights_and_go_with_the_downgrade(
    database_url: str, engine: AsyncEngine
) -> None:
    names = await _function_names(engine)
    assert len(names) == 8
    async with engine.connect() as connection:
        found = (
            await connection.execute(
                text(
                    "SELECT proname, prosecdef, proconfig FROM pg_proc "
                    "WHERE proname = ANY(:names) ORDER BY proname"
                ),
                {"names": names},
            )
        ).all()
    assert [row.proname for row in found] == sorted(names)
    for row in found:
        assert row.prosecdef is False, row.proname
        assert row.proconfig == ["search_path=pg_catalog, public"], row.proname

    support._migrate(database_url, "downgrade", "0007")
    try:
        assert await _function_names_present(engine, names) == []
        assert await _trigger_names(engine) == []
    finally:
        support._migrate(database_url, "upgrade", "head")
    assert sorted(await _function_names_present(engine, names)) == sorted(names)
    assert len(await _trigger_names(engine)) == 8


async def _function_names(engine: AsyncEngine) -> list[str]:
    import importlib

    migration = importlib.import_module(
        "alembic_migrations.versions.0008_partner_submissions"
    )
    return [name for name in migration.FUNCTIONS]


async def _function_names_present(engine: AsyncEngine, names: list[str]) -> list[str]:
    async with engine.connect() as connection:
        result = await connection.execute(
            text("SELECT proname FROM pg_proc WHERE proname = ANY(:names)"),
            {"names": names},
        )
        return [row[0] for row in result]


async def _trigger_names(engine: AsyncEngine) -> list[str]:
    async with engine.connect() as connection:
        result = await connection.execute(
            text("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal")
        )
        return [row[0] for row in result]
