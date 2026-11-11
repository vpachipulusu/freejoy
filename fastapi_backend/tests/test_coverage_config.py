"""The coverage settings that keep ``pytest --cov`` fast and complete (FREEJ-65).

With the default tracer a full run took 640 to 745 s against 270 to 283 s without coverage, and every line that
runs inside a SQLAlchemy greenlet was missing from the report. ``core = "sysmon"`` (Python 3.12's
``sys.monitoring``) fixes both; these tests keep it from being dropped by accident. See
``docs/developer/testing.md``.
"""

import tomllib
from pathlib import Path

import coverage

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def _coverage_settings() -> dict[str, object]:
    with PYPROJECT.open("rb") as handle:
        return tomllib.load(handle)["tool"]["coverage"]


def test_the_sysmon_core_is_selected_for_line_coverage() -> None:
    run = _coverage_settings()["run"]

    assert isinstance(run, dict)
    assert run["core"] == "sysmon"
    assert run["source"] == ["app", "commands"]  # application code stays measured
    assert "branch" not in run  # sysmon measures lines only on Python 3.12


def test_the_installed_coverage_knows_the_core_option() -> None:
    major, minor, *_ = (int(part) for part in coverage.__version__.split(".")[:2])

    assert (major, minor) >= (7, 9)


def test_the_floor_is_a_ratchet_above_the_old_one() -> None:
    report = _coverage_settings()["report"]

    assert isinstance(report, dict)
    assert report["fail_under"] >= 91.0
