"""The offer health command against a real Postgres: expiry, audit rows, locking and target queries.

Same contract as ``test_make_admin_e2e.py``: runs only when ``TEST_DATABASE_URL`` points at a
throwaway local ``*_test`` database, which it rebuilds with the real Alembic migrations. Nothing
here opens a network connection: the reachability step is only exercised up to the target query.
"""

import asyncio
import os
import subprocess
import sys
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.core.database import build_engine
from app.core.ids import new_id
from app.repositories.audit import AuditRepository
from app.repositories.offer_health import OfferHealthRepository
from app.services.audit import AuditService
from app.services.offer_expiry import ExpiryFailed, expire_ended_offers
from app.services.offer_reachability import TargetKind, collect_targets
from tests.alembic_support import run_alembic

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
BACKEND_ROOT = Path(__file__).resolve().parents[2]
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
VERIFIED = NOW - timedelta(days=3)

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="TEST_DATABASE_URL is not set"
)


def _migrate(url: str, *args: str) -> None:
    run_alembic(url, *args)


@pytest.fixture(scope="module")
def database_url() -> Iterator[str]:
    assert TEST_DATABASE_URL is not None
    target = make_url(TEST_DATABASE_URL)
    assert (target.database or "").endswith("_test"), "database must end in _test"
    assert target.host in LOCAL_HOSTS, "refusing to rebuild a non-local database"
    _migrate(TEST_DATABASE_URL, "downgrade", "base")
    _migrate(TEST_DATABASE_URL, "upgrade", "head")
    yield TEST_DATABASE_URL


@pytest.fixture
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    built = build_engine(database_url)
    async with built.begin() as connection:
        await connection.execute(
            text("TRUNCATE audit_logs, offers, categories, merchants CASCADE")
        )
    yield built
    await built.dispose()


async def add_references(
    engine: AsyncEngine, website: str | None = "https://www.shop.example"
) -> tuple[str, str]:
    category_id, merchant_id = new_id(), new_id()
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO categories (id, name, slug, short_name, description, intro, tint)"
                " VALUES (:id, 'Home', :slug, 'Home', 'd', 'i', 'sage')"
            ),
            {"id": category_id, "slug": f"home-{category_id}"},
        )
        await connection.execute(
            text(
                "INSERT INTO merchants (id, name, slug, website) VALUES (:id, 'Acme', :slug, :site)"
            ),
            {"id": merchant_id, "slug": f"acme-{merchant_id}", "site": website},
        )
    return category_id, merchant_id


async def add_offer(
    engine: AsyncEngine,
    refs: tuple[str, str],
    status: str,
    end_at: datetime | None,
    **extra: Any,
) -> str:
    offer_id = new_id()
    values = {
        "id": offer_id,
        "title": "t",
        "slug": f"offer-{offer_id}",
        "summary": "s",
        "category_id": refs[0],
        "merchant_id": refs[1],
        "offer_type": "FREE_SAMPLE",
        "commercial_basis": "EDITORIAL",
        "image_tint": "mint",
        "image_label": "l",
        "image_alt": "a",
        "postage_kind": "FREE",
        "eligibility": "e",
        "availability": "a",
        "status": status,
        "end_at": end_at,
        "last_verified_at": VERIFIED,
        **extra,
    }
    columns = ", ".join(values)
    params = ", ".join(f":{name}" for name in values)
    async with engine.begin() as connection:
        await connection.execute(
            text(f"INSERT INTO offers ({columns}) VALUES ({params})"), values
        )
    return offer_id


async def add_link(
    engine: AsyncEngine,
    offer_id: str,
    provider: str,
    url: str,
    *,
    active: bool = True,
) -> str:
    link_id = new_id()
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO affiliate_links (id, offer_id, provider, destination_url, active)"
                " VALUES (:id, :offer, :provider, :url, :active)"
            ),
            {
                "id": link_id,
                "offer": offer_id,
                "provider": provider,
                "url": url,
                "active": active,
            },
        )
    return link_id


async def rows(engine: AsyncEngine, sql: str, **params: Any) -> list[Any]:
    async with engine.connect() as connection:
        return list((await connection.execute(text(sql), params)).all())


async def statuses(engine: AsyncEngine) -> dict[str, str]:
    return {
        row[0]: row[1] for row in await rows(engine, "SELECT id, status FROM offers")
    }


