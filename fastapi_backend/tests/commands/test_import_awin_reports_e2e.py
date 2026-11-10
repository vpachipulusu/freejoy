"""The report ingest against a real Postgres: migration 0005, constraints, upserts, matching, locks, the CLI.

Same contract as ``test_import_awin_offers_e2e.py``: runs only when ``TEST_DATABASE_URL`` points at a
throwaway local ``*_test`` database, which it rebuilds with the real Alembic migrations. Nothing here opens
a network connection: the Awin client is always a fake, and the subprocess tests run with every non-loopback
connection blocked (``tests/commands/awin_reports_cli_runner.py``).
"""

import asyncio
import os
import subprocess
import sys
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, date, datetime
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.core.database import build_engine
from app.core.ids import new_id
from app.repositories.network_reports import MAX_CANDIDATES, REPORT_LOCK_KEY
from app.services.network_report_run import (
    ReportRunFailed,
    RunOptions,
    RunReport,
    run_report_import,
)
from commands.import_awin_reports import database_units
from tests._subprocess_support import BACKEND_ROOT, inherited_environment
from tests.awin_report_samples import NOW, raw_row, raw_rows
from tests.fakes_awin import FakeAwinClient

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
DAY = date(2026, 11, 3)
LATER = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)

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
            text(
                "TRUNCATE audit_logs, network_report_rows, offers, categories, merchants CASCADE"
            )
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
    pages: list[Any],
    *,
    now: datetime = NOW,
    lock_timeout_ms: int = 30_000,
    **options: Any,
) -> RunReport:
    maker = async_sessionmaker(engine, expire_on_commit=False)
    return await run_report_import(
        FakeAwinClient(transaction_pages=pages),
        database_units(maker, lock_timeout_ms),
        RunOptions(**{"start": DAY, "end": DAY, **options}),
        now,
    )


async def seed_catalogue(
    engine: AsyncEngine, *, advertiser_id: int = 77
) -> dict[str, str]:
    """A category, a mapped merchant, an offer with one active awin link; the ids it made."""
    ids = {name: new_id() for name in ("category", "merchant", "offer", "link")}
    await execute(
        engine,
        "INSERT INTO categories (id, name, slug, short_name, description, intro, tint)"
        " VALUES (:c, 'c', :cs, 'c', 'd', 'i', 'cream')",
        c=ids["category"],
        cs=f"cat-{ids['category']}",
    )
    await execute(
        engine,
        "INSERT INTO merchants (id, name, slug) VALUES (:m, 'm', :ms)",
        m=ids["merchant"],
        ms=f"mer-{ids['merchant']}",
    )
    await execute(
        engine,
        "INSERT INTO merchant_imports (id, provider, external_id, merchant_id)"
        " VALUES (:id, 'awin', :ext, :m)",
        id=new_id(),
        ext=str(advertiser_id),
        m=ids["merchant"],
    )
    await execute(
        engine,
        "INSERT INTO offers (id, title, slug, summary, category_id, merchant_id, offer_type,"
        " commercial_basis, image_tint, image_label, image_alt, postage_kind, eligibility, availability)"
        " VALUES (:o, 't', :os, 's', :c, :m, 'VOUCHER', 'AFFILIATE', 'cream', 'l', 'a', 'CHECK_TERMS', 'e', 'a')",
        o=ids["offer"],
        os=f"offer-{ids['offer']}",
        c=ids["category"],
        m=ids["merchant"],
    )
    return ids


async def add_link(
    engine: AsyncEngine,
    offer_id: str,
    *,
    destination: str = "https://www.awin1.com/cread.php?awinmid=77&awinaffid=424242",
    tracking_id: str | None = None,
    active: bool = True,
    link_id: str | None = None,
) -> str:
    link_id = link_id or new_id()
    await execute(
        engine,
        "INSERT INTO affiliate_links (id, offer_id, provider, destination_url, tracking_id, active)"
        " VALUES (:id, :o, 'awin', :d, :t, :a)",
        id=link_id,
        o=offer_id,
        d=destination,
        t=tracking_id,
        a=active,
    )
    return link_id


async def stored(engine: AsyncEngine, external_id: str) -> Any:
    return (
        await rows(
            engine,
            "SELECT * FROM network_report_rows WHERE external_id = :e",
            e=external_id,
        )
    )[0]


