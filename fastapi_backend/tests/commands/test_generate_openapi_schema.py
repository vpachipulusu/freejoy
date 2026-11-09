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
    app = mocker.patch("commands.generate_openapi_schema.app")
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


BACKEND_ROOT = Path(__file__).resolve().parents[2]


def _run_schema_command(
    module: str, cwd: Path, *args: str, **environment: str | None
) -> subprocess.CompletedProcess[str]:
    """Run a schema command the way a developer does: no test switch, so only the command's own handling counts."""
    child = {**os.environ, "PYTHONPATH": str(BACKEND_ROOT)}
    child.pop("IGNORE_ENV_FILE", None)
    child.pop("OPENAPI_OUTPUT_FILE", None)
    for name, value in environment.items():
        if value is None:
            child.pop(name, None)
        else:
            child[name] = value
    return subprocess.run(
        [sys.executable, "-m", module, *args],
        cwd=cwd,
        env=child,
        capture_output=True,
        text=True,
        check=False,
    )


def _generate(cwd: Path, output: Path, **environment: str | None) -> dict[str, object]:
    result = _run_schema_command(
        "commands.generate_openapi_schema",
        cwd,
        OPENAPI_OUTPUT_FILE=str(output),
        **environment,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(output.read_text(encoding="utf-8"))


def test_the_generated_schema_does_not_depend_on_a_local_dotenv_file(
    tmp_path: Path,
) -> None:
    clean_dir = tmp_path / "clean"
    dirty_dir = tmp_path / "dirty"
    clean_dir.mkdir()
    dirty_dir.mkdir()
    (dirty_dir / ".env").write_text(
        'APP_NAME="Leaked Brand"\nAPP_VERSION=9.9.9\nOPENAPI_URL=\n',
        encoding="utf-8",
    )

    clean = _generate(clean_dir, tmp_path / "clean.json")
    dirty = _generate(dirty_dir, tmp_path / "dirty.json")

    assert dirty == clean
    info = dirty["info"]
    assert isinstance(info, dict)
    assert info["title"] == "Freejoy API"


def test_the_generated_schema_does_not_depend_on_an_exported_brand(
    tmp_path: Path,
) -> None:
    clean = _generate(tmp_path, tmp_path / "clean.json")

    exported = _generate(
        tmp_path,
        tmp_path / "exported.json",
        APP_NAME="Shell Brand",
        APP_VERSION="9.9.9",
    )

    assert exported == clean
