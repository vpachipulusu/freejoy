"""Seed a local database with sample catalogue data.

    uv run python -m commands.seed_catalogue

Loads the same 6 categories, 8 merchants and 24 offers as the Phase 1 fixtures (see
``commands/seed_data.py``) so the catalogue API and the admin screens have rows to work against.
Every row is sample data: merchants are fictional, offers carry ``SAMPLE_NOTE`` in
``publisher_notes`` and affiliate links point at ``https://example.com``.

* Fails closed: it runs only when ``ENVIRONMENT`` is exactly ``local`` or ``test``. Any other
  value, an empty one, an unset one (the setting then defaults to ``production``) or a
  configuration that cannot be loaded refuses with exit code 1 and writes nothing.
* Local database only: the ``DATABASE_URL`` host must be ``localhost``, ``127.0.0.1`` or
  ``::1``. Anything else (a remote host, a unix socket, an unparsable or empty URL) refuses.
* Idempotent: merchants and offers are upserted by slug and each offer keeps one sample
  affiliate link, so a second run creates no rows and a changed row is updated. Categories have
  no sample marker, so they are insert-only: an existing category is never modified.
* Never overwrites real rows: a merchant or offer that has a seed slug but is not sample data
  makes the run refuse and roll back.
* Dates are offsets from the day the command runs (``seed_data.OfferSeed``), so published sample
  offers stay live; a run on a later day moves the dates forward.

The database must already be migrated (``uv run alembic upgrade head``). Click events and email
subscribers are never seeded: they describe visitors, and there are none.
"""

import asyncio
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.models.platform import (
    AffiliateLink,
    Category,
    CommercialBasis,
    Merchant,
    Offer,
    OfferStatus,
)
from commands.seed_data import (
    CATEGORIES,
    MERCHANTS,
    SAMPLE_NOTE,
    SAMPLE_ORIGIN,
    SAMPLE_PROVIDER,
    OfferSeed,
    steps_for,
)
from commands.seed_data_offers import OFFERS

ALLOWED_ENVIRONMENTS = frozenset({"local", "test"})
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
# A query parameter can override the host in the URL, so a URL that sets one is refused.
HOST_QUERY_KEYS = frozenset({"host", "hostaddr"})
SAMPLE_MERCHANT_SUFFIX = " (Sample)"
# With the slash, so https://example.community and https://example.com.evil.invalid do not match.
SAMPLE_PREFIX = f"{SAMPLE_ORIGIN}/"
# Ended and archived sample offers keep their link, but it is switched off.
INACTIVE_LINK_STATUSES = frozenset({OfferStatus.EXPIRED, OfferStatus.ARCHIVED})


@dataclass(frozen=True)
class SeedSummary:
    """How many sample rows the catalogue holds after a run (not how many changed)."""

    categories: int
    merchants: int
    offers: int
    affiliate_links: int


def is_seed_allowed(environment: str | None) -> bool:
    """True only for the exact values ``local`` and ``test``; everything else fails closed."""
    return environment in ALLOWED_ENVIRONMENTS


def is_local_database(url: str | None) -> bool:
    """True only when the URL names a loopback host; everything else fails closed."""
    if not url:
        return False
    try:
        parsed = make_url(url)
    except (ArgumentError, ValueError):  # ValueError: a non-numeric port
        return False
    if not parsed.host or HOST_QUERY_KEYS & set(parsed.query):
        return False
    return parsed.host.strip("[]").lower() in LOCAL_HOSTS


class SeedConflict(Exception):
    """A row with a seed slug exists but is not sample data; it must not be overwritten."""

    def __init__(self, table: str, slug: str) -> None:
        super().__init__(f"{table} row '{slug}' is not sample data")
        self.table = table
        self.slug = slug


def _is_sample(row: Category | Merchant | Offer) -> bool:
    """Categories carry no marker, so they are never treated as sample rows."""
    if isinstance(row, Offer):
        return row.publisher_notes == SAMPLE_NOTE and (row.source_url or "").startswith(
            SAMPLE_PREFIX
        )
    if isinstance(row, Merchant):
        return row.name.endswith(SAMPLE_MERCHANT_SUFFIX) and (
            row.website or ""
        ).startswith(SAMPLE_PREFIX)
    return False