# --- the schema ----------------------------------------------------------------------------


GOOD_ROW = {
    "provider": "awin",
    "external": "1",
    "merchant_ext": "77",
    "status": "pending",
    "raw": "pending",
    "commission": 150,
    "sale": 3000,
    "currency": "GBP",
    "click_ref": "ref-1",
    "hash": "a" * 64,
}
INSERT_ROW = (
    "INSERT INTO network_report_rows (id, provider, external_id, merchant_external_id,"
    " transaction_at, commission_status, commission_status_raw, commission_amount_minor,"
    " sale_amount_minor, currency, click_ref, report_hash, first_seen_at, last_seen_at)"
    " VALUES (:id, :provider, :external, :merchant_ext, now(), :status, :raw, :commission,"
    " :sale, :currency, :click_ref, :hash, now(), now() + :last_after * interval '1 second')"
)
PREFIX = "ck_network_report_rows_"


async def test_the_table_enforces_its_constraints(engine: AsyncEngine) -> None:
    good = {**GOOD_ROW, "last_after": 0}
    await execute(engine, INSERT_ROW, id=new_id(), **good)
    cases: list[tuple[dict[str, Any], str]] = [
        ({}, "uq_network_report_rows_provider_external_id"),
        ({"external": "2", "provider": "AWIN"}, PREFIX + "provider_valid"),
        ({"external": "2", "provider": "a b"}, PREFIX + "provider_valid"),
        ({"external": ""}, PREFIX + "external_id_valid"),
        ({"external": "x" * 65}, PREFIX + "external_id_valid"),
        ({"external": "2", "merchant_ext": ""}, PREFIX + "merchant_external_id_valid"),
        ({"external": "2", "status": "refunded"}, PREFIX + "commission_status_valid"),
        ({"external": "2", "status": "APPROVED"}, PREFIX + "commission_status_valid"),
        ({"external": "2", "raw": ""}, PREFIX + "commission_status_raw_valid"),
        ({"external": "2", "raw": "x" * 33}, PREFIX + "commission_status_raw_valid"),
        ({"external": "2", "commission": -1}, PREFIX + "amounts_valid"),
        ({"external": "2", "sale": -1}, PREFIX + "amounts_valid"),
        ({"external": "2", "currency": "gbp"}, PREFIX + "currency_valid"),
        ({"external": "2", "currency": "GB"}, PREFIX + "currency_valid"),
        ({"external": "2", "click_ref": ""}, PREFIX + "click_ref_valid"),
        ({"external": "2", "click_ref": "x" * 101}, PREFIX + "click_ref_valid"),
        ({"external": "2", "hash": "short"}, PREFIX + "report_hash_valid"),
        ({"external": "2", "hash": "A" * 64}, PREFIX + "report_hash_valid"),
        ({"external": "2", "last_after": -5}, PREFIX + "seen_order_valid"),
    ]
    for changes, constraint in cases:
        with pytest.raises(IntegrityError, match=constraint):
            await execute(engine, INSERT_ROW, id=new_id(), **{**good, **changes})
    await (
        execute(  # a null click reference, a large amount and a deleted status are fine
            engine,
            INSERT_ROW,
            id=new_id(),
            **{
                **good,
                "external": "3",
                "click_ref": None,
                "commission": 99_999_999_999,
                "status": "deleted",
            },
        )
    )


async def test_the_catalogue_links_must_exist_when_given(engine: AsyncEngine) -> None:
    insert = INSERT_ROW.replace("report_hash,", "report_hash, offer_id,").replace(
        ":hash,", ":hash, 'no-such-offer',"
    )

    with pytest.raises(IntegrityError):
        await execute(engine, insert, id=new_id(), **GOOD_ROW, last_after=0)


# --- a run ---------------------------------------------------------------------------------


