"""``scripts/gate.sh``: a stage that hangs or fails ends the gate RED with a clear line and the tail of its output.

``GATE_SELF_TEST_COMMAND`` runs one command as a stage with the pytest stage's limit
(``GATE_PYTEST_TIMEOUT_SECONDS``) and nothing else, so these tests exercise the same ``step`` the real stages use.
"""

import os
import subprocess
from pathlib import Path

import pytest

from tests._subprocess_support import CHILD_TIMEOUT_SECONDS
from tests.test_gate_database_guard import BASH, REPO_ROOT

pytestmark = pytest.mark.skipif(BASH is None, reason="no usable bash")


def run_gate(
    tmp_path: Path, command: str, limit_seconds: int
) -> tuple[subprocess.CompletedProcess[str], str]:
    assert BASH is not None
    log = tmp_path / "gate.log"
    result = subprocess.run(
        [BASH, "scripts/gate.sh", str(log)],
        cwd=REPO_ROOT,
        env={
            **os.environ,
            "GATE_SELF_TEST_COMMAND": command,
            "GATE_PYTEST_TIMEOUT_SECONDS": str(limit_seconds),
        },
        capture_output=True,
        text=True,
        timeout=CHILD_TIMEOUT_SECONDS,
        check=False,
    )
    return result, log.read_text(encoding="utf-8", errors="replace")


def test_a_stage_over_its_limit_is_killed_and_the_gate_is_red(tmp_path: Path) -> None:
    result, log = run_gate(tmp_path, "sleep 120", 2)

    assert result.returncode == 1
    assert "self test TIMED OUT after 2 seconds and was killed" in result.stdout
    assert "=== GATE RESULT: RED" in result.stdout
    assert "TIMED OUT" in log


def test_a_stage_that_finishes_within_its_limit_is_not_touched(tmp_path: Path) -> None:
    result, _ = run_gate(tmp_path, "exit 0", 120)

    assert result.returncode == 0
    assert "TIMED OUT" not in result.stdout
    assert "=== GATE RESULT: GREEN" in result.stdout


def test_a_failing_stage_prints_the_end_of_its_output_to_the_console(
    tmp_path: Path,
) -> None:
    result, log = run_gate(tmp_path, "seq 1 200; exit 3", 60)

    assert result.returncode == 1
    assert "self test exit=3" in result.stdout
    lines = result.stdout.splitlines()
    assert "200" in lines and "199" in lines  # the end of the output
    assert "100" not in lines  # not all of it: the log has the rest
    assert "100" in log.splitlines()
    assert "=== GATE RESULT: RED" in result.stdout
