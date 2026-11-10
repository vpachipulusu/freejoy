"""``commands.seed_catalogue``: the environment guard, the sample data and idempotency.

The guard and data tests need no database. The upsert tests run against the throwaway database
named by ``TEST_DATABASE_URL`` (a local ``*_test`` database, one per xdist worker) and are skipped
without it, like the ``*_e2e.py`` suites; CI provides one.
"""

import asyncio
import os
import re
import subprocess
import sys
from collections.abc import Awaitable, Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.core.config as config_module
from app.core.database import build_engine
from app.core.ids import new_id
from app.models.platform import (
    AffiliateLink,
    Category,
    CommercialBasis,
    Merchant,
    Offer,
    OfferStatus,
    OfferType,
    PostageKind,
)
from commands import seed_catalogue
from commands.seed_data import (
    CATEGORIES,
    MERCHANTS,
    SAMPLE_NOTE,
    SAMPLE_ORIGIN,
    steps_for,
)
from commands.seed_data_offers import OFFERS

BACKEND_ROOT = Path(__file__).resolve().parents[2]
FIXTURES_TS = (
    BACKEND_ROOT.parent
    / "nextjs-frontend"
    / "src"
    / "lib"
    / "catalogue"
    / "fixtures.ts"
)
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
TODAY = date(2026, 10, 29)
LOCAL_URL = "postgresql+asyncpg://user:pw@localhost:5432/freejoy"

needs_database = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="TEST_DATABASE_URL is not set"
)


class ForbiddenMaker:
    """A session maker that fails the test if the command ever reaches the database."""

    def __call__(self) -> Any:
        pytest.fail("the database must not be opened")


class LeakyMaker:
    """A session maker whose failure message carries a connection string."""

    def __call__(self) -> Any:
        raise RuntimeError("postgresql://user:hunter2@db.example.invalid/prod")


def refuse(capsys: pytest.CaptureFixture[str], **kwargs: Any) -> str:
    """Run ``main`` with a maker that must never be used; return its output."""
    exit_code = seed_catalogue.main(maker=ForbiddenMaker(), **kwargs)
    assert exit_code == 1
    return capsys.readouterr().out


# --- environment guard ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "environment",
    [
        "production",
        "staging",
        "preview",
        "development",
        "",
        "Local",
        " test",
        "TEST",
        "local ",
    ],
)
def test_refuses_to_run_outside_local_and_test(
    environment: str, capsys: pytest.CaptureFixture[str]
) -> None:
    output = refuse(capsys, environment=environment, database_url=LOCAL_URL)

    assert "refusing to run" in output
    assert "Nothing was written" in output


@pytest.mark.parametrize("environment", ["local", "test"])
def test_allows_exactly_local_and_test(environment: str) -> None:
    assert seed_catalogue.is_seed_allowed(environment)


def test_unknown_environment_is_not_allowed() -> None:
    assert not seed_catalogue.is_seed_allowed(None)
    assert not seed_catalogue.is_seed_allowed("")


def test_reads_the_environment_from_settings_when_not_given(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        config_module,
        "settings",
        SimpleNamespace(ENVIRONMENT="production", DATABASE_URL=LOCAL_URL),
    )

    assert "ENVIRONMENT must be" in refuse(capsys)