async def test_a_run_stores_rows_matched_by_tracking_id_by_url_and_by_merchant_only(
    engine: AsyncEngine,
) -> None:
    ids = await seed_catalogue(engine)
    by_id = await add_link(engine, ids["offer"], tracking_id="ref-1")
    by_url = await add_link(
        engine,
        ids["offer"],
        destination="https://www.awin1.com/cread.php?awinmid=77&awinaffid=424242&clickref=ref-2",
    )
    page = [raw_row(1), raw_row(2), raw_row(3, clickRefs=None), raw_row(4)]

    report = await run(engine, [page])

    assert (report.tally.inserted, report.tally.unmatched) == (4, 2)
    first = await stored(engine, "1")
    second = await stored(engine, "2")
    third = await stored(engine, "3")
    assert (first.affiliate_link_id, first.offer_id) == (by_id, ids["offer"])
    assert (second.affiliate_link_id, second.offer_id) == (by_url, ids["offer"])
    assert third.offer_id is None and third.affiliate_link_id is None
    assert {first.merchant_id, second.merchant_id, third.merchant_id} == {
        ids["merchant"]
    }
    assert first.commission_amount_minor == 150 and first.sale_amount_minor == 3000
    assert first.transaction_at == datetime(2026, 11, 3, 10, 15, tzinfo=UTC)
    assert first.commission_status == "approved" and first.currency == "GBP"
    assert first.first_seen_at == NOW and first.last_seen_at == NOW


async def test_a_click_reference_with_a_like_wildcard_matches_only_itself(
    engine: AsyncEngine,
) -> None:
    ids = await seed_catalogue(engine)
    await add_link(
        engine,
        ids["offer"],
        destination="https://www.awin1.com/cread.php?awinmid=77&awinaffid=424242&clickref=aXb",
    )

    await run(engine, [[raw_row(1, clickRefs={"clickRef": "a_b"})]])

    assert (await stored(engine, "1")).offer_id is None


async def test_an_inactive_link_never_matches(engine: AsyncEngine) -> None:
    ids = await seed_catalogue(engine)
    await add_link(engine, ids["offer"], tracking_id="ref-1", active=False)

    await run(engine, [[raw_row(1)]])

    assert (await stored(engine, "1")).offer_id is None


async def test_a_rerun_changes_nothing_not_even_updated_at(engine: AsyncEngine) -> None:
    await run(engine, [raw_rows(1, 3)])
    before = await rows(
        engine,
        "SELECT id, report_hash, updated_at FROM network_report_rows ORDER BY id",
    )

    report = await run(engine, [raw_rows(1, 3)], now=LATER)

    after = await rows(
        engine,
        "SELECT id, report_hash, updated_at FROM network_report_rows ORDER BY id",
    )
    assert (report.tally.inserted, report.tally.unchanged) == (0, 3)
    assert after == before
    seen = await rows(
        engine, "SELECT first_seen_at, last_seen_at FROM network_report_rows"
    )
    assert {(row.first_seen_at, row.last_seen_at) for row in seen} == {(NOW, LATER)}


async def test_a_changed_status_and_amount_update_the_same_row(
    engine: AsyncEngine,
) -> None:
    await run(engine, [[raw_row(1, commissionStatus="pending")]])
    original = await stored(engine, "1")

    report = await run(
        engine,
        [
            [
                raw_row(
                    1,
                    commissionStatus="approved",
                    commissionAmount={"amount": 2, "currency": "GBP"},
                )
            ]
        ],
        now=LATER,
    )

    changed = await stored(engine, "1")
    assert (report.tally.updated, report.tally.inserted) == (1, 0)
    assert await scalar(engine, "SELECT count(*) FROM network_report_rows") == 1
    assert changed.id == original.id and changed.report_hash != original.report_hash
    assert changed.commission_status == "approved"
    assert changed.commission_amount_minor == 200
    assert changed.first_seen_at == NOW and changed.last_seen_at == LATER


async def test_deleting_the_offer_link_and_merchant_keeps_the_row_with_nulls(
    engine: AsyncEngine,
) -> None:
    ids = await seed_catalogue(engine)
    await add_link(engine, ids["offer"], tracking_id="ref-1")
    await run(engine, [[raw_row(1)]])
    matched = await stored(engine, "1")
    assert matched.offer_id and matched.affiliate_link_id and matched.merchant_id

    await execute(
        engine, "DELETE FROM affiliate_links WHERE id = :i", i=matched.affiliate_link_id
    )
    after_link = await stored(engine, "1")
    assert after_link.affiliate_link_id is None and after_link.offer_id == ids["offer"]

    await execute(engine, "DELETE FROM offers WHERE id = :i", i=ids["offer"])
    after_offer = await stored(engine, "1")
    assert after_offer.offer_id is None and after_offer.merchant_id == ids["merchant"]

    await execute(engine, "DELETE FROM merchants WHERE id = :i", i=ids["merchant"])
    after_merchant = await stored(engine, "1")
    assert after_merchant.merchant_id is None
    assert after_merchant.merchant_external_id == "77"
    assert after_merchant.commission_amount_minor == 150  # the revenue survives


