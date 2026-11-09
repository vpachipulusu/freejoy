import json
from pathlib import Path

import pytest

from commands import check_openapi_schema, generate_openapi_schema
from tests._subprocess_support import (
    LEAKY_ENV_FILE,
    from_scratch_environment,
    run_module,
)

SCHEMA = {"openapi": "3.1.0", "paths": {}}


@pytest.fixture
def schema_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "openapi.json"
    monkeypatch.setattr(check_openapi_schema, "SCHEMA_FILE", path)
    return path


def fake_generator(schema: dict[str, object]):
    def generate(output_file: Path) -> None:
        output_file.write_text(json.dumps(schema))

    return generate


def test_fails_when_the_committed_schema_does_not_exist(
    schema_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert check_openapi_schema.main() == 1
    assert "missing" in capsys.readouterr().out


def test_passes_when_the_committed_schema_is_current(
    schema_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    schema_file.write_text(json.dumps(SCHEMA))
    monkeypatch.setattr(
        generate_openapi_schema, "generate_openapi_schema", fake_generator(SCHEMA)
    )

    assert check_openapi_schema.main() == 0


def test_fails_when_the_committed_schema_is_stale(
    schema_file: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    schema_file.write_text(json.dumps({"openapi": "3.0.0", "paths": {}}))
    monkeypatch.setattr(
        generate_openapi_schema, "generate_openapi_schema", fake_generator(SCHEMA)
    )

    assert check_openapi_schema.main() == 1
    assert "out of date" in capsys.readouterr().out


def test_the_check_gives_the_same_answer_whatever_the_shell_and_dotenv_hold(
    tmp_path: Path,
) -> None:
    clean_dir = tmp_path / "clean"
    dirty_dir = tmp_path / "dirty"
    clean_dir.mkdir()
    dirty_dir.mkdir()
    (dirty_dir / ".env").write_text(LEAKY_ENV_FILE, encoding="utf-8")

    clean = run_module(
        "commands.check_openapi_schema", clean_dir, from_scratch_environment()
    )
    dirty = run_module(
        "commands.check_openapi_schema",
        dirty_dir,
        from_scratch_environment(
            APP_NAME="Shell Brand",
            ENVIRONMENT="production",
            SENTRY_DSN="https://x@y.invalid/1",
        ),
    )

    # The first stdout line names a temporary file, so compare the verdict line only.
    assert clean.returncode == dirty.returncode
    assert clean.stdout.splitlines()[-1] == dirty.stdout.splitlines()[-1]
    assert clean.stderr == "" and dirty.stderr == ""
