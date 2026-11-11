"""The switch that stops a local ``.env`` file from reaching settings (FREEJ-45)."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.core import env_file
from app.core.config import Settings
from tests._subprocess_support import (
    BACKEND_ROOT,
    CHILD_TIMEOUT_SECONDS,
    inherited_environment,
)

LEAKED = "Leaked From Dotenv"


def _write_env_file(directory: Path, *lines: str) -> Path:
    path = directory / ".env"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _read_app_name(directory: Path, environment: dict[str, str]) -> str:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from app.core.config import settings; print(settings.APP_NAME)",
        ],
        cwd=directory,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=CHILD_TIMEOUT_SECONDS,
    )
    return result.stdout.strip()


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "Yes", "on"])
def test_the_env_file_is_ignored_for_a_truthy_value(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv(env_file.IGNORE_ENV_FILE, value)

    assert env_file.settings_env_file() is None


@pytest.mark.parametrize("value", [None, "", "0", "false", "no", "off", "nonsense"])
def test_the_env_file_is_read_unless_the_switch_is_truthy(
    monkeypatch: pytest.MonkeyPatch, value: str | None
) -> None:
    if value is None:
        monkeypatch.delenv(env_file.IGNORE_ENV_FILE, raising=False)
    else:
        monkeypatch.setenv(env_file.IGNORE_ENV_FILE, value)

    assert env_file.settings_env_file() == ".env"


def test_load_env_file_does_nothing_while_the_switch_is_on(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = _write_env_file(tmp_path, f"ENV_FILE_PROBE={LEAKED}")
    monkeypatch.setenv(env_file.IGNORE_ENV_FILE, "1")
    monkeypatch.setenv("ENV_FILE_PROBE", "unset-me")
    monkeypatch.delenv("ENV_FILE_PROBE")

    env_file.load_env_file(path)

    assert "ENV_FILE_PROBE" not in os.environ


def test_load_env_file_reads_the_file_when_the_switch_is_off(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = _write_env_file(tmp_path, f"ENV_FILE_PROBE={LEAKED}")
    monkeypatch.delenv(env_file.IGNORE_ENV_FILE, raising=False)
    monkeypatch.setenv("ENV_FILE_PROBE", "unset-me")
    monkeypatch.delenv("ENV_FILE_PROBE")

    env_file.load_env_file(path)

    assert os.environ["ENV_FILE_PROBE"] == LEAKED


def test_a_dotenv_value_never_reaches_settings_under_pytest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_env_file(tmp_path, f"APP_NAME={LEAKED}", "ENVIRONMENT=production")
    monkeypatch.chdir(tmp_path)

    settings = Settings()  # type: ignore[call-arg]  # reads ".env" from the cwd unless ignored

    assert settings.APP_NAME == Settings.model_fields["APP_NAME"].default
    assert settings.ENVIRONMENT == "test"


def test_a_subprocess_started_by_a_test_ignores_the_dotenv_file(
    tmp_path: Path,
) -> None:
    _write_env_file(tmp_path, f"APP_NAME={LEAKED}")

    app_name = _read_app_name(tmp_path, inherited_environment())

    assert app_name == Settings.model_fields["APP_NAME"].default


def test_the_dotenv_file_is_still_read_outside_the_tests(tmp_path: Path) -> None:
    # Without the switch the same child process reads the file, so production and
    # local uvicorn behave as before and the two tests above really prove something.
    _write_env_file(tmp_path, f"APP_NAME={LEAKED}")

    app_name = _read_app_name(
        tmp_path,
        inherited_environment(**{env_file.IGNORE_ENV_FILE: None, "APP_NAME": None}),
    )

    assert app_name == LEAKED


def test_alembic_reads_the_backend_env_file_not_one_found_by_searching_upwards(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A .env in a parent directory must never be picked up; only the backend's own file counts.
    backend = tmp_path / "backend"
    backend.mkdir()
    _write_env_file(tmp_path, f"ENV_FILE_PROBE={LEAKED}")
    monkeypatch.setattr(env_file, "BACKEND_ENV_FILE", backend / ".env")
    monkeypatch.delenv(env_file.IGNORE_ENV_FILE, raising=False)
    monkeypatch.setenv("ENV_FILE_PROBE", "unset-me")
    monkeypatch.delenv("ENV_FILE_PROBE")

    assert env_file.load_env_file() is False

    assert "ENV_FILE_PROBE" not in os.environ


def test_the_default_env_file_is_fastapi_backend_slash_dot_env() -> None:
    assert env_file.BACKEND_ENV_FILE == BACKEND_ROOT / ".env"