async def test_a_completed_run_writes_one_audit_row_with_counts_only(
    engine: AsyncEngine,
) -> None:
    await run(engine, [[raw_row(1), raw_row(2, commissionStatus="refunded")]])

    (audit,) = await rows(
        engine,
        "SELECT user_id, action, resource, resource_id, metadata FROM audit_logs",
    )
    assert audit.user_id is None
    assert audit.action == "admin.reports.imported"
    assert audit.resource == "network_report_rows" and audit.resource_id is None
    assert audit.metadata["actor"] == "commands.import_awin_reports"
    assert audit.metadata["details"] == {
        "status": "completed",
        "from": "2026-11-03",
        "to": "2026-11-03",
        "pages": 1,
        "fetched": 2,
        "valid": 1,
        "inserted": 1,
        "updated": 0,
        "unchanged": 0,
        "unmatched": 1,
        "rejected_known": 0,
        "skipped": {"unknown_status": 1},
        "match_problems": {},
        "stopped_early": None,
        "resume_from": None,
    }
    text_of_row = repr(audit.metadata)
    for fragment in ("ref-1", "GBP", "approved"):
        assert fragment not in text_of_row


async def test_a_failure_after_a_committed_page_leaves_a_failed_audit_row(
    engine: AsyncEngine,
) -> None:
    from app.core.awin_client import AwinError, AwinReason

    with pytest.raises(ReportRunFailed):
        await run(engine, [raw_rows(1, 200), AwinError(AwinReason.SERVER_ERROR)])

    assert await scalar(engine, "SELECT count(*) FROM network_report_rows") == 200
    details = await scalar(engine, "SELECT metadata->'details' FROM audit_logs")
    assert details["status"] == "failed" and details["cause"] == "server_error"


async def test_a_status_change_keeps_a_stored_match_even_when_the_link_is_deactivated(
    engine: AsyncEngine,
) -> None:
    ids = await seed_catalogue(engine)
    link = await add_link(engine, ids["offer"], tracking_id="ref-1")
    await run(engine, [[raw_row(1, commissionStatus="pending")]])
    await execute(
        engine, "UPDATE affiliate_links SET active = false WHERE id = :i", i=link
    )
    await add_link(engine, ids["offer"], tracking_id="ref-1")  # a second active carrier

    report = await run(
        engine,
        [
            [
                raw_row(
                    1,
                    commissionStatus="approved",
                    commissionAmount={"amount": 2, "currency": "GBP"},
                )
            ]
        ],
        now=LATER,
    )

    changed = await stored(engine, "1")
    assert report.tally.updated == 1
    assert (changed.offer_id, changed.affiliate_link_id) == (ids["offer"], link)
    assert (
        changed.commission_status == "approved"
        and changed.commission_amount_minor == 200
    )


async def test_a_changed_click_reference_is_matched_again(engine: AsyncEngine) -> None:
    ids = await seed_catalogue(engine)
    await add_link(engine, ids["offer"], tracking_id="ref-1")
    second = await add_link(engine, ids["offer"], tracking_id="ref-new")
    await run(engine, [[raw_row(1)]])

    await run(
        engine,
        [[raw_row(1, clickRefs={"clickRef": "ref-new"}, commissionStatus="paid")]],
        now=LATER,
    )

    assert (await stored(engine, "1")).affiliate_link_id == second


@pytest.mark.parametrize("path", ["tracking_id", "destination"])
async def test_another_advertisers_link_never_matches_and_is_counted(
    engine: AsyncEngine, path: str
) -> None:
    ids = await seed_catalogue(engine)
    if path == "tracking_id":
        await add_link(engine, ids["offer"], tracking_id="ref-1")
    else:
        await add_link(
            engine,
            ids["offer"],
            destination="https://www.awin1.com/cread.php?awinmid=77&awinaffid=424242&clickref=ref-1",
        )

    report = await run(engine, [[raw_row(1, advertiserId=88)]])

    row_ = await stored(engine, "1")
    assert row_.offer_id is None and row_.affiliate_link_id is None
    assert dict(report.tally.match_problems) == {"merchant_mismatch": 1}


