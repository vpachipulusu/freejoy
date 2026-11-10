"""The Awin importer against a real Postgres: migration 0004, constraints, upserts, audit rows, locks, the CLI.

Same contract as ``test_check_offer_health_e2e.py``: runs only when ``TEST_DATABASE_URL`` points at a
throwaway local ``*_test`` database, which it rebuilds with the real Alembic migrations. Nothing here opens
a network connection: the Awin client is always a fake, and the subprocess tests run with every non-loopback
connection blocked (``tests/commands/awin_cli_runner.py``).
"""

import asyncio
import os
import subprocess
import sys
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

import app.services.awin_import_run as run_module
from app.core.database import build_engine
from app.core.ids import new_id
from app.core.permissions import AdminContext
from app.repositories.admin_offers import AdminOfferRepository
from app.repositories.audit import AuditRepository
from app.repositories.awin_import import AwinImportRepository
from app.services.admin_catalogue_errors import OfferChanged, PublishBlocked
from app.services.admin_offers import AdminOfferService
from app.services.audit import AuditService
from app.services.awin_import_run import RunOptions, RunReport, Unit, run_import
from app.services.offer_lifecycle import OfferAction
from commands.import_awin_offers import database_units
from tests._subprocess_support import BACKEND_ROOT, inherited_environment
from tests.awin_samples import CODE, PUBLISHER_ID, raw_item, raw_page, tracking_for
from tests.fakes_awin import FakeAwinClient

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
EDITOR = AdminContext(
    "editor-1", "admin", frozenset({"offers.write", "offers.publish"})
)

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="TEST_DATABASE_URL is not set"
)


def _migrate(url: str, *args: str) -> None:
    subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_ROOT,
        env={**os.environ, "DATABASE_URL_DIRECT": url},
        check=True,
        capture_output=True,
    )


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


async def rows(engine: AsyncEngine, sql: str, **params: Any) -> list[Any]:
    async with engine.connect() as connection:
        return list((await connection.execute(text(sql), params)).all())


async def scalar(engine: AsyncEngine, sql: str, **params: Any) -> Any:
    return (await rows(engine, sql, **params))[0][0]


async def execute(engine: AsyncEngine, sql: str, **params: Any) -> None:
    async with engine.begin() as connection:
        await connection.execute(text(sql), params)


async def run(
    engine: AsyncEngine,
    items: list[Any],
    *,
    now: datetime = NOW,
    lock_timeout_ms: int = 30_000,
    **options: Any,
) -> RunReport:
    maker = async_sessionmaker(engine, expire_on_commit=False)
    return await run_import(
        FakeAwinClient([items]),
        database_units(maker, lock_timeout_ms),
        RunOptions(publisher_id=PUBLISHER_ID, **options),
        now,
    )


async def first_offer(engine: AsyncEngine) -> str:
    return str(
        await scalar(engine, "SELECT id FROM offers ORDER BY created_at LIMIT 1")
    )


class EditorAuditor:
    """What the admin router's auditor does, minus the request details: one admin audit row."""

    def __init__(self, service: AuditService) -> None:
        self.service = service

    async def record(self, admin: AdminContext, **fields: Any) -> None:
        await self.service.record_admin_action(admin, **fields)


def editor_service(session: Any) -> AdminOfferService:
    auditor = EditorAuditor(AuditService(AuditRepository(session)))
    return AdminOfferService(AdminOfferRepository(session), session, auditor)


# --- the schema ----------------------------------------------------------------------------


