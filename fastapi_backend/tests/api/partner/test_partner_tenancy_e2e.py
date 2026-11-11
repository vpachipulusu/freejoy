"""Tenancy proofs on Postgres (ADR-013, FREEJ-58): one merchant's key reaches only that merchant's submissions.

A ref belongs to the merchant that sent it: ``(merchant_id, ref)`` is unique, so two merchants may use the same ref and
never see each other's. Another merchant's ref is, to the caller, exactly an unknown one: the same 404 body, the same
headers, found (or not) by one query that carries the merchant id.
"""

import asyncio
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import event, text
from sqlalchemy.exc import DBAPIError, NoResultFound
from sqlalchemy.ext.asyncio import AsyncEngine

import tests.api.partner.submissions_support as sub
import tests.catalogue_e2e_support as support
from app.core.config import settings
from app.core.database import PartnerSessionProvider
from app.partner import deps
from app.partner.submission_store import PartnerSubmissionStore
from tests.api.partner.submissions_support import (
    PARTNER_OFFERS,
    SITE,
    make_category,
    make_merchant,
    offer_body,
    partner_call,
    put_offer,
)
from tests.catalogue_e2e_support import rows

pytestmark = support.requires_database
database_url, engine, client = support.database_url, support.engine, support.client
partner_role, partner_world, admin = (
    sub.partner_role,
    sub.partner_world,
    sub.admin,
)
OTHER_SITE = "https://other.example.com"


async def two_merchants(
    client: AsyncClient, admin: dict[str, str]
) -> tuple[str, sub.Merchant, sub.Merchant]:
    category = await make_category(client, admin)
    first = await make_merchant(client, admin, "First", SITE)
    second = await make_merchant(client, admin, "Second", OTHER_SITE)
    return category, first, second


def body_for(category: str, merchant: sub.Merchant, **changes: Any) -> dict[str, Any]:
    return offer_body(category, url=f"{merchant.website}/free", **changes)


def shape(response: Any) -> tuple[int, dict[str, Any], dict[str, str]]:
    """Status, body and the headers that matter, with the per-request ones left out."""
    headers = {k: v for k, v in response.headers if k != "x-request-id"}
    return response.status, response.json(), headers


async def test_another_merchants_ref_reads_as_an_unknown_ref_not_a_403(
    client: AsyncClient, admin: dict[str, str], partner_world: None
) -> None:
    category, first, second = await two_merchants(client, admin)
    await put_offer(second.key, "theirs", body_for(category, second))

    foreign = await partner_call(first.key, "GET", f"{PARTNER_OFFERS}/theirs")
    unknown = await partner_call(first.key, "GET", f"{PARTNER_OFFERS}/nobody-has-this")

    assert foreign.status == 404
    assert shape(foreign) == shape(unknown)
    assert shape(foreign)[1] == {"success": False, "error": "Not Found"}


async def test_another_merchants_ref_cannot_be_ended_and_the_offer_is_untouched(
    client: AsyncClient, engine: AsyncEngine, admin: dict[str, str], partner_world: None
) -> None:
    category, first, second = await two_merchants(client, admin)
    await put_offer(second.key, "theirs", body_for(category, second))

    foreign = await partner_call(first.key, "POST", f"{PARTNER_OFFERS}/theirs/end")
    unknown = await partner_call(
        first.key, "POST", f"{PARTNER_OFFERS}/nobody-has-this/end"
    )

    assert foreign.status == 404
    assert shape(foreign) == shape(unknown)
    (offer,) = await rows(engine, "SELECT end_at FROM offers")
    assert offer.end_at is None
    (audits,) = await rows(
        engine, "SELECT count(*) FROM audit_logs WHERE action = 'partner.offers.end'"
    )
    assert audits[0] == 0


async def test_a_put_on_a_ref_another_merchant_uses_makes_its_own_submission_and_never_touches_theirs(
    client: AsyncClient, engine: AsyncEngine, admin: dict[str, str], partner_world: None
) -> None:
    category, first, second = await two_merchants(client, admin)
    await put_offer(second.key, "shared", body_for(category, second, title="Theirs"))

    mine = await put_offer(first.key, "shared", body_for(category, first, title="Mine"))
    theirs = await partner_call(second.key, "GET", f"{PARTNER_OFFERS}/shared")
    pending = await rows(
        engine,
        "SELECT count(*) FROM partner_submissions WHERE pending_hash IS NOT NULL",
    )

    assert (mine.status, mine.json()["data"]["outcome"]) == (201, "created")
    assert theirs.json()["data"]["title"] == "Theirs"
    assert theirs.json()["data"]["update_pending"] is False
    assert pending[0][0] == 0
    titles = {
        (r.merchant_id, r.title)
        for r in await rows(engine, "SELECT merchant_id, title FROM offers")
    }
    assert titles == {(first.id, "Mine"), (second.id, "Theirs")}


async def test_a_put_cannot_carry_a_link_to_another_merchants_site_even_for_a_ref_it_owns(
    client: AsyncClient, admin: dict[str, str], partner_world: None
) -> None:
    category, first, _second = await two_merchants(client, admin)

    response = await put_offer(
        first.key, "mine", offer_body(category, url=f"{OTHER_SITE}/steal")
    )

    assert (response.status, response.json()["code"]) == (422, "link_host_not_allowed")


