"""``tests.alembic_support``: a stalled or failing Alembic child ends the fixture with a clear error.

The child is replaced by small Python programs (``_child_command`` is patched), so nothing here needs a database.
"""

import sys
import time
import warnings
from pathlib import Path

import pytest

from tests import alembic_support
from tests.alembic_support import AlembicFailed, AlembicStalled, run_alembic

URL = "postgresql+asyncpg://postgres:pw@localhost:5433/example_gw1_test"
SESSIONS = "  pid 42 [x] idle in transaction"


@pytest.fixture(autouse=True)
def no_database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(alembic_support, "end_leftover_sessions", lambda url: 0)
    monkeypatch.setattr(alembic_support, "describe_sessions", lambda url: SESSIONS)


def child(monkeypatch: pytest.MonkeyPatch, code: str) -> None:
    """Make ``run_alembic`` start ``code`` as the child; the alembic arguments arrive as ``sys.argv[1:]``."""
    monkeypatch.setattr(
        alembic_support,
        "_child_command",
        lambda args, limit: [sys.executable, "-c", code, *args],
    )


def test_a_child_that_outlives_the_limit_fails_naming_the_command_the_database_and_the_sessions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    child(monkeypatch, "import time; time.sleep(60)")
    monkeypatch.setattr(alembic_support, "ALEMBIC_TIMEOUT_SECONDS", 1)

    with pytest.raises(AlembicStalled) as stalled:
        run_alembic(URL, "downgrade", "base")

    message = str(stalled.value)
    assert "`alembic downgrade base`" in message
    assert "example_gw1_test" in message and "did not finish in 1 s" in message
    assert SESSIONS in message
    assert "postgres:pw" not in message  # the URL, and its password, never appear


def test_the_sessions_are_sampled_while_the_child_is_still_alive(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    heartbeat = tmp_path / "heartbeat"
    child(
        monkeypatch,
        "import sys, time\n"
        "while True:\n"
        "    open(sys.argv[1], 'w').write(str(time.time()))\n"
        "    time.sleep(0.05)\n",
    )
    monkeypatch.setattr(alembic_support, "ALEMBIC_TIMEOUT_SECONDS", 2)
    sampled: list[float] = []

    def describe(url: str) -> str:
        sampled.append(time.time() - float(heartbeat.read_text()))
        return SESSIONS

    monkeypatch.setattr(alembic_support, "describe_sessions", describe)

    with pytest.raises(AlembicStalled):
        run_alembic(URL, str(heartbeat))

    # The child was still writing when the sessions were read, and it is really gone afterwards.
    assert len(sampled) == 1 and sampled[0] < 1.0
    ended_at = heartbeat.read_text()
    time.sleep(0.4)
    assert heartbeat.read_text() == ended_at


def test_a_child_that_dumps_its_own_stack_before_the_limit_shows_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The real prologue (faulthandler timer at 90% of a 2 s limit), and a child that never returns.
    monkeypatch.setattr(
        alembic_support, "_ALEMBIC_ENTRY", "import time; time.sleep(60)"
    )
    monkeypatch.setattr(alembic_support, "ALEMBIC_TIMEOUT_SECONDS", 2)

    with pytest.raises(AlembicStalled) as stalled:
        run_alembic(URL, "upgrade", "head")

    message = str(stalled.value)
    assert "Timeout (0:00:01.8" in message
    assert "most recent call first" in message


def test_a_failing_child_reports_the_end_of_its_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    child(
        monkeypatch,
        "import sys; sys.stderr.write('x' * 9000 + 'DROP TABLE users: boom'); sys.exit(1)",
    )

    with pytest.raises(AlembicFailed) as error:
        run_alembic(URL, "upgrade", "head")

    message = str(error.value)
    assert "exited with 1" in message and message.endswith("boom")
    assert len(message) < 6200
    assert SESSIONS not in message  # no timeout in the output: no session list


def test_a_failure_that_mentions_a_timeout_carries_the_session_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    child(
        monkeypatch,
        "import sys; sys.stderr.write('canceling statement due to lock timeout'); sys.exit(1)",
    )

    with pytest.raises(AlembicFailed) as error:
        run_alembic(URL, "downgrade", "base")

    assert "lock timeout" in str(error.value) and SESSIONS in str(error.value)


def test_a_child_that_succeeds_returns_quietly(monkeypatch: pytest.MonkeyPatch) -> None:
    child(monkeypatch, "pass")

    run_alembic(URL, "upgrade", "head")


def test_the_child_sees_the_database_in_database_url_direct(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    child(
        monkeypatch,
        "import os, sys; sys.exit(0 if os.environ['DATABASE_URL_DIRECT'] == sys.argv[1] else 3)",
    )

    run_alembic(
        URL, URL
    )  # the argument is the expected value, passed through as sys.argv[1]


def test_the_real_child_command_is_alembic_behind_a_faulthandler_timer() -> None:
    command = alembic_support._child_command(("upgrade", "head"), 300)

    assert command[:2] == [sys.executable, "-c"]
    assert "dump_traceback_later(280" in command[2] and "exit=True" in command[2]
    assert 'main(argv=sys.argv[1:], prog="alembic")' in command[2]
    assert command[3:] == ["upgrade", "head"]


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+asyncpg://u:p@db.example.com:5432/app_gw1_test",
        "postgresql+asyncpg://u:p@localhost:5432/app",
    ],
)
def test_a_database_that_is_not_a_local_test_one_is_refused(url: str) -> None:
    with pytest.raises(ValueError, match="local"):
        run_alembic(url, "upgrade", "head")


def test_sessions_are_not_ended_on_a_database_this_process_does_not_hold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.undo()  # the real end_leftover_sessions: it must stop before it connects

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        ended = alembic_support.end_leftover_sessions(
            "postgresql+asyncpg://u:p@localhost:5432/not_held_test"
        )

    assert ended == 0
    assert any("does not hold its guard" in str(item.message) for item in caught)