async def test_the_provenance_tables_enforce_their_constraints(
    engine: AsyncEngine,
) -> None:
    await run(engine, [raw_item(1)])
    offer_id = await first_offer(engine)
    insert = (
        "INSERT INTO offer_imports (id, provider, external_id, offer_id, source_hash,"
        " applied_source_hash, applied_content_hash, applied_at)"
        " VALUES (:id, :provider, :external, :offer, :h, :h, :h, now())"
    )
    good = {"provider": "awin", "external": "2", "offer": offer_id, "h": "a" * 64}
    other = await _bare_offer(engine)

    cases: list[tuple[dict[str, Any], str]] = [
        ({**good, "external": "1", "offer": other}, "same provider and external id"),
        ({**good, "external": "9"}, "second provenance row for one offer"),
        ({**good, "offer": other, "provider": "AWIN"}, "upper-case provider"),
        ({**good, "offer": other, "provider": "a b"}, "provider with a space"),
        ({**good, "offer": other, "external": ""}, "empty external id"),
        ({**good, "offer": other, "external": "x" * 65}, "over-long external id"),
        ({**good, "offer": other, "h": "short"}, "a hash that is not 64 characters"),
        ({**good, "offer": "no-such-offer"}, "an offer that does not exist"),
    ]
    for params, why in cases:
        with pytest.raises(IntegrityError):
            await execute(engine, insert, id=new_id(), **params)
        assert why


async def _bare_offer(engine: AsyncEngine) -> str:
    category = str(await scalar(engine, "SELECT id FROM categories LIMIT 1"))
    merchant = str(await scalar(engine, "SELECT id FROM merchants LIMIT 1"))
    offer_id = new_id()
    await execute(
        engine,
        "INSERT INTO offers (id, title, slug, summary, category_id, merchant_id, offer_type,"
        " image_tint, image_label, image_alt, postage_kind, eligibility, availability)"
        " VALUES (:id, 't', :slug, 's', :c, :m, 'VOUCHER', 'cream', 'l', 'a', 'CHECK_TERMS', 'e', 'a')",
        id=offer_id,
        slug=f"bare-{offer_id}",
        c=category,
        m=merchant,
    )
    return offer_id


async def test_a_merchant_import_row_is_unique_per_advertiser_and_per_merchant(
    engine: AsyncEngine,
) -> None:
    await run(engine, [raw_item(1)])
    merchant = str(await scalar(engine, "SELECT id FROM merchants"))
    insert = (
        "INSERT INTO merchant_imports (id, provider, external_id, merchant_id)"
        " VALUES (:id, 'awin', :external, :merchant)"
    )

    with pytest.raises(IntegrityError):  # the advertiser is already mapped
        await execute(engine, insert, id=new_id(), external="77", merchant=merchant)
    with pytest.raises(IntegrityError):  # the merchant already stands for an advertiser
        await execute(engine, insert, id=new_id(), external="78", merchant=merchant)
    with pytest.raises(IntegrityError):
        await execute(engine, insert, id=new_id(), external="79", merchant="nope")


async def test_deleting_an_offer_or_merchant_deletes_only_its_provenance(
    engine: AsyncEngine,
) -> None:
    await run(engine, [raw_item(1)])
    offer_id = await first_offer(engine)

    await execute(engine, "DELETE FROM offers WHERE id = :id", id=offer_id)

    assert await scalar(engine, "SELECT count(*) FROM offer_imports") == 0
    assert await scalar(engine, "SELECT count(*) FROM merchants") == 1
    await execute(engine, "DELETE FROM merchants")
    assert await scalar(engine, "SELECT count(*) FROM merchant_imports") == 0


async def test_the_downgrade_drops_only_the_provenance_tables(
    engine: AsyncEngine, database_url: str
) -> None:
    await run(engine, [raw_item(1)])
    await engine.dispose()

    await asyncio.to_thread(_migrate, database_url, "downgrade", "0003")
    try:
        tables = {
            r[0]
            for r in await rows(
                engine, "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
            )
        }
        assert {"offers", "merchants"} <= tables
        assert not {"offer_imports", "merchant_imports"} & tables
        assert await scalar(engine, "SELECT count(*) FROM offers") == 1
    finally:
        await asyncio.to_thread(_migrate, database_url, "upgrade", "head")


# --- creating and idempotency --------------------------------------------------------------


