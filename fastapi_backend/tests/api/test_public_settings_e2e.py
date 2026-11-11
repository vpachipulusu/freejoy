"""End to end: the public currency read on a real Postgres.

Runs only when ``TEST_DATABASE_URL`` points at a throwaway local database whose name ends
in "_test" (same contract as ``test_storefront_e2e.py``). The route, service and SQL are
the production code; it proves set, unset and unknown values, and that no other setting leaks.
"""

import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.api.deps import get_session_provider
from app.core.database import DefaultSessionProvider, build_engine
from app.core.ids import new_id
from app.main import create_app
from tests.alembic_support import run_alembic

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
BACKEND_ROOT = Path(__file__).resolve().parents[2]
KEY = "test-only-internal-service-key-not-for-deployment"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="TEST_DATABASE_URL is not set"
)


def _migrate(url: str, *args: str) -> None:
    run_alembic(url, *args)


@pytest.fixture(scope="module")
def database_url() -> Iterator[str]:
    assert TEST_DATABASE_URL is not None
    target = make_url(TEST_DATABASE_URL)
    assert (target.database or "").endswith("_test"), (
        "refusing to rebuild a database whose name does not end in _test"
    )
    assert target.host in LOCAL_HOSTS, "refusing to rebuild a non-local database"
    _migrate(TEST_DATABASE_URL, "downgrade", "base")
    _migrate(TEST_DATABASE_URL, "upgrade", "head")
    yield TEST_DATABASE_URL


@pytest.fixture
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    built = build_engine(database_url)
    async with built.begin() as connection:
        await connection.execute(text("DELETE FROM app_settings"))
    yield built
    await built.dispose()


@pytest.fixture
async def client(engine: AsyncEngine) -> AsyncIterator[AsyncClient]:
    app = create_app()
    provider = DefaultSessionProvider(
        async_sessionmaker(engine, expire_on_commit=False)
    )
    app.dependency_overrides[get_session_provider] = lambda: provider
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(
        transport=transport, base_url="http://test", headers={"X-Internal-Key": KEY}
    ) as http:
        yield http


async def seed_setting(engine: AsyncEngine, key: str, json_value: str) -> None:
    """Insert one row; ``json_value`` is JSON text, as the admin settings action stores it."""
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO app_settings (id, key, value, category) "
                "VALUES (:id, :key, CAST(:value AS jsonb), 'general')"
            ),
            {"id": new_id(), "key": key, "value": json_value},
        )


async def test_stored_currency_is_returned(
    client: AsyncClient, engine: AsyncEngine
) -> None:
    await seed_setting(engine, "currency", '"EUR"')

    response = await client.get("/v1/settings/currency")

    assert response.status_code == 200
    assert response.json() == {"success": True, "data": {"currency": "EUR"}}


async def test_missing_row_answers_the_default(client: AsyncClient) -> None:
    response = await client.get("/v1/settings/currency")

    assert response.status_code == 200
    assert response.json()["data"] == {"currency": "GBP"}


@pytest.mark.parametrize("stored", ['"XYZ"', "42", "null", '["EUR"]', '{"a": 1}'])
async def test_unknown_or_non_string_value_answers_the_default(
    client: AsyncClient, engine: AsyncEngine, stored: str
) -> None:
    await seed_setting(engine, "currency", stored)

    response = await client.get("/v1/settings/currency")

    assert response.json()["data"] == {"currency": "GBP"}


async def test_other_settings_never_appear(
    client: AsyncClient, engine: AsyncEngine
) -> None:
    await seed_setting(engine, "currency", '"GBP"')
    await seed_setting(engine, "support_email", '"private@example.com"')

    response = await client.get("/v1/settings/currency")

    assert response.json()["data"] == {"currency": "GBP"}
    assert "private@example.com" not in response.text
    assert "support_email" not in response.text
