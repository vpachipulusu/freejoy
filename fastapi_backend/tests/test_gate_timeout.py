"""``scripts/gate.sh``: a stage that hangs or fails ends with a clear line, the tail of its output, and no leftovers.

``GATE_SELF_TEST_COMMAND`` runs one command as a stage with the pytest stage's limit
(``GATE_PYTEST_TIMEOUT_SECONDS``) and nothing else, so these tests exercise the same ``step`` the real stages use.
It is never a gate result: the result line does not appear, and the exit code is 3 when the stage passed and 1
when it failed.
"""

import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests._subprocess_support import CHILD_TIMEOUT_SECONDS
from tests.test_gate_database_guard import BASH, REPO_ROOT

pytestmark = pytest.mark.skipif(BASH is None, reason="no usable bash")

MARKER_DELAY_SECONDS = 8
STAGE_LIMIT_SECONDS = 4


def run_gate(
    tmp_path: Path, command: str, limit_seconds: int, **environment: str
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
            **environment,
        },
        capture_output=True,
        text=True,
        timeout=CHILD_TIMEOUT_SECONDS,
        check=False,
    )
    return result, log.read_text(encoding="utf-8", errors="replace")


def mechanism(result: subprocess.CompletedProcess[str]) -> str:
    found = re.search(r"=== stage limits: (.*?);", result.stdout)
    assert found, result.stdout
    return found.group(1)


def test_a_stage_over_its_limit_is_ended_and_the_self_test_is_not_a_gate_result(
    tmp_path: Path,
) -> None:
    result, log = run_gate(tmp_path, "sleep 120", 2)

    if (
        mechanism(result) == "none"
    ):  # no way to end a stage here: the gate says so instead of pretending
        assert "limits are NOT enforced" in result.stdout
        pytest.skip("this machine has no way to end a stage")
    assert result.returncode == 1
    assert "self test TIMED OUT after 2 seconds and was ended" in result.stdout
    assert "self test exit=124 elapsed=" in result.stdout
    assert (
        "=== SELF TEST ONLY: no check runs; this is not a gate result" in result.stdout
    )
    assert "=== GATE SELF TEST: stage failed; NOT A GATE VERDICT" in result.stdout
    assert "GATE RESULT" not in result.stdout and "GATE RESULT" not in log
    assert "TIMED OUT" in log


def test_a_stage_that_exits_124_by_itself_is_not_called_a_timeout(
    tmp_path: Path,
) -> None:
    result, _ = run_gate(tmp_path, "exit 124", 60)

    assert (
        result.returncode == 1
    )  # it failed, and the stage's own 124 is not mistaken for the limit
    assert "TIMED OUT" not in result.stdout
    assert "self test exit=124" in result.stdout


def test_a_stage_that_passes_in_time_exits_3_and_never_prints_a_gate_result(
    tmp_path: Path,
) -> None:
    result, log = run_gate(tmp_path, "exit 0", 120)

    assert result.returncode == 3
    assert "TIMED OUT" not in result.stdout
    assert "=== GATE SELF TEST: stage passed; NOT A GATE VERDICT" in result.stdout
    assert "GATE RESULT" not in result.stdout and "GATE RESULT" not in log
    assert re.search(
        r"self test \[\d\d:\d\d:\d\d\]", result.stdout
    )  # the stage line carries the time
    assert "exit=0 elapsed=" in result.stdout


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


@pytest.mark.parametrize(
    "name", ["GATE_PYTEST_TIMEOUT_SECONDS", "GATE_STAGE_TIMEOUT_SECONDS"]
)
def test_a_limit_that_is_not_a_number_is_refused(tmp_path: Path, name: str) -> None:
    assert BASH is not None
    result = subprocess.run(
        [BASH, "scripts/gate.sh", str(tmp_path / "gate.log")],
        cwd=REPO_ROOT,
        env={**os.environ, name: "ten minutes", "GATE_SELF_TEST_COMMAND": "exit 0"},
        capture_output=True,
        text=True,
        timeout=CHILD_TIMEOUT_SECONDS,
        check=False,
    )

    assert result.returncode == 2
    assert f"{name} must be a whole number" in result.stdout


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="elsewhere `timeout` ends the whole process group; only Git Bash needs the process-tree kill",
)
def test_a_native_grandchild_does_not_outlive_the_ended_stage(tmp_path: Path) -> None:
    """The stage starts a native Python grandchild that would write a marker after the limit; it must not.

    Honest limit of this check: with plain coreutils ``timeout`` on the same Git Bash (8.32) the marker did not
    appear either (a plain, a detached and a base-interpreter grandchild were all ended), so this proves the
    watchdog does not leave survivors, not that ``timeout`` would. The watchdog is kept for the exit-code
    decision, the explicit tree kill and machines without coreutils.
    """
    marker = tmp_path / "marker.txt"
    script = tmp_path / "grandchild.py"
    script.write_text(
        "import subprocess, sys, time\n"
        "subprocess.Popen([sys.executable, '-c',\n"
        f'    \'import sys, time; time.sleep({MARKER_DELAY_SECONDS}); open(sys.argv[1], "w").write("alive")\',\n'
        "    sys.argv[1]])\n"
        "open(sys.argv[1] + '.started', 'w').write('x')\n"
        "time.sleep(120)\n",
        encoding="utf-8",
    )
    python = shlex.quote(sys.executable.replace("\\", "/"))
    command = (
        f"{python} {shlex.quote(script.as_posix())} {shlex.quote(marker.as_posix())}"
    )

    result, _ = run_gate(tmp_path, command, STAGE_LIMIT_SECONDS)

    assert f"self test TIMED OUT after {STAGE_LIMIT_SECONDS} seconds" in result.stdout
    # The grandchild really was started (else the check below proves nothing) and is given time to write.
    assert Path(str(marker) + ".started").exists()
    time.sleep(MARKER_DELAY_SECONDS + 2)
    assert not marker.exists(), (
        "the grandchild survived the ended stage and wrote its marker"
    )
