import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from commands.generate_openapi_schema import (
    generate_openapi_schema,
    remove_operation_id_tag,
)
from tests._subprocess_support import (
    CHILD_TIMEOUT_SECONDS,
    LEAKY_ENV_FILE,
    from_scratch_environment,
    run_module,
)


def load_json_file(filename):
    test_dir = os.path.dirname(__file__)
    file_path = os.path.join(test_dir, "files", filename)
    with open(file_path, "r") as f:
        return json.load(f)


@pytest.fixture
def sample_openapi_schema():
    return load_json_file("openapi_test.json")


@pytest.fixture
def expected_output_schema():
    return load_json_file("openapi_test_output.json")


def test_remove_operation_id_tag(sample_openapi_schema, expected_output_schema):
    cleaned_schema = remove_operation_id_tag(sample_openapi_schema)
    assert cleaned_schema == expected_output_schema


@pytest.fixture
def mock_app(mocker):
    app = mocker.patch("app.main.app")
    app.openapi.return_value = {
        "openapi": "3.1.0",
        "info": {"title": "FastAPI", "version": "0.1.0"},
        "paths": {},
    }
    return app


def test_generate_openapi_schema(mocker, mock_app):
    mock_remove_operation_id_tag = mocker.patch(
        "commands.generate_openapi_schema.remove_operation_id_tag"
    )
    mock_remove_operation_id_tag.return_value = {"mocked_schema": True}

    output_file = "openapi_test.json"
    expected_output = json.dumps({"mocked_schema": True}, indent=2)

    generate_openapi_schema(output_file)

    mock_app.openapi.assert_called_once()
    mock_remove_operation_id_tag.assert_called_once_with(mock_app.openapi.return_value)

    output_path = Path(output_file)
    assert output_path.is_file()
    # Bytes, not text: the schema is committed, so it must use LF on every platform.
    assert output_path.read_bytes() == (expected_output + "\n").encode()
    assert b"\r" not in output_path.read_bytes()

    output_path.unlink()


EXPORTED_SETTINGS = {
    "APP_NAME": "Shell Brand",
    "APP_VERSION": "9.9.9",
    "ENVIRONMENT": "production",
    "SENTRY_DSN": "https://fake@example.invalid/1",
    "sentry_dsn": "https://fake@example.invalid/2",
    "EMAIL_PROVIDER": "brevo",
    "DATABASE_URL": "postgresql+asyncpg://real:real@db.example.invalid/real",
}


def _generate(cwd: Path, output: Path, **exported: str | None) -> dict[str, object]:
    """Run the documented command from a shell that exports only ``exported``."""
    result = run_module(
        "commands.generate_openapi_schema",
        cwd,
        from_scratch_environment(OPENAPI_OUTPUT_FILE=str(output), **exported),
    )
    assert result.returncode == 0, result.stderr
    return json.loads(output.read_text(encoding="utf-8"))


def _title(schema: dict[str, object]) -> object:
    info = schema["info"]
    assert isinstance(info, dict)
    return info["title"]


def test_the_command_runs_from_a_shell_with_nothing_exported(tmp_path: Path) -> None:
    schema = _generate(tmp_path, tmp_path / "openapi.json")

    assert _title(schema) == "Freejoy API"


def test_the_generated_schema_does_not_depend_on_a_local_dotenv_file(
    tmp_path: Path,
) -> None:
    clean_dir = tmp_path / "clean"
    dirty_dir = tmp_path / "dirty"
    clean_dir.mkdir()
    dirty_dir.mkdir()
    (dirty_dir / ".env").write_text(LEAKY_ENV_FILE, encoding="utf-8")

    clean = _generate(clean_dir, tmp_path / "clean.json")
    dirty = _generate(dirty_dir, tmp_path / "dirty.json")

    assert dirty == clean
    assert _title(dirty) == "Freejoy API"


def test_the_generated_schema_does_not_depend_on_exported_settings(
    tmp_path: Path,
) -> None:
    clean = _generate(tmp_path, tmp_path / "clean.json")

    exported = _generate(tmp_path, tmp_path / "exported.json", **EXPORTED_SETTINGS)

    assert exported == clean


def test_importing_the_module_is_inert() -> None:
    # Importing must not touch os.environ or build the settings: the isolation runs when the
    # schema is generated, not when something merely imports the module.
    code = (
        "import os, sys\n"
        "before = dict(os.environ)\n"
        "import commands.generate_openapi_schema\n"
        "assert dict(os.environ) == before, 'the import changed os.environ'\n"
        "assert 'app.core.config' not in sys.modules, 'the import built the settings'\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        env=from_scratch_environment(),
        capture_output=True,
        text=True,
        timeout=CHILD_TIMEOUT_SECONDS,
        check=False,
    )

    assert result.returncode == 0, result.stderr