def test_refuses_when_settings_cannot_be_loaded(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delattr(config_module, "settings")

    assert "refusing to run" in refuse(capsys)


def test_refusal_never_prints_the_connection_string(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    secret = "postgresql+asyncpg://user:hunter2@db.example.invalid/prod"
    monkeypatch.setattr(
        config_module,
        "settings",
        SimpleNamespace(ENVIRONMENT="production", DATABASE_URL=secret),
    )

    output = refuse(capsys)

    assert "hunter2" not in output
    assert "postgresql" not in output


def test_takes_no_command_line_arguments(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = seed_catalogue.main(
        environment="local",
        database_url=LOCAL_URL,
        maker=ForbiddenMaker(),
        argv=["--force"],
    )

    assert exit_code == 2
    assert "no arguments" in capsys.readouterr().out


def test_reports_only_the_error_type_when_the_database_fails(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = seed_catalogue.main(
        environment="test", database_url=LOCAL_URL, maker=LeakyMaker()
    )

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "RuntimeError" in output
    assert "hunter2" not in output
    assert "db.example.invalid" not in output


REMOTE_URLS = [
    (
        "postgresql+asyncpg://neon_user:s3cret@"
        + "ep-cool-name-123456.eu-west-2.aws.neon.tech/neondb?sslmode=require",
        ("s3cret", "neon.tech", "neon_user"),
    ),
    (
        "postgresql+asyncpg://u:s3cret@db.remote.example.invalid:5432/freejoy_test",
        ("s3cret", "example.invalid", "freejoy_test"),
    ),
    (
        "postgresql+asyncpg://u:s3cret@localhost.example.invalid/freejoy",
        ("s3cret", "example.invalid"),
    ),
    ("postgresql+asyncpg://u:s3cret@127.0.0.2/freejoy", ("s3cret", "127.0.0.2")),
    ("postgresql+asyncpg://u:s3cret@10.0.0.5/freejoy", ("s3cret", "10.0.0.5")),
    ("postgresql+asyncpg:///freejoy?host=/var/run/postgresql", ("var/run",)),
    (
        "postgresql+asyncpg://u:s3cret@localhost/freejoy?host=db.remote.example.invalid",
        ("s3cret", "example.invalid"),
    ),
    ("", ()),
    ("not a url", ("not a url",)),
    (
        "postgresql+asyncpg://u:s3cret@localhost:5432,db.remote.invalid:5432/x",
        ("s3cret", "remote.invalid", "5432,"),
    ),
    ("postgresql+asyncpg://u:s3cret@localhost:abc/x", ("s3cret", "abc")),
]


@pytest.mark.parametrize(("url", "secrets"), REMOTE_URLS)
def test_refuses_a_database_that_is_not_on_this_machine(
    url: str, secrets: tuple[str, ...], capsys: pytest.CaptureFixture[str]
) -> None:
    output = refuse(capsys, environment="local", database_url=url)

    assert "refusing to run" in output
    assert "DATABASE_URL must point at localhost, 127.0.0.1 or ::1" in output
    assert "Nothing was written" in output
    assert "postgresql" not in output
    assert all(secret not in output for secret in secrets)


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+asyncpg://u:p@localhost/db",
        "postgresql+asyncpg://u:p@127.0.0.1:5433/db",
        "postgresql+asyncpg://u:p@[::1]:5432/db",
    ],
)
def test_loopback_databases_pass_the_locality_guard(
    url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = seed_catalogue.main(
        environment="test", database_url=url, maker=LeakyMaker()
    )

    assert seed_catalogue.is_local_database(url)
    assert exit_code == 1
    assert "RuntimeError" in capsys.readouterr().out


def test_an_unset_database_url_is_not_local() -> None:
    assert not seed_catalogue.is_local_database(None)
    assert not seed_catalogue.is_local_database("")


def test_refuses_a_remote_database_url_read_from_settings(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    remote = "postgresql+asyncpg://u:s3cret@db.remote.example.invalid/freejoy"
    monkeypatch.setattr(
        config_module,
        "settings",
        SimpleNamespace(ENVIRONMENT="local", DATABASE_URL=remote),
    )

    output = refuse(capsys)

    assert "DATABASE_URL must point at" in output
    assert "s3cret" not in output
    assert "example.invalid" not in output


def test_refuses_when_the_database_url_cannot_be_read(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(config_module, "settings", SimpleNamespace(ENVIRONMENT="local"))

    assert "DATABASE_URL must point at" in refuse(capsys)


# --- sample data ------------------------------------------------------------------------------


def test_seeds_the_phase_1_fixture_counts() -> None:
    assert len(CATEGORIES) == 6
    assert len(MERCHANTS) == 8
    assert len(OFFERS) == 24


def test_slugs_match_the_frontend_fixtures() -> None:
    if not FIXTURES_TS.exists():
        pytest.skip("the frontend fixtures are not checked out")
    fixture_slugs = set(re.findall(r"slug: '([^']+)'", FIXTURES_TS.read_text("utf-8")))
    seeded = (
        {c.slug for c in CATEGORIES}
        | {m.slug for m in MERCHANTS}
        | {o.slug for o in OFFERS}
    )
    assert seeded == fixture_slugs


def test_slugs_are_unique_per_kind() -> None:
    for seeds in (CATEGORIES, MERCHANTS, OFFERS):
        slugs = [seed.slug for seed in seeds]
        assert len(slugs) == len(set(slugs))


def test_every_offer_points_at_a_seeded_category_and_merchant() -> None:
    categories = {c.slug for c in CATEGORIES}
    merchants = {m.slug for m in MERCHANTS}
    assert all(o.category_slug in categories for o in OFFERS)
    assert all(o.merchant_slug in merchants for o in OFFERS)


def test_every_offer_type_and_status_is_represented() -> None:
    assert {o.offer_type for o in OFFERS} == set(OfferType)
    assert {o.status for o in OFFERS} == set(OfferStatus)


def test_merchant_names_are_marked_as_fictional_samples() -> None:
    assert all(m.name.endswith(" (Sample)") for m in MERCHANTS)


def test_published_offers_have_a_verification_date_not_in_the_future() -> None:
    published = [o for o in OFFERS if o.status is OfferStatus.PUBLISHED]
    assert published
    assert all(o.verified_days <= 0 for o in published)


def test_postage_follows_the_database_rule() -> None:
    for offer in OFFERS:
        if offer.postage_kind is PostageKind.COST:
            assert offer.postage_amount_minor is not None
            assert offer.postage_amount_minor >= 0
            assert offer.postage_currency is not None
            assert re.fullmatch(r"[A-Z]{3}", offer.postage_currency)
        else:
            assert offer.postage_amount_minor is None
            assert offer.postage_currency is None


def test_ended_offers_have_ended_and_live_offers_have_not() -> None:
    for offer in OFFERS:
        if offer.status in {OfferStatus.EXPIRED, OfferStatus.ARCHIVED}:
            assert offer.end_days is not None
            assert offer.end_days < 0
        if offer.status is OfferStatus.PUBLISHED and offer.end_days is not None:
            assert offer.end_days >= 0
        assert offer.end_days is None or offer.start_days < offer.end_days


def test_steps_are_available_for_every_offer_type() -> None:
    for offer_type in OfferType:
        steps = steps_for(offer_type)
        assert len(steps) == 3
        assert all(set(step) == {"title", "detail"} for step in steps)


def test_sample_data_names_no_real_destination() -> None:
    assert SAMPLE_ORIGIN == "https://example.com"
    assert SAMPLE_NOTE == "SAMPLE DATA — not a real offer"


# --- upserts against a real database ----------------------------------------------------------


def run[T](coroutine: Awaitable[T]) -> T:
    async def wrapped() -> T:
        return await coroutine

    return asyncio.run(wrapped())


def _migrate_to_head(url: str) -> None:
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_ROOT,
        env={**os.environ, "DATABASE_URL_DIRECT": url},
        check=True,
        capture_output=True,
    )


@pytest.fixture(scope="module")
def migrated_url() -> str:
    """The per-worker ``*_test`` database URL, migrated to head once for this module."""
    if TEST_DATABASE_URL is None:
        pytest.skip("TEST_DATABASE_URL is not set")
    target = make_url(TEST_DATABASE_URL)
    if not (target.database or "").endswith("_test"):
        pytest.fail("refusing to wipe a non-test database")
    if target.host not in LOCAL_HOSTS:
        pytest.fail("refusing to wipe a non-local database")
    _migrate_to_head(TEST_DATABASE_URL)
    return TEST_DATABASE_URL


@pytest.fixture
def maker(migrated_url: str) -> Iterator[async_sessionmaker[AsyncSession]]:
    """Session maker on an empty catalogue: tables are truncated before and after a test."""
    engine = build_engine(migrated_url)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    async def clear() -> None:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "TRUNCATE affiliate_links, click_events, offers, merchants, categories"
                    " RESTART IDENTITY CASCADE"
                )
            )

    run(clear())
    yield session_maker
    run(clear())
    run(engine.dispose())


async def _count(session_maker: async_sessionmaker[AsyncSession], model: Any) -> int:
    async with session_maker() as session:
        return int(await session.scalar(select(func.count()).select_from(model)) or 0)


def _counts(session_maker: async_sessionmaker[AsyncSession]) -> dict[str, int]:
    async def gather() -> dict[str, int]:
        return {
            "categories": await _count(session_maker, Category),
            "merchants": await _count(session_maker, Merchant),
            "offers": await _count(session_maker, Offer),
            "affiliate_links": await _count(session_maker, AffiliateLink),
        }

    return run(gather())


def _seed(session_maker: async_sessionmaker[AsyncSession], today: date = TODAY) -> int:
    return seed_catalogue.main(
        environment="test",
        database_url=TEST_DATABASE_URL,
        maker=session_maker,
        today=today,
    )


EXPECTED_COUNTS = {"categories": 6, "merchants": 8, "offers": 24, "affiliate_links": 24}


@needs_database
@pytest.mark.parametrize("environment", ["local", "test"])
def test_seeds_an_empty_database_in_local_and_test(
    maker: async_sessionmaker[AsyncSession],
    environment: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = seed_catalogue.main(
        environment=environment,
        database_url=TEST_DATABASE_URL,
        maker=maker,
        today=TODAY,
    )

    assert exit_code == 0
    assert _counts(maker) == EXPECTED_COUNTS
    assert "6 categories, 8 merchants, 24 offers and 24 affiliate links" in (
        capsys.readouterr().out
    )


@needs_database
def test_refusal_writes_nothing(maker: async_sessionmaker[AsyncSession]) -> None:
    exit_code = seed_catalogue.main(
        environment="production",
        database_url=TEST_DATABASE_URL,
        maker=maker,
        today=TODAY,
    )

    assert exit_code == 1
    assert all(count == 0 for count in _counts(maker).values())


@needs_database
def test_second_run_creates_no_duplicates(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    async def ids() -> set[str]:
        async with maker() as session:
            rows = await session.scalars(select(Offer.id))
            return set(rows)

    assert _seed(maker) == 0
    first_counts, first_ids = _counts(maker), run(ids())

    assert _seed(maker) == 0

    assert _counts(maker) == first_counts == EXPECTED_COUNTS
    assert run(ids()) == first_ids


@needs_database
def test_every_offer_is_sample_data_with_an_example_destination(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    async def load() -> tuple[list[Offer], list[AffiliateLink]]:
        async with maker() as session:
            offers = list(await session.scalars(select(Offer)))
            links = list(await session.scalars(select(AffiliateLink)))
            return offers, links

    ended = {"laundry-sheets-sample", "bedtime-hamper-prize-draw"}

    _seed(maker)
    offers, links = run(load())

    assert {o.publisher_notes for o in offers} == {SAMPLE_NOTE}
    assert {o.status for o in offers} == set(OfferStatus)
    assert {o.offer_type for o in offers} == set(OfferType)
    assert all(
        link.destination_url.startswith("https://example.com/") for link in links
    )
    assert all(
        o.source_url is None or o.source_url.startswith("https://example.com/")
        for o in offers
    )
    assert len({link.offer_id for link in links}) == 24
    slug_by_id = {o.id: o.slug for o in offers}
    assert {slug_by_id[link.offer_id] for link in links if not link.active} == ended


@needs_database
def test_dates_are_relative_to_the_day_the_command_runs(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    async def verified() -> dict[str, datetime]:
        async with maker() as session:
            rows = await session.execute(select(Offer.slug, Offer.last_verified_at))
            return {slug: when for slug, when in rows if when is not None}

    _seed(maker, TODAY)
    first = run(verified())
    _seed(maker, TODAY + timedelta(days=3))
    later = run(verified())

    assert first["glow-boosting-face-cream-sample"] == datetime(
        2026, 10, 27, tzinfo=UTC
    )
    assert later["glow-boosting-face-cream-sample"] == datetime(
        2026, 10, 30, tzinfo=UTC
    )
    assert len(later) == 24


@needs_database
def test_changed_rows_are_updated_by_slug(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    offer_slug = "coffee-discovery-pack"

    async def tamper_and_load() -> tuple[str, str]:
        async with maker() as session, session.begin():
            await session.execute(
                update(Offer).where(Offer.slug == offer_slug).values(title="Edited")
            )
            await session.execute(
                update(Merchant)
                .where(Merchant.slug == "good-beans")
                .values(name="X (Sample)")
            )
            await session.execute(
                update(Category).where(Category.slug == "pets").values(enabled=False)
            )
            await session.execute(
                update(AffiliateLink).values(destination_url="https://other.invalid/x")
            )
        return await _offer_id(maker, offer_slug), await _merchant_id(maker)

    _seed(maker)
    offer_id, merchant_id = run(tamper_and_load())

    assert _seed(maker) == 0

    async def load() -> tuple[Offer, Merchant, Category, list[str]]:
        async with maker() as session:
            offer = await session.scalar(select(Offer).where(Offer.slug == offer_slug))
            merchant = await session.scalar(
                select(Merchant).where(Merchant.slug == "good-beans")
            )
            category = await session.scalar(
                select(Category).where(Category.slug == "pets")
            )
            urls = list(await session.scalars(select(AffiliateLink.destination_url)))
            assert offer and merchant and category
            return offer, merchant, category, urls

    offer, merchant, category, urls = run(load())
    assert offer.title == "Coffee discovery pack"
    assert offer.id == offer_id
    assert merchant.name == "Good Beans (Sample)"
    assert merchant.id == merchant_id
    assert category.enabled is False  # categories are insert-only
    assert all(url.startswith("https://example.com/go/") for url in urls)
    assert _counts(maker) == EXPECTED_COUNTS


async def _offer_id(session_maker: async_sessionmaker[AsyncSession], slug: str) -> str:
    async with session_maker() as session:
        return str(await session.scalar(select(Offer.id).where(Offer.slug == slug)))


async def _merchant_id(session_maker: async_sessionmaker[AsyncSession]) -> str:
    async with session_maker() as session:
        return str(
            await session.scalar(
                select(Merchant.id).where(Merchant.slug == "good-beans")
            )
        )


@needs_database
def test_rows_with_other_slugs_are_left_alone(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    async def add_real_merchant() -> None:
        async with maker() as session, session.begin():
            session.add(Merchant(slug="not-a-sample", name="Kept"))

    async def names() -> set[str]:
        async with maker() as session:
            return set(await session.scalars(select(Merchant.name)))

    run(add_real_merchant())

    _seed(maker)

    assert "Kept" in run(names())
    assert _counts(maker)["merchants"] == 9


def _real_offer(slug: str, category_id: str, merchant_id: str) -> Offer:
    return Offer(
        slug=slug,
        title="Real title",
        summary="s",
        category_id=category_id,
        merchant_id=merchant_id,
        offer_type=OfferType.VOUCHER,
        commercial_basis=CommercialBasis.EDITORIAL,
        image_tint="blush",
        image_label="l",
        image_alt="a",
        postage_kind=PostageKind.FREE,
        eligibility="e",
        availability="a",
        publisher_notes="my own notes",
    )


def _real_category(slug: str, **overrides: Any) -> Category:
    values: dict[str, Any] = {
        "name": "Real",
        "short_name": "Real",
        "description": "d",
        "intro": "i",
        "tint": "sky",
    }
    return Category(slug=slug, **{**values, **overrides})


@needs_database
def test_a_real_offer_with_a_seed_slug_is_never_overwritten(
    maker: async_sessionmaker[AsyncSession], capsys: pytest.CaptureFixture[str]
) -> None:
    async def add_real_offer() -> None:
        async with maker() as session, session.begin():
            category = _real_category("real-cat", id=new_id())
            merchant = Merchant(id=new_id(), slug="real-merchant", name="Real")
            session.add_all([category, merchant])
            await session.flush()
            session.add(_real_offer("coffee-discovery-pack", category.id, merchant.id))

    async def title() -> str | None:
        async with maker() as session:
            return await session.scalar(
                select(Offer.title).where(Offer.slug == "coffee-discovery-pack")
            )

    run(add_real_offer())
    before = _counts(maker)

    exit_code = _seed(maker)

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "offers row 'coffee-discovery-pack' is not sample data" in output
    assert "Nothing was written" in output
    assert run(title()) == "Real title"
    assert _counts(maker) == before


@needs_database
def test_a_real_merchant_with_a_seed_slug_is_never_overwritten(
    maker: async_sessionmaker[AsyncSession], capsys: pytest.CaptureFixture[str]
) -> None:
    async def add_real_merchant() -> None:
        async with maker() as session, session.begin():
            session.add(Merchant(slug="tea-time", name="Tea Time"))

    run(add_real_merchant())

    assert _seed(maker) == 1

    assert "merchants row 'tea-time' is not sample data" in capsys.readouterr().out
    assert _counts(maker) == {
        "categories": 0,
        "merchants": 1,
        "offers": 0,
        "affiliate_links": 0,
    }


@needs_database
def test_an_existing_category_is_never_modified(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    async def add_category() -> None:
        async with maker() as session, session.begin():
            session.add(
                _real_category("beauty", name="Custom", enabled=False, sort_order=9)
            )

    async def load() -> Category:
        async with maker() as session:
            found = await session.scalar(
                select(Category).where(Category.slug == "beauty")
            )
            assert found is not None
            return found

    run(add_category())

    assert _seed(maker) == 0

    beauty = run(load())
    assert (beauty.name, beauty.tint, beauty.enabled, beauty.sort_order) == (
        "Custom",
        "sky",
        False,
        9,
    )
    assert _counts(maker) == EXPECTED_COUNTS