async def test_an_import_creates_a_draft_with_provenance_and_audit_rows(
    engine: AsyncEngine,
) -> None:
    report = await run(engine, [raw_item(1), raw_item(2)])

    assert report.tally.created == 2
    offer = (
        await rows(
            engine, "SELECT status, last_verified_at, featured, offer_type FROM offers"
        )
    )[0]
    assert tuple(offer) == ("DRAFT", None, False, "VOUCHER")
    assert (
        await scalar(
            engine,
            "SELECT count(*) FROM affiliate_links WHERE provider = 'awin' AND active",
        )
        == 2
    )
    assert await scalar(engine, "SELECT count(*) FROM offer_imports") == 2
    category = (await rows(engine, "SELECT slug, enabled FROM categories"))[0]
    merchant = (await rows(engine, "SELECT name, active, website FROM merchants"))[0]
    assert tuple(category) == ("imported-uncategorised", False)
    assert tuple(merchant) == ("Acme Stores", False, None)
    actors = await rows(
        engine,
        "SELECT user_id, metadata->>'actor' FROM audit_logs"
        " WHERE action IN ('admin.offers.create', 'admin.offer_links.create',"
        " 'admin.merchants.create', 'admin.categories.create')",
    )
    assert len(actors) == 2 + 2 + 1 + 1
    assert {tuple(a) for a in actors} == {(None, "commands.import_awin_offers")}
    leaked = await scalar(
        engine,
        "SELECT count(*) FROM audit_logs WHERE metadata::text LIKE '%https://%'"
        " OR metadata::text LIKE '%awinaffid%' OR metadata::text LIKE :code",
        code=f"%{CODE}%",
    )
    assert leaked == 0


async def test_an_imported_draft_cannot_be_published_through_the_real_publish_path(
    engine: AsyncEngine,
) -> None:
    await run(engine, [raw_item(1, endDate=None)])
    offer_id = await first_offer(engine)

    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        service = editor_service(session)
        token = (await service.get_offer(offer_id)).review_token
        with pytest.raises(PublishBlocked) as blocked:
            await service.transition(
                EDITOR, offer_id, OfferAction.PUBLISH, review_token=token
            )

    assert set(blocked.value.reasons) == {
        "category_disabled",
        "merchant_inactive",
        "not_verified",
    }


async def test_a_second_run_writes_nothing(engine: AsyncEngine) -> None:
    await run(engine, [raw_item(1), raw_item(2)])
    before = await rows(engine, "SELECT id, updated_at FROM offers ORDER BY id")
    imports = await rows(engine, "SELECT id, updated_at FROM offer_imports ORDER BY id")
    audits = await scalar(engine, "SELECT count(*) FROM audit_logs")

    report = await run(engine, [raw_item(1), raw_item(2)], now=NOW + timedelta(days=1))

    assert report.tally.unchanged == 2 and report.tally.created == 0
    assert await rows(engine, "SELECT id, updated_at FROM offers ORDER BY id") == before
    assert (
        await rows(engine, "SELECT id, updated_at FROM offer_imports ORDER BY id")
        == imports
    )
    assert await scalar(engine, "SELECT count(*) FROM audit_logs") == audits


async def test_a_slug_taken_by_a_person_falls_back_to_the_promotion_id(
    engine: AsyncEngine,
) -> None:
    await run(engine, [raw_item(1)])
    # forget where the offer came from: it now looks like an offer a person made
    await execute(engine, "TRUNCATE offer_imports, merchant_imports")

    report = await run(engine, [raw_item(1)])

    assert report.tally.created == 1
    slugs = {r[0] for r in await rows(engine, "SELECT slug FROM offers")}
    assert slugs == {"10-off-everything-1-1", "awin-1"}


# --- refresh and edit protection -----------------------------------------------------------


async def test_an_untouched_import_has_its_text_refreshed_in_place(
    engine: AsyncEngine,
) -> None:
    await run(engine, [raw_item(1)])
    offer_id = await first_offer(engine)
    destination = await scalar(engine, "SELECT destination_url FROM affiliate_links")

    report = await run(
        engine, [raw_item(1, title="Source retitled")], now=NOW + timedelta(days=1)
    )

    assert report.tally.updated == 1
    assert await first_offer(engine) == offer_id
    assert await scalar(engine, "SELECT title FROM offers") == "Source retitled"
    assert (
        await scalar(engine, "SELECT destination_url FROM affiliate_links")
        == destination
    )
    assert await scalar(engine, "SELECT count(*) FROM affiliate_links") == 1
    assert await scalar(engine, "SELECT source_changed_at FROM offer_imports") is None
    # the fingerprint was restored, so a second source change refreshes again
    again = await run(
        engine, [raw_item(1, title="Retitled twice")], now=NOW + timedelta(days=2)
    )
    assert again.tally.updated == 1