async def test_each_list_has_only_that_merchants_submissions(
    client: AsyncClient, admin: dict[str, str], partner_world: None
) -> None:
    category, first, second = await two_merchants(client, admin)
    for ref in ("a", "b", "c"):
        await put_offer(first.key, ref, body_for(category, first, title=f"First {ref}"))
    for ref in ("x", "y"):
        await put_offer(
            second.key, ref, body_for(category, second, title=f"Second {ref}")
        )

    mine = (await partner_call(first.key, "GET", PARTNER_OFFERS)).json()["data"]
    theirs = (await partner_call(second.key, "GET", PARTNER_OFFERS)).json()["data"]

    assert sorted(o["ref"] for o in mine["offers"]) == ["a", "b", "c"]
    assert mine["total_count"] == 3
    assert sorted(o["ref"] for o in theirs["offers"]) == ["x", "y"]
    assert theirs["total_count"] == 2


async def test_the_audit_row_of_a_write_names_the_key_that_made_it(
    client: AsyncClient, engine: AsyncEngine, admin: dict[str, str], partner_world: None
) -> None:
    category, first, second = await two_merchants(client, admin)

    await put_offer(first.key, "a", body_for(category, first))
    await put_offer(second.key, "a", body_for(category, second))
    audits = await rows(
        engine, "SELECT metadata FROM audit_logs WHERE action = 'partner.offers.submit'"
    )

    assert sorted(
        (a.metadata["key_id"], a.metadata["merchant_id"]) for a in audits
    ) == sorted([(first.key_id, first.id), (second.key_id, second.id)])


# ------------------------------------------------------------- the store, asked about the wrong merchant


@pytest.fixture
async def store_world(
    database_url: str,
    partner_role: str,
    engine: AsyncEngine,
    client: AsyncClient,
    admin: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    partner_world: None,
) -> tuple[PartnerSessionProvider, sub.Merchant, sub.Merchant, str]:
    category, first, second = await two_merchants(client, admin)
    await put_offer(second.key, "theirs", body_for(category, second))
    provider = deps.get_partner_session_provider()
    assert isinstance(provider, PartnerSessionProvider)
    (offer,) = await rows(engine, "SELECT id FROM offers")
    monkeypatch.setattr(settings, "PARTNER_API_ENABLED", True)
    return provider, first, second, str(offer.id)


async def test_every_store_read_and_write_for_the_wrong_merchant_finds_and_changes_nothing(
    engine: AsyncEngine,
    store_world: tuple[PartnerSessionProvider, sub.Merchant, sub.Merchant, str],
) -> None:
    provider, first, second, offer_id = store_world
    before = await rows(
        engine,
        "SELECT o.title, o.end_at, l.destination_url, i.source_hash, s.content_hash FROM offers o "
        "JOIN affiliate_links l ON l.offer_id = o.id JOIN offer_imports i ON i.offer_id = o.id "
        "JOIN partner_submissions s ON s.offer_id = o.id",
    )
    link_id = (await rows(engine, "SELECT id FROM affiliate_links"))[0].id
    submission_id = (await rows(engine, "SELECT id FROM partner_submissions"))[0].id

    async with provider.session() as session:
        store = PartnerSubmissionStore(session)
        assert await store.find_submission(first.id, "theirs") is None
        assert await store.find_submission(first.id, "theirs", lock=True) is None
        assert await store.lock_offer(first.id, offer_id) is None
        assert await store.links(first.id, offer_id) == []
        assert await store.import_row(first.id, offer_id) is None
        assert await store.view(first.id, "theirs") is None
        assert await store.list_views(first.id, 10, 0) == ([], 0)
        assert await store.end_offer(first.id, offer_id) is False
        with pytest.raises(NoResultFound):
            await store.update_offer(first.id, offer_id, {"title": "Hijacked"})
        with pytest.raises(NoResultFound):
            await store.update_link(first.id, offer_id, link_id, f"{SITE}/hijack")
        await store.update_import(first.id, offer_id, {"source_hash": "f" * 64})
        await store.update_submission(
            first.id, submission_id, {"content_hash": "f" * 64}
        )
        assert await store.count_open(first.id) == 0
        # An insert whose source is the offer finds no row of the wrong merchant, so nothing is inserted.
        with pytest.raises(NoResultFound):
            await store.insert_link(first.id, offer_id, f"{SITE}/hijack")
        with pytest.raises(NoResultFound):
            await store.insert_import(
                first.id,
                offer_id=offer_id,
                external_id="x",
                digest="f" * 64,
                applied_content_hash="f" * 64,
            )
        with pytest.raises(NoResultFound):
            await store.insert_submission(
                first.id,
                submission_id="s" * 24,
                ref="hijack",
                offer_id=offer_id,
                content_hash="f" * 64,
                key_id="k" * 24,
            )
        # An audit row is not about a row of another merchant's: it is attributed to the caller, who is named in it.
        await store.audit(
            first.id,
            action="partner.offers.submit",
            offer_id=offer_id,
            metadata={"outcome": "refused"},
        )
        await session.commit()

    after = await rows(
        engine,
        "SELECT o.title, o.end_at, l.destination_url, i.source_hash, s.content_hash FROM offers o "
        "JOIN affiliate_links l ON l.offer_id = o.id JOIN offer_imports i ON i.offer_id = o.id "
        "JOIN partner_submissions s ON s.offer_id = o.id",
    )
    assert [tuple(r) for r in after] == [tuple(r) for r in before]
    (audit,) = await rows(
        engine, "SELECT metadata FROM audit_logs WHERE metadata->>'outcome' = 'refused'"
    )
    assert audit.metadata["merchant_id"] == first.id
    assert (await rows(engine, "SELECT count(*) FROM affiliate_links"))[0][0] == 1
    assert (await rows(engine, "SELECT count(*) FROM offer_imports"))[0][0] == 1
    assert (await rows(engine, "SELECT count(*) FROM partner_submissions"))[0][0] == 1
    # and the right merchant does find it
    async with provider.session() as session:
        right = PartnerSubmissionStore(session)
        assert (await right.find_submission(second.id, "theirs")) is not None
        assert await right.count_open(second.id) == 1