async def expire(
    engine: AsyncEngine, *, batch_size: int = 200, auditor: Any = None
) -> int:
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        return await expire_ended_offers(
            OfferHealthRepository(session),
            session,
            auditor or AuditService(AuditRepository(session)),
            NOW,
            batch_size=batch_size,
        )


async def test_only_published_offers_that_have_ended_are_expired(
    engine: AsyncEngine,
) -> None:
    refs = await add_references(engine)
    past = await add_offer(engine, refs, "PUBLISHED", NOW - timedelta(seconds=1))
    exactly_now = await add_offer(engine, refs, "PUBLISHED", NOW)
    future = await add_offer(engine, refs, "PUBLISHED", NOW + timedelta(seconds=1))
    no_end = await add_offer(engine, refs, "PUBLISHED", None)
    draft = await add_offer(engine, refs, "DRAFT", NOW - timedelta(days=1))
    expired = await add_offer(engine, refs, "EXPIRED", NOW - timedelta(days=1))
    archived = await add_offer(engine, refs, "ARCHIVED", NOW - timedelta(days=1))

    count = await expire(engine)

    assert count == 2
    assert await statuses(engine) == {
        past: "EXPIRED",
        exactly_now: "EXPIRED",
        future: "PUBLISHED",
        no_end: "PUBLISHED",
        draft: "DRAFT",
        expired: "EXPIRED",
        archived: "ARCHIVED",
    }


async def test_each_expiry_writes_an_audit_row_with_no_user_and_the_command_as_actor(
    engine: AsyncEngine,
) -> None:
    refs = await add_references(engine)
    offer_id = await add_offer(engine, refs, "PUBLISHED", NOW - timedelta(hours=2))

    await expire(engine)

    audit = await rows(
        engine,
        "SELECT user_id, organization_id, action, resource, resource_id, metadata"
        " FROM audit_logs",
    )
    assert len(audit) == 1
    user_id, organization_id, action, resource, resource_id, metadata = audit[0]
    assert user_id is None and organization_id is None
    assert (action, resource, resource_id) == (
        "admin.offers.expire",
        "offers",
        offer_id,
    )
    assert metadata["actor"] == "commands.check_offer_health"
    assert metadata["before"] == {"status": "PUBLISHED"}
    assert metadata["after"] == {"status": "EXPIRED"}
    assert metadata["details"]["reason"] == "end_at_passed"


async def test_a_rerun_expires_nothing_and_writes_nothing(engine: AsyncEngine) -> None:
    refs = await add_references(engine)
    await add_offer(engine, refs, "PUBLISHED", NOW - timedelta(hours=2))
    await expire(engine)

    assert await expire(engine) == 0

    assert len(await rows(engine, "SELECT 1 FROM audit_logs")) == 1


async def test_batches_commit_independently_and_cover_every_offer(
    engine: AsyncEngine,
) -> None:
    refs = await add_references(engine)
    for _ in range(5):
        await add_offer(engine, refs, "PUBLISHED", NOW - timedelta(hours=1))

    count = await expire(engine, batch_size=2)

    assert count == 5
    assert set((await statuses(engine)).values()) == {"EXPIRED"}
    assert len(await rows(engine, "SELECT 1 FROM audit_logs")) == 5


async def test_an_offer_locked_by_an_editor_is_left_for_the_next_run(
    engine: AsyncEngine,
) -> None:
    refs = await add_references(engine)
    locked = await add_offer(engine, refs, "PUBLISHED", NOW - timedelta(hours=1))
    free = await add_offer(engine, refs, "PUBLISHED", NOW - timedelta(hours=1))
    async with engine.connect() as editor:
        await editor.execute(
            text("SELECT id FROM offers WHERE id = :id FOR UPDATE"), {"id": locked}
        )

        count = await expire(engine)

        assert count == 1
        assert (await statuses(engine))[free] == "EXPIRED"
        assert (await statuses(engine))[locked] == "PUBLISHED"
        await editor.rollback()

    assert await expire(engine) == 1
    assert (await statuses(engine))[locked] == "EXPIRED"


async def test_a_failed_audit_write_rolls_the_whole_batch_back(
    engine: AsyncEngine,
) -> None:
    class FailingAuditor:
        async def record_system_action(self, *_: Any, **__: Any) -> None:
            raise RuntimeError("audit write failed")

    refs = await add_references(engine)
    await add_offer(engine, refs, "PUBLISHED", NOW - timedelta(hours=1))

    with pytest.raises(ExpiryFailed) as raised:
        await expire(engine, auditor=FailingAuditor())

    assert raised.value.expired == 0
    assert set((await statuses(engine)).values()) == {"PUBLISHED"}
    assert await rows(engine, "SELECT 1 FROM audit_logs") == []