async def test_an_importer_refresh_after_the_editor_loaded_the_offer_stales_verify_and_publish(
    engine: AsyncEngine,
) -> None:
    await run(engine, [raw_item(1, endDate=None)])
    offer_id = await first_offer(engine)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        loaded = (await editor_service(session).get_offer(offer_id)).review_token

    await run(
        engine,
        [raw_item(1, title="Source retitled", endDate=None)],
        now=NOW + timedelta(days=1),
    )
    offers = await rows(engine, "SELECT * FROM offers")
    audits = await scalar(engine, "SELECT count(*) FROM audit_logs")

    async with maker() as session:
        service = editor_service(session)
        with pytest.raises(OfferChanged):
            await service.verify(EDITOR, offer_id, None, review_token=loaded)
        with pytest.raises(OfferChanged):
            await service.transition(
                EDITOR, offer_id, OfferAction.PUBLISH, review_token=loaded
            )

    assert await rows(engine, "SELECT * FROM offers") == offers
    assert await scalar(engine, "SELECT count(*) FROM audit_logs") == audits
    async with maker() as session:  # a token read after the refresh is accepted
        service = editor_service(session)
        fresh = (await service.get_offer(offer_id)).review_token
        verified = await service.verify(EDITOR, offer_id, None, review_token=fresh)
    assert verified.offer.last_verified_at is not None


async def test_a_changed_tracking_url_is_held_back_and_the_rows_stay_identical(
    engine: AsyncEngine,
) -> None:
    await run(engine, [raw_item(1)])
    offers = await rows(engine, "SELECT * FROM offers")
    links = await rows(engine, "SELECT * FROM affiliate_links")
    moved = raw_item(1, urlTracking=tracking_for(1, clickref2="1"))

    first = await run(engine, [moved], now=NOW + timedelta(days=1))
    second = await run(engine, [moved], now=NOW + timedelta(days=2))

    assert first.tally.source_changed == 1 and second.tally.unchanged == 1
    assert await rows(engine, "SELECT * FROM offers") == offers
    assert await rows(engine, "SELECT * FROM affiliate_links") == links
    assert (
        await scalar(engine, "SELECT source_changed_at FROM offer_imports") is not None
    )
    marked = await scalar(
        engine,
        "SELECT count(*) FROM audit_logs WHERE action = 'admin.offers.source_changed'",
    )
    assert marked == 1


async def test_an_offer_edited_through_the_admin_service_is_never_overwritten(
    engine: AsyncEngine,
) -> None:
    await run(engine, [raw_item(1)])
    offer_id = await first_offer(engine)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        await editor_service(session).update_offer(
            EDITOR, offer_id, {"title": "An editor's title"}
        )

    report = await run(
        engine, [raw_item(1, title="Source retitled")], now=NOW + timedelta(days=1)
    )

    assert report.tally.source_changed == 1 and report.tally.updated == 0
    assert await scalar(engine, "SELECT title FROM offers") == "An editor's title"
    marker = await scalar(engine, "SELECT source_changed_at FROM offer_imports")
    assert marker == NOW + timedelta(days=1)
    changed = await rows(
        engine,
        "SELECT user_id FROM audit_logs WHERE action = 'admin.offers.source_changed'",
    )
    assert [tuple(r) for r in changed] == [(None,)]


@pytest.mark.parametrize("status", ["PUBLISHED", "EXPIRED", "ARCHIVED"])
async def test_a_published_expired_or_archived_offer_is_never_overwritten(
    engine: AsyncEngine, status: str
) -> None:
    await run(engine, [raw_item(1)])
    await execute(
        engine,
        "UPDATE offers SET status = :status, last_verified_at = now()",
        status=status,
    )

    report = await run(engine, [raw_item(1, title="Source retitled")], now=NOW)

    assert report.tally.source_changed == 1
    assert await scalar(engine, "SELECT title FROM offers") != "Source retitled"
    assert await scalar(engine, "SELECT status FROM offers") == status