async def test_more_carriers_than_the_cap_match_nothing_whatever_their_ids(
    engine: AsyncEngine,
) -> None:
    ids = await seed_catalogue(engine)
    base = "https://www.awin1.com/cread.php?awinmid=77&awinaffid=424242&clickref="
    await add_link(engine, ids["offer"], destination=base + "ref-1", link_id="a-first")
    await add_link(engine, ids["offer"], destination=base + "ref-1", link_id="z-last")
    for n in range(MAX_CANDIDATES):
        await add_link(
            engine, ids["offer"], destination=base + "ref-1x", link_id=f"m-{n:03d}"
        )

    report = await run(engine, [[raw_row(1, clickRefs={"clickRef": "ref-1"})]])

    assert (await stored(engine, "1")).offer_id is None
    assert dict(report.tally.match_problems) == {"too_many_candidates": 1}


async def test_a_link_with_the_reference_only_in_slot_two_does_not_match(
    engine: AsyncEngine,
) -> None:
    ids = await seed_catalogue(engine)
    await add_link(
        engine,
        ids["offer"],
        destination="https://www.awin1.com/cread.php?awinmid=77&awinaffid=424242&clickref2=ref-1",
    )

    await run(engine, [[raw_row(1, clickRefs={"clickRef": "ref-1"})]])

    assert (await stored(engine, "1")).offer_id is None


async def test_an_approved_transaction_that_is_later_deleted_updates_the_row(
    engine: AsyncEngine,
) -> None:
    await run(engine, [[raw_row(1, commissionStatus="approved")]])

    report = await run(engine, [[raw_row(1, commissionStatus="deleted")]], now=LATER)

    assert (report.tally.updated, report.tally.inserted) == (1, 0)
    assert (await stored(engine, "1")).commission_status == "deleted"


async def test_a_stored_row_that_later_fails_validation_is_left_alone_and_counted(
    engine: AsyncEngine,
) -> None:
    await run(engine, [[raw_row(1), raw_row(2)]])
    before = await stored(engine, "1")

    report = await run(
        engine,
        [[raw_row(1, commissionAmount={"amount": -1, "currency": "GBP"}), raw_row(2)]],
        now=LATER,
    )

    after = await stored(engine, "1")
    assert report.tally.rejected_known == 1 and report.tally.unchanged == 1
    assert (after.report_hash, after.commission_amount_minor) == (
        before.report_hash,
        before.commission_amount_minor,
    )


async def test_updated_at_advances_on_a_real_update_but_last_seen_at_never_moves_back(
    engine: AsyncEngine,
) -> None:
    await run(engine, [[raw_row(1, commissionStatus="pending")]], now=LATER)
    first = await stored(engine, "1")

    await run(
        engine, [[raw_row(1, commissionStatus="approved")]], now=NOW
    )  # an older run

    second = await stored(engine, "1")
    assert second.updated_at > first.updated_at
    assert second.commission_status == "approved"
    assert second.last_seen_at == LATER  # GREATEST: not rewound to NOW


async def test_a_response_longer_than_a_page_is_written_in_chunks_and_not_asked_for_again(
    engine: AsyncEngine,
) -> None:
    from app.services.network_report_run import PAGE_SIZE

    client = FakeAwinClient(transaction_pages=[raw_rows(1, PAGE_SIZE + 50)])
    maker = async_sessionmaker(engine, expire_on_commit=False)

    report = await run_report_import(
        client, database_units(maker), RunOptions(start=DAY, end=DAY), NOW
    )

    assert len(client.transaction_requests) == 1
    assert report.tally.inserted == PAGE_SIZE + 50
    assert (
        await scalar(engine, "SELECT count(*) FROM network_report_rows")
        == PAGE_SIZE + 50
    )


async def test_a_real_run_that_stops_at_max_pages_fails_and_leaves_an_incomplete_audit_row(
    engine: AsyncEngine,
) -> None:
    with pytest.raises(ReportRunFailed) as stopped:
        await run(engine, [raw_rows(1, 200), raw_rows(1000, 200)], max_pages=1)

    assert stopped.value.cause == "max_pages_reached"
    assert await scalar(engine, "SELECT count(*) FROM network_report_rows") == 200
    details = await scalar(engine, "SELECT metadata->'details' FROM audit_logs")
    assert details["status"] == "incomplete" and details["resume_from"] == "2026-11-03"