async def test_the_status_change_bumps_updated_at(engine: AsyncEngine) -> None:
    refs = await add_references(engine)
    offer_id = await add_offer(engine, refs, "PUBLISHED", NOW - timedelta(hours=1))
    before = (
        await rows(engine, "SELECT updated_at FROM offers WHERE id = :i", i=offer_id)
    )[0][0]

    await expire(engine)

    after = (
        await rows(engine, "SELECT updated_at FROM offers WHERE id = :i", i=offer_id)
    )[0][0]
    assert after > before


async def test_targets_are_live_offers_source_urls_and_active_direct_links_only(
    engine: AsyncEngine,
) -> None:
    refs = await add_references(engine)
    live = await add_offer(
        engine,
        refs,
        "PUBLISHED",
        NOW + timedelta(days=5),
        source_url="https://a.example/x",
    )
    open_ended = await add_offer(engine, refs, "PUBLISHED", None, source_url=None)
    ended = await add_offer(
        engine,
        refs,
        "PUBLISHED",
        NOW - timedelta(days=1),
        source_url="https://b.example/x",
    )
    draft = await add_offer(
        engine, refs, "DRAFT", None, source_url="https://c.example/x"
    )
    direct = await add_link(engine, live, "direct", "https://www.shop.example/deal")
    await add_link(engine, live, "awin", "https://www.awin1.com/cread.php?x=1")
    await add_link(engine, live, "cj", "https://www.anrdoezrs.net/click-1")
    await add_link(engine, live, "sample", "https://example.com/s")
    await add_link(engine, live, "direct", "https://www.shop.example/off", active=False)
    open_direct = await add_link(
        engine, open_ended, "direct", "https://shop.example/open"
    )
    await add_link(engine, ended, "direct", "https://www.shop.example/ended")
    await add_link(engine, draft, "direct", "https://www.shop.example/draft")
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async with maker() as session:
        targets = await collect_targets(OfferHealthRepository(session), NOW, limit=50)

    assert {(t.kind, t.offer_id, t.link_id) for t in targets.items} == {
        (TargetKind.SOURCE_URL, live, None),
        (TargetKind.AFFILIATE_LINK, live, direct),
        (TargetKind.AFFILIATE_LINK, open_ended, open_direct),
    }
    assert targets.not_followable == 0


async def test_a_direct_link_off_the_merchants_host_is_not_a_target(
    engine: AsyncEngine,
) -> None:
    refs = await add_references(engine, website="https://www.shop.example")
    offer = await add_offer(engine, refs, "PUBLISHED", None)
    await add_link(engine, offer, "direct", "https://elsewhere.example/x")
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async with maker() as session:
        targets = await collect_targets(OfferHealthRepository(session), NOW, limit=50)

    assert targets.items == () and targets.not_followable == 1


async def test_a_url_on_a_network_tracking_host_is_never_a_target(
    engine: AsyncEngine,
) -> None:
    refs = await add_references(engine, website="https://www.awin1.com")
    tracked = await add_offer(
        engine,
        refs,
        "PUBLISHED",
        None,
        source_url="https://www.awin1.com/cread.php?x=1",
    )
    await add_link(engine, tracked, "direct", "https://www.awin1.com/cread.php?x=2")
    fine = await add_offer(
        engine, refs, "PUBLISHED", None, source_url="https://a.example/x"
    )
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async with maker() as session:
        targets = await collect_targets(OfferHealthRepository(session), NOW, limit=50)

    assert [(t.kind, t.offer_id) for t in targets.items] == [
        (TargetKind.SOURCE_URL, fine)
    ]
    assert targets.tracking_host_skipped == 2


async def test_the_command_line_expires_and_exits_zero(
    engine: AsyncEngine, database_url: str
) -> None:
    refs = await add_references(engine)
    offer_id = await add_offer(
        engine, refs, "PUBLISHED", datetime.now(UTC) - timedelta(days=1)
    )

    finished = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-m", "commands.check_offer_health"],
        cwd=BACKEND_ROOT,
        env={**os.environ, "DATABASE_URL": database_url, "ENVIRONMENT": "test"},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert finished.returncode == 0, finished.stderr
    assert "expired 1 offer(s)" in finished.stdout
    assert (await statuses(engine))[offer_id] == "EXPIRED"
