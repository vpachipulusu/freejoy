"""The coverage settings that keep ``pytest --cov`` fast and complete (FREEJ-65).

With the default tracer (CTracer) a full run took 640 to 745 s against 270 to 283 s without coverage, and
application lines that run after an awaited database call went unrecorded: CTracer without
``concurrency = greenlet`` loses its place when SQLAlchemy's asyncio layer switches greenlets. ``core = "sysmon"``
(Python 3.12's ``sys.monitoring``) fixes both. These tests keep it from being dropped by accident: one reads the
settings, one checks the tracer that is really running. See ``docs/developer/testing.md``.
"""

import sys
import tomllib
from pathlib import Path
from typing import Any, cast

import coverage
import pytest

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"

# Each of these makes coverage fall back to CTracer (or is not supported by sysmon on Python 3.12).
OPTIONS_THAT_REPLACE_SYSMON = ("concurrency", "dynamic_context", "timid", "branch")


def _coverage_settings() -> dict[str, Any]:
    with PYPROJECT.open("rb") as handle:
        return cast(dict[str, Any], tomllib.load(handle)["tool"]["coverage"])


def test_the_sysmon_core_is_selected_for_line_coverage() -> None:
    run = _coverage_settings()["run"]

    assert run["core"] == "sysmon"
    assert run["source"] == ["app", "commands"]  # application code stays measured
    for option in OPTIONS_THAT_REPLACE_SYSMON:
        assert option not in run, f"{option} makes coverage leave the sysmon core"


def test_sysmon_is_the_core_in_use_when_coverage_is_measuring() -> None:
    if coverage.Coverage.current() is None:
        pytest.skip("not running under --cov")

    # COVERAGE_CORE in the environment beats the setting in pyproject.toml, and a few options switch
    # tracers silently, so the settings alone do not prove it: ask which tool holds the coverage slot.
    assert sys.monitoring.get_tool(sys.monitoring.COVERAGE_ID) == "coverage.py"


def test_the_installed_coverage_knows_the_core_option() -> None:
    assert coverage.version_info[:2] >= (7, 9)


def test_the_floor_is_at_least_91() -> None:
    report = _coverage_settings()["report"]

    assert report["fail_under"] >= 91.0