# --- locks ---------------------------------------------------------------------------------


async def test_a_run_waits_for_the_run_lock_then_fails_as_lock_timeout(
    engine: AsyncEngine,
) -> None:
    holder = await engine.connect()
    try:
        transaction = await holder.begin()
        await holder.execute(
            text("SELECT pg_advisory_xact_lock(:k)"), {"k": REPORT_LOCK_KEY}
        )

        with pytest.raises(ReportRunFailed) as failed:
            await run(engine, [[raw_row(1)]], lock_timeout_ms=300)
        await transaction.rollback()
    finally:
        await holder.close()

    assert failed.value.cause == "lock_timeout"
    assert await scalar(engine, "SELECT count(*) FROM network_report_rows") == 0


async def test_two_simultaneous_runs_take_turns_and_leave_one_row_each(
    engine: AsyncEngine,
) -> None:
    page = raw_rows(1, 25)

    first, second = await asyncio.gather(run(engine, [page]), run(engine, [page]))

    assert await scalar(engine, "SELECT count(*) FROM network_report_rows") == 25
    assert first.tally.inserted + second.tally.inserted == 25
    assert first.tally.unchanged + second.tally.unchanged == 25


async def test_the_offers_import_lock_does_not_block_a_report_run(
    engine: AsyncEngine,
) -> None:
    from app.repositories.awin_import import IMPORT_LOCK_KEY

    holder = await engine.connect()
    try:
        transaction = await holder.begin()
        await holder.execute(
            text("SELECT pg_advisory_xact_lock(:k)"), {"k": IMPORT_LOCK_KEY}
        )

        report = await run(engine, [[raw_row(1)]], lock_timeout_ms=300)
        await transaction.rollback()
    finally:
        await holder.close()

    assert report.tally.inserted == 1


# --- the command line ----------------------------------------------------------------------


def _cli(*args: str, environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "tests.commands.awin_reports_cli_runner", *args],
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
    window = ["--from", "2026-11-03", "--to", "2026-11-03"]

    first = await asyncio.to_thread(_cli, "run", "3", "-", *window, environment=env)
    assert first.returncode == 0, first.stderr
    assert "inserted 3" in first.stdout and first.stderr == ""
    assert await scalar(engine, "SELECT count(*) FROM network_report_rows") == 3

    second = await asyncio.to_thread(_cli, "run", "3", "-", *window, environment=env)
    assert second.returncode == 0 and "unchanged 3" in second.stdout

    moved = await asyncio.to_thread(
        _cli, "run", "3", "declined", *window, environment=env
    )
    assert moved.returncode == 0 and "updated 3" in moved.stdout
    assert (
        await scalar(
            engine,
            "SELECT count(*) FROM network_report_rows WHERE commission_status = 'declined'",
        )
        == 3
    )
    assert await scalar(engine, "SELECT count(*) FROM audit_logs") == 3


async def test_a_dry_run_from_the_command_line_writes_nothing(
    engine: AsyncEngine, database_url: str
) -> None:
    finished = await asyncio.to_thread(
        _cli,
        "run",
        "3",
        "-",
        "--from",
        "2026-11-03",
        "--to",
        "2026-11-03",
        "--dry-run",
        environment=_environment(database_url),
    )

    assert finished.returncode == 0, finished.stderr
    assert "dry run, nothing was written" in finished.stdout
    assert await scalar(engine, "SELECT count(*) FROM network_report_rows") == 0
    assert await scalar(engine, "SELECT count(*) FROM audit_logs") == 0


async def test_the_real_entry_point_exits_2_without_settings_and_never_builds_a_client(
    engine: AsyncEngine, database_url: str
) -> None:
    finished = await asyncio.to_thread(
        subprocess.run,
        [
            sys.executable,
            "-m",
            "commands.import_awin_reports",
            "--from",
            "2026-11-03",
            "--to",
            "2026-11-03",
            "--dry-run",
        ],
        cwd=BACKEND_ROOT,
        env=_environment(database_url),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert finished.returncode == 2
    assert "AWIN_PUBLISHER_ID" in finished.stderr
    assert "AWIN_OAUTH_API_TOKEN" in finished.stderr
