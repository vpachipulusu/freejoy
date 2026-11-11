"""Child processes for tests that must see exactly the environment they are given."""

import os
import subprocess
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent

# A child that has not finished by now is stuck: fail the test instead of letting pytest wait for ever (FREEJ-64).
CHILD_TIMEOUT_SECONDS = 120

# A .env full of values that would change the app (brand, version, docs URL, error reporting).
LEAKY_ENV_FILE = chr(10).join(
    [
        "APP_NAME=Leaked Brand",
        "APP_VERSION=9.9.9",
        "OPENAPI_URL=",
        "SENTRY_DSN=https://fake@example.invalid/1",
        "",
    ]
)

# What a Python child process needs from the operating system, and nothing about the app.
OS_VARIABLES = (
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "WINDIR",
    "COMSPEC",
    "TEMP",
    "TMP",
    "TMPDIR",
    "HOME",
    "USERPROFILE",
    "APPDATA",
    "LOCALAPPDATA",
    "LANG",
    "LC_ALL",
)


def inherited_environment(**changes: str | None) -> dict[str, str]:
    """The pytest process environment as a child sees it, with ``changes`` applied (None removes)."""
    child = {**os.environ, "PYTHONPATH": str(BACKEND_ROOT)}
    return _apply(child, changes)


def from_scratch_environment(**extra: str | None) -> dict[str, str]:
    """Only what the OS needs plus ``PYTHONPATH``: no setting, no switch, nothing a shell exported."""
    child = {name: os.environ[name] for name in OS_VARIABLES if name in os.environ}
    child["PYTHONPATH"] = str(BACKEND_ROOT)
    return _apply(child, extra)


def run_module(
    module: str, cwd: Path, environment: dict[str, str], *args: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", module, *args],
        cwd=cwd,
        env=environment,
        capture_output=True,
        text=True,
        timeout=CHILD_TIMEOUT_SECONDS,
        check=False,
    )


def _apply(
    environment: dict[str, str], changes: dict[str, str | None]
) -> dict[str, str]:
    for name, value in changes.items():
        if value is None:
            environment.pop(name, None)
        else:
            environment[name] = value
    return environment