async def test_an_edit_to_the_affiliate_link_protects_the_offer(
    engine: AsyncEngine,
) -> None:
    await run(engine, [raw_item(1)])
    await execute(engine, "UPDATE affiliate_links SET active = false")

    report = await run(engine, [raw_item(1, title="Source retitled")])

    assert report.tally.source_changed == 1
    assert await scalar(engine, "SELECT active FROM affiliate_links") is False


async def test_housekeeping_that_only_touches_updated_at_does_not_block_a_refresh(
    engine: AsyncEngine,
) -> None:
    await run(engine, [raw_item(1)])
    await execute(engine, "UPDATE offers SET updated_at = now() + interval '1 day'")

    report = await run(engine, [raw_item(1, title="Source retitled")])

    assert report.tally.updated == 1


# --- transactions, locks and concurrency ---------------------------------------------------


async def test_two_runs_at_the_same_time_create_each_offer_once(
    engine: AsyncEngine,
) -> None:
    feed = [raw_item(n) for n in range(1, 21)]

    first, second = await asyncio.gather(run(engine, feed), run(engine, feed))

    assert first.tally.created + second.tally.created == 20
    assert first.tally.unchanged + second.tally.unchanged == 20
    assert await scalar(engine, "SELECT count(*) FROM offers") == 20
    assert await scalar(engine, "SELECT count(*) FROM merchants") == 1
    assert await scalar(engine, "SELECT count(*) FROM categories") == 1


