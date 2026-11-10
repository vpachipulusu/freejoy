"""The per-merchant click report queries against a real Postgres.

Runs only with ``TEST_DATABASE_URL`` (see ``tests/catalogue_e2e_support.py``). The rules under test
are in ``app/repositories/merchant_clicks.py``: clicks are grouped in SQL per offer per UTC day, the
range is inclusive of both days, another merchant's clicks never count, ``BOT`` rows are left out
(and counted apart), offers with no clicks still appear and the number of offers is capped.
"""

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

import tests.catalogue_e2e_support as support
from app.core.ids import new_id
from app.repositories.merchant_clicks import MerchantClickRepository
from app.services.merchant_click_report import MerchantClickReportService
from tests.api.test_catalogue_e2e import add_category, add_merchant, add_offer

pytestmark = support.requires_database
database_url, engine = support.database_url, support.engine

START = datetime(2026, 3, 10, tzinfo=UTC)
END = datetime(2026, 3, 12, tzinfo=UTC)  # exclusive: the range is 10 and 11 March


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with async_sessionmaker(engine, expire_on_commit=False)() as opened:
        yield opened


async def click(
    engine: AsyncEngine,
    offer_id: str,
    at: datetime,
    user_agent_class: str | None = "DESKTOP",
) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO click_events (id, offer_id, occurred_at, user_agent_class) "
                "VALUES (:id, :offer, :at, :ua)"
            ),
            {"id": new_id(), "offer": offer_id, "at": at, "ua": user_agent_class},
        )


@pytest.fixture
async def world(engine: AsyncEngine) -> dict[str, str]:
    category = await add_category(engine, "beauty")
    acme = await add_merchant(engine, "acme")
    other = await add_merchant(engine, "other")
    return {
        "category": category,
        "acme": acme,
        "other": other,
        "soap": await add_offer(engine, category, acme, "soap", title="Soap"),
        "balm": await add_offer(engine, category, acme, "balm", title="Balm"),
        "foreign": await add_offer(engine, category, other, "foreign"),
    }


async def test_clicks_are_grouped_per_offer_per_utc_day(engine, session, world):
    await click(engine, world["soap"], datetime(2026, 3, 10, 9, 0, tzinfo=UTC))
    await click(engine, world["soap"], datetime(2026, 3, 10, 18, 30, tzinfo=UTC))
    await click(engine, world["soap"], datetime(2026, 3, 11, 9, 0, tzinfo=UTC))
    await click(engine, world["balm"], datetime(2026, 3, 11, 9, 0, tzinfo=UTC))

    days = await MerchantClickRepository(session).daily(
        [world["soap"], world["balm"]], START, END
    )

    assert sorted((d.offer_id, d.day, d.clicks) for d in days) == sorted(
        [
            (world["soap"], date(2026, 3, 10), 2),
            (world["soap"], date(2026, 3, 11), 1),
            (world["balm"], date(2026, 3, 11), 1),
        ]
    )


async def test_the_day_boundaries_are_midnight_utc(engine, session, world):
    # 23:59:59 on the 9th is outside, 00:00 on the 10th and 23:59:59 on the 11th are inside,
    # 00:00 on the 12th is outside (the end of the range is exclusive).
    await click(engine, world["soap"], datetime(2026, 3, 9, 23, 59, 59, tzinfo=UTC))
    await click(engine, world["soap"], datetime(2026, 3, 10, 0, 0, 0, tzinfo=UTC))
    await click(engine, world["soap"], datetime(2026, 3, 11, 23, 59, 59, tzinfo=UTC))
    await click(engine, world["soap"], datetime(2026, 3, 12, 0, 0, 0, tzinfo=UTC))

    days = await MerchantClickRepository(session).daily([world["soap"]], START, END)

    assert [(d.day, d.clicks) for d in days] == [
        (date(2026, 3, 10), 1),
        (date(2026, 3, 11), 1),
    ]


async def test_a_click_is_dated_by_its_utc_day_whatever_the_session_time_zone(
    engine, session, world
):
    await click(engine, world["soap"], datetime(2026, 3, 10, 23, 30, tzinfo=UTC))
    await session.execute(text("SET TIME ZONE 'Pacific/Auckland'"))

    days = await MerchantClickRepository(session).daily([world["soap"]], START, END)

    assert [(d.day, d.clicks) for d in days] == [(date(2026, 3, 10), 1)]


async def test_another_merchants_clicks_are_not_counted(engine, session, world):
    await click(engine, world["soap"], datetime(2026, 3, 10, 9, 0, tzinfo=UTC))
    await click(engine, world["foreign"], datetime(2026, 3, 10, 9, 0, tzinfo=UTC))
    repo = MerchantClickRepository(session)

    totals = await repo.totals(world["acme"], START, END)
    page = await repo.offers(world["acme"], START, END, limit=10)

    assert totals.clicks == 1
    assert [offer.offer_id for offer in page.offers] == [world["soap"], world["balm"]]


