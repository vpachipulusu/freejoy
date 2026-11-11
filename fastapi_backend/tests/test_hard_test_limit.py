"""The hard per-test limit in ``tests/conftest.py``: a hung test is ended and named, and the run fails (FREEJ-64).

A child pytest loads the conftest as a plugin and runs a test that never returns with the limit set to 2 s.
"""

import os
import subprocess
import sys
from pathlib import Path

from tests._subprocess_support import BACKEND_ROOT, CHILD_TIMEOUT_SECONDS


def run_child(tmp_path: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (tmp_path / "test_probe.py").write_text(
        "import time\n\n"
        "def test_first():\n    assert True\n\n"
        "def test_hangs():\n    time.sleep(120)\n\n"
        "def test_never_reached():\n    assert True\n",
        encoding="utf-8",
    )
    environment = {
        **os.environ,
        "PYTHONPATH": str(BACKEND_ROOT),
        "FREEJOY_HARD_TEST_LIMIT_SECONDS": "2",
    }
    environment.pop("TEST_DATABASE_URL", None)
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "tests.conftest",
            "-p",
            "no:cacheprovider",
            "-c",
            str(tmp_path / "pytest.ini"),
            "--rootdir",
            str(tmp_path),
            str(tmp_path),
            *extra,
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=CHILD_TIMEOUT_SECONDS,
        check=False,
    )


def test_a_hung_test_dumps_every_threads_stack_and_ends_the_run(tmp_path: Path) -> None:
    result = run_child(tmp_path)

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "Timeout (0:00:02)!" in output
    assert (
        "test_probe.py" in output and "in test_hangs" in output
    )  # the stack names the hung test
    assert "test_never_reached" not in output