async def test_a_failing_batch_rolls_back_whole_while_earlier_batches_stay(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(run_module, "BATCH_SIZE", 2)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    created_audits = 0

    class FailingAuditor(AuditService):
        async def record_system_action(self, *args: Any, **kwargs: Any) -> None:
            nonlocal created_audits
            if kwargs["action"] == "admin.offers.create":
                created_audits += 1
                if created_audits == 4:
                    raise RuntimeError("audit write failed")
            await super().record_system_action(*args, **kwargs)

    @asynccontextmanager
    async def units() -> AsyncIterator[Unit]:
        async with maker() as session:
            yield Unit(
                AwinImportRepository(session),
                session,
                FailingAuditor(AuditRepository(session)),
            )

    with pytest.raises(run_module.ImportFailed) as failed:
        await run_import(
            FakeAwinClient([raw_page(1, 6)]),
            units,
            RunOptions(publisher_id=PUBLISHER_ID),
            NOW,
        )

    assert failed.value.cause == "RuntimeError"
    assert failed.value.report.tally.created == 2
    assert await scalar(engine, "SELECT count(*) FROM offers") == 2
    assert await scalar(engine, "SELECT count(*) FROM offer_imports") == 2
    assert await scalar(engine, "SELECT count(*) FROM affiliate_links") == 2
    rerun = await run(engine, raw_page(1, 6))
    assert rerun.tally.created == 4 and rerun.tally.unchanged == 2


async def test_an_offer_an_editor_is_saving_is_waited_for_then_judged_as_edited(
    engine: AsyncEngine,
) -> None:
    await run(engine, [raw_item(1)])
    offer_id = await first_offer(engine)
    editor = await engine.connect()
    try:
        transaction = await editor.begin()
        await editor.execute(
            text("SELECT id FROM offers WHERE id = :id FOR UPDATE"), {"id": offer_id}
        )
        importer = asyncio.create_task(
            run(engine, [raw_item(1, title="Source retitled")])
        )
        await asyncio.sleep(0.5)
        assert not importer.done()  # waiting for the editor's lock
        await editor.execute(
            text("UPDATE offers SET title = 'Saved by an editor' WHERE id = :id"),
            {"id": offer_id},
        )
        await transaction.commit()
    finally:
        await editor.close()

    report = await asyncio.wait_for(importer, timeout=20)

    assert report.tally.source_changed == 1
    assert await scalar(engine, "SELECT title FROM offers") == "Saved by an editor"


async def test_a_stuck_editor_transaction_fails_the_run_instead_of_waiting_for_ever(
    engine: AsyncEngine,
) -> None:
    await run(engine, [raw_item(1), raw_item(2)])
    offer_id = await first_offer(engine)
    editor = await engine.connect()
    try:
        transaction = await editor.begin()
        await editor.execute(
            text("SELECT id FROM offers WHERE id = :id FOR UPDATE"), {"id": offer_id}
        )
        feed = [raw_item(n, title=f"Source retitled {n}") for n in (1, 2)]
        maker = async_sessionmaker(engine, expire_on_commit=False)

        with pytest.raises(run_module.ImportFailed) as failed:
            await run_import(
                FakeAwinClient([feed]),
                database_units(maker, lock_timeout_ms=300),
                RunOptions(publisher_id=PUBLISHER_ID),
                NOW,
            )
        await transaction.rollback()
    finally:
        await editor.close()

    assert failed.value.cause == "lock_timeout"
    assert (
        await scalar(engine, "SELECT count(*) FROM offers WHERE title LIKE 'Source%'")
        == 0
    )
    rerun = await run(
        engine, [raw_item(n, title=f"Source retitled {n}") for n in (1, 2)]
    )
    assert rerun.tally.updated == 2  # a rerun carries on once the lock is gone


async def test_a_second_run_waits_for_the_run_lock_only_as_long_as_the_timeout(
    engine: AsyncEngine,
) -> None:
    holder = await engine.connect()
    try:
        transaction = await holder.begin()
        await holder.execute(text("SELECT pg_advisory_xact_lock(7460046001)"))

        with pytest.raises(run_module.ImportFailed) as failed:
            await run(engine, [raw_item(1)], lock_timeout_ms=300)
        await transaction.rollback()
    finally:
        await holder.close()

    assert failed.value.cause == "lock_timeout"
    assert await scalar(engine, "SELECT count(*) FROM offers") == 0


async def test_the_holding_category_is_read_under_a_share_lock(
    engine: AsyncEngine,
) -> None:
    await run(engine, [raw_item(1)])
    editor = await engine.connect()
    try:
        transaction = (
            await editor.begin()
        )  # an admin enabling the category, not yet committed
        await editor.execute(text("UPDATE categories SET enabled = enabled"))

        with pytest.raises(run_module.ImportFailed) as failed:
            await run(engine, [raw_item(2)], lock_timeout_ms=300)
        await transaction.rollback()
    finally:
        await editor.close()

    assert failed.value.cause == "lock_timeout"


# --- the command line ----------------------------------------------------------------------


def _cli(*args: str, environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "tests.commands.awin_cli_runner", *args],
        cwd=BACKEND_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def _environment(database_url: str) -> dict[str, str]:
    return inherited_environment(DATABASE_URL=database_url, ENVIRONMENT="test")


async def test_the_command_line_imports_with_a_fake_client_and_no_network(
    engine: AsyncEngine, database_url: str
) -> None:
    env = _environment(database_url)

    probe = await asyncio.to_thread(_cli, "probe", environment=env)
    assert probe.returncode == 0 and probe.stdout.strip() == "blocked", probe.stderr

    first = await asyncio.to_thread(_cli, "run", "3", "-", environment=env)
    assert first.returncode == 0, first.stderr
    assert "created 3" in first.stdout and first.stderr == ""
    assert (
        await scalar(engine, "SELECT count(*) FROM offers WHERE status = 'DRAFT'") == 3
    )

    second = await asyncio.to_thread(_cli, "run", "3", "-", environment=env)
    assert second.returncode == 0 and "unchanged 3" in second.stdout

    moved = await asyncio.to_thread(_cli, "run", "3", "v2", environment=env)
    assert moved.returncode == 0 and "updated 3" in moved.stdout
    assert (
        await scalar(engine, "SELECT count(*) FROM offers WHERE title LIKE '%v2'") == 3
    )


async def test_a_dry_run_from_the_command_line_writes_nothing(
    engine: AsyncEngine, database_url: str
) -> None:
    finished = await asyncio.to_thread(
        _cli, "run", "3", "-", "--dry-run", environment=_environment(database_url)
    )

    assert finished.returncode == 0, finished.stderr
    assert "dry run, nothing was written" in finished.stdout
    assert await scalar(engine, "SELECT count(*) FROM offers") == 0


async def test_the_real_entry_point_exits_2_without_settings_and_never_builds_a_client(
    engine: AsyncEngine, database_url: str
) -> None:
    finished = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-m", "commands.import_awin_offers", "--dry-run"],
        cwd=BACKEND_ROOT,
        env=_environment(database_url),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert finished.returncode == 2
    assert (
        "AWIN_PUBLISHER_ID" in finished.stderr
        and "AWIN_OAUTH_API_TOKEN" in finished.stderr
    )
    assert await scalar(engine, "SELECT count(*) FROM offers") == 0


# --- the review queue: dismissing a source change and the next importer run ----------------------


async def dismiss_through_the_service(engine: AsyncEngine, offer_id: str) -> None:
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        service = editor_service(session)
        token = (await service.get_offer(offer_id)).review_token
        await service.dismiss_source_change(EDITOR, offer_id, token)


async def import_state(engine: AsyncEngine) -> Any:
    return (
        await rows(
            engine,
            "SELECT source_hash, applied_source_hash, applied_content_hash, applied_at,"
            " source_changed_at, updated_at FROM offer_imports",
        ),
        await rows(engine, "SELECT * FROM offers"),
        await rows(engine, "SELECT action FROM audit_logs ORDER BY created_at, id"),
    )


async def test_a_dismissed_source_change_is_not_flagged_again_but_a_newer_one_is(
    engine: AsyncEngine,
) -> None:
    await run(engine, [raw_item(1)])
    offer_id = await first_offer(engine)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        await editor_service(session).update_offer(
            EDITOR, offer_id, {"title": "An editor's title"}
        )
    day_two, day_three = NOW + timedelta(days=1), NOW + timedelta(days=2)
    await run(engine, [raw_item(1, title="Source retitled")], now=day_two)
    hashes = await rows(
        engine, "SELECT source_hash, applied_source_hash FROM offer_imports"
    )
    assert (
        await scalar(engine, "SELECT source_changed_at FROM offer_imports") == day_two
    )

    await dismiss_through_the_service(engine, offer_id)

    assert await scalar(engine, "SELECT source_changed_at FROM offer_imports") is None
    assert (
        await rows(engine, "SELECT source_hash, applied_source_hash FROM offer_imports")
        == hashes
    )
    audited = await rows(
        engine,
        "SELECT user_id, resource, resource_id FROM audit_logs"
        " WHERE action = 'admin.offers.source_change_dismissed'",
    )
    assert [tuple(r) for r in audited] == [("editor-1", "offers", offer_id)]
    settled = await import_state(engine)

    again = await run(engine, [raw_item(1, title="Source retitled")], now=day_three)

    assert again.tally.unchanged == 1 and again.tally.source_changed == 0
    assert await import_state(engine) == settled

    newer = await run(engine, [raw_item(1, title="Retitled twice")], now=day_three)

    assert newer.tally.source_changed == 1 and newer.tally.updated == 0
    assert (
        await scalar(engine, "SELECT source_changed_at FROM offer_imports") == day_three
    )
    assert await scalar(engine, "SELECT title FROM offers") == "An editor's title"


async def test_dismissing_leaves_the_offer_and_its_review_token_untouched(
    engine: AsyncEngine,
) -> None:
    await run(engine, [raw_item(1)])
    offer_id = await first_offer(engine)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        await editor_service(session).update_offer(
            EDITOR, offer_id, {"summary": "Mine"}
        )
    await run(engine, [raw_item(1, title="Moved")], now=NOW + timedelta(days=1))
    offers_before = await rows(engine, "SELECT * FROM offers")
    async with maker() as session:
        token = (await editor_service(session).get_offer(offer_id)).review_token

    await dismiss_through_the_service(engine, offer_id)

    async with maker() as session:
        after = await editor_service(session).get_offer(offer_id)
    assert after.review_token == token
    assert await rows(engine, "SELECT * FROM offers") == offers_before
    assert after.provenance is not None and after.provenance.source_changed_at is None
