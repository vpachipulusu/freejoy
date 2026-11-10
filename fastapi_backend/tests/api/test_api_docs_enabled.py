"""FREEJ-48: ``API_DOCS_ENABLED`` serves the docs in production during the testing phase."""

import logging
from collections.abc import Iterator

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings, settings
from app.main import create_app

DOCS_PATHS = ["/docs", "/redoc", "/openapi.json"]
MAIN_LOGGER = "app.main"


class RecordingHandler(logging.Handler):
    """Collects the records of one logger (create_app replaces the root handlers)."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def main_log() -> Iterator[RecordingHandler]:
    handler = RecordingHandler()
    logger = logging.getLogger(MAIN_LOGGER)
    logger.addHandler(handler)
    yield handler
    logger.removeHandler(handler)


def _configure(
    monkeypatch: pytest.MonkeyPatch,
    *,
    environment: str,
    vercel_env: str | None = None,
    docs_enabled: bool,
) -> None:
    monkeypatch.setattr(settings, "ENVIRONMENT", environment)
    monkeypatch.setattr(settings, "VERCEL_ENV", vercel_env)
    monkeypatch.setattr(settings, "API_DOCS_ENABLED", docs_enabled)


async def _get(path: str) -> tuple[int, dict[str, object] | None, str]:
    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://test"
    ) as http:
        response = await http.get(path)
    is_json = response.headers.get("content-type", "").startswith("application/json")
    return response.status_code, response.json() if is_json else None, response.text


def _warnings(handler: RecordingHandler) -> list[logging.LogRecord]:
    return [r for r in handler.records if r.levelno == logging.WARNING]


def test_the_setting_is_off_by_default() -> None:
    assert Settings.model_fields["API_DOCS_ENABLED"].default is False


@pytest.mark.parametrize("path", DOCS_PATHS)
async def test_production_serves_no_docs_by_default(
    monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    _configure(monkeypatch, environment="production", docs_enabled=False)

    status, body, _ = await _get(path)

    assert status == 404
    assert body is not None
    assert body["success"] is False
    assert isinstance(body["error"], str)


@pytest.mark.parametrize("path", ["/docs", "/redoc"])
async def test_production_serves_the_docs_ui_when_the_setting_is_true(
    monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    _configure(monkeypatch, environment="production", docs_enabled=True)

    status, _, text = await _get(path)

    assert status == 200
    assert "<html" in text.lower()


async def test_production_serves_the_schema_at_the_openapi_url_when_the_setting_is_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch, environment="production", docs_enabled=True)
    monkeypatch.setattr(settings, "OPENAPI_URL", "/custom-schema.json")

    status, body, _ = await _get("/custom-schema.json")

    assert status == 200
    assert body is not None
    assert body["info"]["title"] == settings.APP_NAME  # type: ignore[index]


@pytest.mark.parametrize("path", DOCS_PATHS)
@pytest.mark.parametrize(
    ("environment", "vercel_env"),
    [("local", None), ("test", None), ("production", "preview")],
)
@pytest.mark.parametrize("docs_enabled", [False, True])
async def test_relaxed_environments_serve_the_docs_either_way(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    environment: str,
    vercel_env: str | None,
    docs_enabled: bool,
) -> None:
    _configure(
        monkeypatch,
        environment=environment,
        vercel_env=vercel_env,
        docs_enabled=docs_enabled,
    )

    status, _, _ = await _get(path)

    assert status == 200


@pytest.mark.parametrize(
    ("environment", "vercel_env"), [("production", None), ("local", "production")]
)
async def test_a_warning_is_logged_once_when_production_serves_docs(
    monkeypatch: pytest.MonkeyPatch,
    main_log: RecordingHandler,
    environment: str,
    vercel_env: str | None,
) -> None:
    _configure(
        monkeypatch, environment=environment, vercel_env=vercel_env, docs_enabled=True
    )

    create_app()

    warnings = _warnings(main_log)
    assert len(warnings) == 1
    assert "API_DOCS_ENABLED" in warnings[0].getMessage()


@pytest.mark.parametrize(
    ("environment", "vercel_env"),
    [("local", None), ("test", None), ("production", "preview")],
)
@pytest.mark.parametrize("docs_enabled", [False, True])
def test_no_warning_is_logged_in_relaxed_environments(
    monkeypatch: pytest.MonkeyPatch,
    main_log: RecordingHandler,
    environment: str,
    vercel_env: str | None,
    docs_enabled: bool,
) -> None:
    _configure(
        monkeypatch,
        environment=environment,
        vercel_env=vercel_env,
        docs_enabled=docs_enabled,
    )

    create_app()

    assert _warnings(main_log) == []


def test_no_warning_is_logged_in_production_with_the_docs_off(
    monkeypatch: pytest.MonkeyPatch, main_log: RecordingHandler
) -> None:
    _configure(monkeypatch, environment="production", docs_enabled=False)

    create_app()

    assert _warnings(main_log) == []