async def test_bot_rows_are_excluded_and_counted_apart(engine, session, world):
    at = datetime(2026, 3, 10, 9, 0, tzinfo=UTC)
    for user_agent_class in ("DESKTOP", "MOBILE", "TABLET", "OTHER", None):
        await click(engine, world["soap"], at, user_agent_class)
    await click(engine, world["soap"], at, "BOT")
    await click(engine, world["balm"], at, "BOT")
    repo = MerchantClickRepository(session)

    totals = await repo.totals(world["acme"], START, END)
    page = await repo.offers(world["acme"], START, END, limit=10)
    days = await repo.daily([world["soap"], world["balm"]], START, END)

    assert (totals.clicks, totals.bot_clicks) == (5, 2)
    assert {offer.offer_id: offer.clicks for offer in page.offers} == {
        world["soap"]: 5,
        world["balm"]: 0,
    }
    assert [(d.offer_id, d.clicks) for d in days] == [(world["soap"], 5)]


async def test_an_offer_without_clicks_in_the_range_still_appears_with_zero(
    engine, session, world
):
    await click(engine, world["soap"], datetime(2026, 3, 10, 9, 0, tzinfo=UTC))
    await click(engine, world["balm"], datetime(2026, 3, 20, 9, 0, tzinfo=UTC))

    page = await MerchantClickRepository(session).offers(
        world["acme"], START, END, limit=10
    )

    assert [(o.slug, o.clicks) for o in page.offers] == [("soap", 1), ("balm", 0)]
    assert page.total_offers == 2


async def test_draft_and_archived_offers_appear_only_when_they_had_clicks(
    engine, session, world
):
    for slug, status in (("draft", "DRAFT"), ("archived", "ARCHIVED")):
        await add_offer(
            engine,
            world["category"],
            world["acme"],
            slug,
            status=status,
            last_verified_at=None,
        )
    busy = await add_offer(
        engine,
        world["category"],
        world["acme"],
        "was-live",
        status="ARCHIVED",
        last_verified_at=None,
    )
    await click(engine, busy, datetime(2026, 3, 10, 9, 0, tzinfo=UTC))
    await add_offer(engine, world["category"], world["acme"], "gone", status="EXPIRED")

    page = await MerchantClickRepository(session).offers(
        world["acme"], START, END, limit=10
    )

    assert {o.slug for o in page.offers} == {"was-live", "soap", "balm", "gone"}
    assert page.total_offers == 4


async def test_offers_are_capped_busiest_first_and_the_full_count_is_reported(
    engine, session, world
):
    third = await add_offer(
        engine, world["category"], world["acme"], "cream", title="Cream"
    )
    at = datetime(2026, 3, 10, 9, 0, tzinfo=UTC)
    await click(engine, world["balm"], at)
    await click(engine, world["balm"], at)
    await click(engine, third, at)

    page = await MerchantClickRepository(session).offers(
        world["acme"], START, END, limit=2
    )

    assert [o.slug for o in page.offers] == ["balm", "cream"]
    assert page.total_offers == 3


async def test_an_unknown_merchant_has_no_offers_and_no_record(session, world):
    repo = MerchantClickRepository(session)

    page = await repo.offers("missing", START, END, limit=10)

    assert await repo.get_merchant("missing") is None
    assert (page.offers, page.total_offers) == ([], 0)
    found = await repo.get_merchant(world["acme"])
    assert found is not None
    assert found.slug == "acme"


async def test_the_service_report_costs_four_statements_however_many_offers(
    engine, session, world
):
    third = await add_offer(engine, world["category"], world["acme"], "cream")
    at = datetime(2026, 3, 10, 9, 0, tzinfo=UTC)
    for offer in (world["soap"], world["balm"], third):
        await click(engine, offer, at)
        await click(engine, offer, at + timedelta(days=1))
    statements: list[str] = []

    def record(conn, cursor, statement, *rest):  # type: ignore[no-untyped-def]
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", record)
    try:
        report = await MerchantClickReportService(
            MerchantClickRepository(session)
        ).report(world["acme"], date(2026, 3, 10), date(2026, 3, 11))
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", record)

    # merchant lookup, totals, offers, per-day counts
    assert len(statements) == 4
    assert [offer.total_clicks for offer in report.offers] == [2, 2, 2]
    assert report.total_clicks == 6


async def test_equal_counts_are_ordered_by_title_then_id(engine, session, world):
    # Inserted out of title order, with equal counts, and two offers sharing a title.
    zed = await add_offer(engine, world["category"], world["acme"], "zed", title="Aaa")
    amy = await add_offer(engine, world["category"], world["acme"], "amy", title="Aaa")
    at = datetime(2026, 3, 10, 9, 0, tzinfo=UTC)
    for offer in (world["soap"], world["balm"], zed, amy):
        await click(engine, offer, at)

    page = await MerchantClickRepository(session).offers(
        world["acme"], START, END, limit=10
    )

    first, second = sorted([zed, amy])
    assert [o.offer_id for o in page.offers] == [
        first,
        second,
        world["balm"],  # "Balm"
        world["soap"],  # "Soap"
    ]


async def test_the_merchant_total_counts_an_offer_the_limit_cut(engine, session, world):
    at = datetime(2026, 3, 10, 9, 0, tzinfo=UTC)
    for _ in range(3):
        await click(engine, world["soap"], at)
    await click(engine, world["balm"], at)
    repo = MerchantClickRepository(session)

    page = await repo.offers(world["acme"], START, END, limit=1)
    totals = await repo.totals(world["acme"], START, END)

    assert [o.slug for o in page.offers] == ["soap"]
    assert page.total_offers == 2
    assert totals.clicks == 4