def _configured_database_url() -> str:
    """The effective ``DATABASE_URL``, or an empty string when settings cannot be loaded."""
    try:
        from app.core.config import settings

        return str(settings.DATABASE_URL)
    except Exception:  # noqa: BLE001  (any failure to load settings must refuse)
        return ""


def _configured_environment() -> str:
    """The effective ``ENVIRONMENT``, or an empty string when settings cannot be loaded."""
    try:
        from app.core.config import settings

        return str(settings.ENVIRONMENT)
    except Exception:  # noqa: BLE001  (any failure to load settings must refuse)
        return ""


async def _upsert_by_slug[Row: (Category, Merchant, Offer)](
    session: AsyncSession,
    model: type[Row],
    values_by_slug: Mapping[str, Mapping[str, Any]],
    *,
    update_existing: bool,
) -> dict[str, Row]:
    """Insert missing rows; update existing sample rows, or leave them alone.

    With ``update_existing`` an existing row that is not sample data raises ``SeedConflict``.
    """
    found = await session.scalars(
        select(model).where(model.slug.in_(list(values_by_slug)))
    )
    existing: dict[str, Row] = {row.slug: row for row in found}
    rows: dict[str, Row] = {}
    for slug, values in values_by_slug.items():
        row = existing.get(slug)
        if row is None:
            row = model(slug=slug, **values)
            session.add(row)
        elif update_existing:
            if not _is_sample(row):
                raise SeedConflict(model.__tablename__, slug)
            for name, value in values.items():
                setattr(row, name, value)
        rows[slug] = row
    await session.flush()
    return rows


def _category_values() -> dict[str, dict[str, Any]]:
    return {
        seed.slug: {
            "name": seed.name,
            "short_name": seed.short_name,
            "description": seed.description,
            "intro": seed.intro,
            "tint": seed.tint,
            "sort_order": seed.sort_order,
            "enabled": True,
        }
        for seed in CATEGORIES
    }


def _merchant_values() -> dict[str, dict[str, Any]]:
    return {
        seed.slug: {
            "name": seed.name,
            "website": f"{SAMPLE_ORIGIN}/merchants/{seed.slug}",
            "logo_url": None,
            "active": True,
        }
        for seed in MERCHANTS
    }


def _availability_text(template: str, end_at: datetime | None) -> str:
    """Fill ``{end}`` from the real end date so the text never goes stale on a later run."""
    if end_at is None:
        return template
    return template.replace("{end}", f"{end_at.day} {end_at:%B %Y}")


def _offer_values(
    seed: OfferSeed, category_id: str, merchant_id: str, midnight: datetime
) -> dict[str, Any]:
    def at(days: int | None) -> datetime | None:
        return None if days is None else midnight + timedelta(days=days)

    end_at = at(seed.end_days)

    return {
        "title": seed.title,
        "summary": seed.summary,
        "description": list(seed.description),
        "category_id": category_id,
        "merchant_id": merchant_id,
        "offer_type": seed.offer_type,
        # The sample catalogue is invented editorial copy: nobody pays for it.
        "commercial_basis": CommercialBasis.EDITORIAL,
        "status": seed.status,
        "featured": seed.featured,
        "image_tint": seed.image_tint,
        "image_label": seed.image_label,
        "image_alt": seed.image_alt,
        "source_url": f"{SAMPLE_ORIGIN}/offers/{seed.slug}",
        "postage_kind": seed.postage_kind,
        "postage_amount_minor": seed.postage_amount_minor,
        "postage_currency": seed.postage_currency,
        "requires_card": seed.requires_card,
        "subscription_terms": seed.subscription_terms,
        "eligibility": seed.eligibility,
        "availability": _availability_text(seed.availability, end_at),
        "steps": steps_for(seed.offer_type),
        "start_at": at(seed.start_days),
        "end_at": end_at,
        "last_verified_at": at(seed.verified_days),
        "freshness": seed.freshness,
        "publisher_notes": SAMPLE_NOTE,
    }


