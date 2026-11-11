"""``tests.alembic_support``: a stalled or failing Alembic child ends the fixture with a clear error."""

import subprocess
from typing import Any

import pytest

from tests import alembic_support
from tests.alembic_support import AlembicFailed, AlembicStalled, run_alembic

URL = "postgresql+asyncpg://postgres:pw@localhost:5433/example_gw1_test"


@pytest.fixture(autouse=True)
def no_database(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests exercise the subprocess handling only: nothing here connects to Postgres."""
    monkeypatch.setattr(alembic_support, "end_leftover_sessions", lambda url: 0)
    monkeypatch.setattr(
        alembic_support,
        "describe_sessions",
        lambda url: "  pid 42 [x] idle in transaction",
    )


def test_a_child_that_outlives_the_limit_fails_naming_the_command_the_database_and_the_sessions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def hang(*args: Any, **kwargs: Any) -> None:
        assert kwargs["timeout"] == alembic_support.ALEMBIC_TIMEOUT_SECONDS
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", hang)

    with pytest.raises(AlembicStalled) as stalled:
        run_alembic(URL, "downgrade", "base")

    message = str(stalled.value)
    assert "`alembic downgrade base`" in message
    assert "example_gw1_test" in message
    assert "pid 42 [x] idle in transaction" in message
    assert "postgres:pw" not in message  # the URL, and its password, never appear


def test_a_failing_child_reports_the_end_of_its_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failed = subprocess.CompletedProcess(
        [], 1, stdout="", stderr="x" * 5000 + "DROP TABLE users: lock timeout"
    )
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: failed)

    with pytest.raises(AlembicFailed) as error:
        run_alembic(URL, "upgrade", "head")

    message = str(error.value)
    assert "exited with 1" in message and message.endswith("lock timeout")
    assert len(message) < 2400


def test_a_child_that_succeeds_returns_quietly(monkeypatch: pytest.MonkeyPatch) -> None:
    done = subprocess.CompletedProcess([], 0, stdout="ok", stderr="")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: done)

    run_alembic(URL, "upgrade", "head")


def test_the_child_sees_the_database_in_database_url_direct(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    def record(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen.update(kwargs)
        return subprocess.CompletedProcess([], 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", record)

    run_alembic(URL, "upgrade", "head")

    assert seen["env"]["DATABASE_URL_DIRECT"] == URL
