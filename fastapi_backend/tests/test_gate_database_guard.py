"""``scripts/gate.sh``: the guard that decides which database the Alembic cycle may rebuild.

The gate runs ``alembic downgrade base`` on ``GATE_TEST_DATABASE_URL``, so the guard accepts only a
local database whose name ends in ``_test``, and refuses anything it cannot read without doubt.
``GATE_GUARD_ONLY=1`` runs just the guard (no log, no step, no connection). Every name below is
made up and unresolvable.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SENTINEL = "Zq9sentinelpw"


def find_bash() -> str | None:
    """A bash that runs scripts. On Windows ``bash`` on PATH can be the WSL launcher, which may not work, so
    Git for Windows' bash is tried too and each candidate is probed."""
    candidates = [shutil.which("bash")]
    git = shutil.which("git")
    if git:
        git_root = Path(git).resolve().parents[1]
        candidates += [
            str(git_root / "bin" / "bash.exe"),
            str(git_root / "usr" / "bin" / "bash.exe"),
        ]
    for candidate in candidates:
        if not candidate or not Path(candidate).exists():
            continue
        probe = subprocess.run(
            [candidate, "-c", "echo ready"], capture_output=True, text=True, check=False
        )
        if probe.returncode == 0 and probe.stdout.strip() == "ready":
            return candidate
    return None


BASH = find_bash()

pytestmark = pytest.mark.skipif(BASH is None, reason="no working bash on this machine")

ACCEPTED = [
    f"postgresql+asyncpg://postgres:{SENTINEL}@localhost:5433/freejoy_test",
    f"postgresql://u:{SENTINEL}@127.0.0.1/x_test",
    "postgresql://localhost/x_test?sslmode=disable",
]

REFUSED = [
    "postgresql://evil.invalid/db@localhost/x_test",
    f"postgresql://u:{SENTINEL}@evil.invalid:5432/db@localhost/x_test",
    f"postgresql://u:{SENTINEL}@evil.invalid/real_db#@localhost/x_test",
    f"postgresql://u:{SENTINEL}@evil.invalid@localhost/x_test",
    f"postgresql://u:{SENTINEL}@localhost:5432:evil.invalid/x_test",
    f"postgresql://u:{SENTINEL}@localhost/x_test/real_db",
    f"postgresql://u:{SENTINEL}@localhost/real_db#_test",
    "localhost/x_test",
    f"postgresql://u:{SENTINEL}@localhost/x_test?host=evil.invalid&database=realdb",
    f"postgresql://u:{SENTINEL}@localhost/x_test?sslmode=disable&host=evil.invalid",
    f"postgresql://u:{SENTINEL}@localhost/x_test?sslmode=disable&HostAddr=evil.invalid",
    f"postgresql://user:{SENTINEL}@ss@evil.invalid/x_test",
    "postgresql://localhost.evil.invalid/x_test",
    "postgresql://127.0.0.1.evil.invalid/x_test",
    f"postgresql://u:{SENTINEL}@localhost/real_db",
    f"postgresql://u:{SENTINEL}@localhost/",
    f"postgresql://u:{SENTINEL}@[::1]:5432/x_test",
    f"postgresql://localhost:{SENTINEL}?rd@evil.invalid/x_test",
    "",
]

# Pieces of the refused URLs that a refusal message must never repeat.
FORBIDDEN_IN_OUTPUT = (
    SENTINEL,
    "evil",
    "invalid",
    "real_db",
    "realdb",
    "x_test",
    "freejoy",
    "5432",
    "5433",
)


def run_guard(url: str) -> subprocess.CompletedProcess[str]:
    """Only the guard: no log file, no step. Run from the repo root so the relative path is portable."""
    assert BASH is not None
    return subprocess.run(
        [BASH, "scripts/gate.sh"],
        cwd=REPO_ROOT,
        env={**os.environ, "GATE_GUARD_ONLY": "1", "GATE_TEST_DATABASE_URL": url},
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("url", ACCEPTED)
def test_a_local_test_database_is_accepted(url: str) -> None:
    result = run_guard(url)

    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    "url", REFUSED, ids=lambda url: url.replace(SENTINEL, "pw") or "empty"
)
def test_anything_else_is_refused_without_echoing_the_url(url: str) -> None:
    logs_before = set(REPO_ROOT.parent.glob("gate-*.log"))

    result = run_guard(url)

    output = result.stdout + result.stderr
    assert result.returncode == 2, output
    assert "REFUSED" in output
    for fragment in FORBIDDEN_IN_OUTPUT:
        assert fragment not in output, fragment
    assert "=== alembic" not in output
    assert set(REPO_ROOT.parent.glob("gate-*.log")) == logs_before


def test_the_refusal_names_the_reason_in_fixed_words() -> None:
    wrong_host = run_guard("postgresql://u:p@db.example.invalid/x_test")
    wrong_name = run_guard("postgresql://u:p@localhost/real_db")
    malformed = run_guard("localhost/x_test")

    assert "host is not localhost or 127.0.0.1" in wrong_host.stdout
    assert "database name does not end in _test" in wrong_name.stdout
    assert "URL is not in a recognised form" in malformed.stdout