async def _upsert_affiliate_links(
    session: AsyncSession, offers: Mapping[str, Offer]
) -> int:
    """One sample link per offer, found by (offer, provider) so a rerun updates it."""
    found = await session.scalars(
        select(AffiliateLink).where(
            AffiliateLink.provider == SAMPLE_PROVIDER,
            AffiliateLink.offer_id.in_([offer.id for offer in offers.values()]),
        )
    )
    existing = {link.offer_id: link for link in found}
    for slug, offer in offers.items():
        values = {
            "destination_url": f"{SAMPLE_ORIGIN}/go/{slug}",
            "tracking_id": f"sample-{slug}",
            "active": offer.status not in INACTIVE_LINK_STATUSES,
        }
        link = existing.get(offer.id)
        if link is None:
            session.add(
                AffiliateLink(offer_id=offer.id, provider=SAMPLE_PROVIDER, **values)
            )
        else:
            for name, value in values.items():
                setattr(link, name, value)
    await session.flush()
    return len(offers)


async def seed_catalogue(session: AsyncSession, *, today: date) -> SeedSummary:
    """Upsert every sample row inside the caller's transaction (the caller commits)."""
    midnight = datetime(today.year, today.month, today.day, tzinfo=UTC)
    categories = await _upsert_by_slug(
        session, Category, _category_values(), update_existing=False
    )
    merchants = await _upsert_by_slug(
        session, Merchant, _merchant_values(), update_existing=True
    )
    offers = await _upsert_by_slug(
        session,
        Offer,
        {
            seed.slug: _offer_values(
                seed,
                categories[seed.category_slug].id,
                merchants[seed.merchant_slug].id,
                midnight,
            )
            for seed in OFFERS
        },
        update_existing=True,
    )
    links = await _upsert_affiliate_links(session, offers)
    return SeedSummary(len(categories), len(merchants), len(offers), links)


async def _run(
    maker: async_sessionmaker[AsyncSession],
    today: date,
    engine: AsyncEngine | None = None,
) -> SeedSummary:
    try:
        async with maker() as session, session.begin():
            return await seed_catalogue(session, today=today)
    finally:
        if engine is not None:
            await engine.dispose()


def _refuse(reason: str) -> int:
    print(f"seed-catalogue: refusing to run. {reason} Nothing was written.")
    return 1


def main(
    *,
    environment: str | None = None,
    database_url: str | None = None,
    maker: async_sessionmaker[AsyncSession] | None = None,
    today: date | None = None,
    argv: Sequence[str] | None = None,
) -> int:
    """Entry point. The keyword arguments exist for tests; the command line passes none."""
    if argv:
        print("seed-catalogue: takes no arguments.")
        return 2
    effective = _configured_environment() if environment is None else environment
    if not is_seed_allowed(effective):
        return _refuse(
            "ENVIRONMENT must be 'local' or 'test' (unset means production)."
        )
    url = _configured_database_url() if database_url is None else database_url
    try:
        if not is_local_database(url):
            return _refuse("DATABASE_URL must point at localhost, 127.0.0.1 or ::1.")
        engine: AsyncEngine | None = None
        if maker is None:
            from app.core.database import async_session_maker, engine

            maker = async_session_maker
        summary = asyncio.run(_run(maker, today or datetime.now(UTC).date(), engine))
    except SeedConflict as conflict:
        return _refuse(f"{conflict.table} row '{conflict.slug}' is not sample data.")
    except Exception as error:  # noqa: BLE001  (report the type only, never the message)
        # Only the type: a driver message can carry host and user details.
        print(
            f"seed-catalogue: failed ({type(error).__name__}). Check DATABASE_URL and "
            "run 'uv run alembic upgrade head' first. Nothing was written."
        )
        return 1
    print(
        f"seed-catalogue: {summary.categories} categories, {summary.merchants} merchants, "
        f"{summary.offers} offers and {summary.affiliate_links} affiliate links are in place."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(argv=sys.argv[1:]))