async def test_a_deleted_or_inactive_merchant_cannot_submit(
    client: AsyncClient, engine: AsyncEngine, admin: dict[str, str], partner_world: None
) -> None:
    category, first, _ = await two_merchants(client, admin)
    await put_offer(first.key, "a", body_for(category, first))
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE merchants SET active = false WHERE id = :i"), {"i": first.id}
        )

    response = await put_offer(first.key, "b", body_for(category, first))

    assert response.status == 401


# ----------------------------------------------------------------------------- locks, connections and secrets


async def test_the_write_locks_do_not_block_a_visitors_click_on_the_offer(
    engine: AsyncEngine,
    store_world: tuple[PartnerSessionProvider, sub.Merchant, sub.Merchant, str],
) -> None:
    provider, _first, second, offer_id = store_world

    async with provider.session() as session:
        store = PartnerSubmissionStore(session)
        assert await store.lock_offer(second.id, offer_id) is not None
        assert await store.find_submission(second.id, "theirs", lock=True) is not None
        # Another connection records a click, which takes a FOR KEY SHARE lock on the offer row for its foreign
        # key. FOR NO KEY UPDATE does not conflict with it; FOR UPDATE would wait, and time out here.
        async with engine.begin() as connection:
            await connection.execute(text("SET LOCAL lock_timeout = '1s'"))
            await connection.execute(
                text(
                    "INSERT INTO click_events (id, offer_id, occurred_at, user_agent_class) "
                    "VALUES ('click-while-locked', :offer, now(), 'DESKTOP')"
                ),
                {"offer": offer_id},
            )
        await session.rollback()

    (clicks,) = await rows(engine, "SELECT count(*) FROM click_events")
    assert clicks[0] == 1


async def test_the_same_body_again_is_answered_while_another_transaction_holds_the_offer_locked(
    client: AsyncClient,
    engine: AsyncEngine,
    admin: dict[str, str],
    partner_world: None,
) -> None:
    category, first, _second = await two_merchants(client, admin)
    body = body_for(category, first)
    await put_offer(first.key, "a", body)

    async with engine.begin() as holder:
        await holder.execute(text("SELECT id FROM offers FOR UPDATE"))
        answered = await asyncio.wait_for(put_offer(first.key, "a", body), timeout=10)

    assert answered.json()["data"]["outcome"] == "unchanged"


async def test_a_request_opens_exactly_the_two_connections_it_needs(
    client: AsyncClient,
    admin: dict[str, str],
    partner_world: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    category, first, _second = await two_merchants(client, admin)
    await put_offer(first.key, "a", body_for(category, first))
    provider = deps.get_partner_session_provider()
    assert isinstance(provider, PartnerSessionProvider)
    engine = provider._session_maker().kw["bind"].sync_engine
    connects: list[object] = []
    event.listen(engine, "connect", lambda *_: connects.append(1))

    for _ in range(5):
        await partner_call(first.key, "GET", f"{PARTNER_OFFERS}/a")

    # The engine does not pool: one connection for the key check and one for the route, per request, and no more.
    assert len(connects) == 2 * 5


async def test_a_database_error_on_the_partner_connection_shows_no_parameter_value(
    store_world: tuple[PartnerSessionProvider, sub.Merchant, sub.Merchant, str],
) -> None:
    provider, _first, _second, _offer_id = store_world
    marker = "marker-value-that-must-not-appear"

    async with provider.session() as session:
        with pytest.raises(DBAPIError) as caught:
            await session.execute(
                text("UPDATE offers SET title = :title WHERE id = :id"),
                {"title": marker, "id": marker},
            )
            await session.execute(
                text("DELETE FROM offers WHERE id = :id"), {"id": marker}
            )
        await session.rollback()

    assert marker not in str(caught.value)
    assert "hidden due to hide_parameters=True" in str(caught.value)
