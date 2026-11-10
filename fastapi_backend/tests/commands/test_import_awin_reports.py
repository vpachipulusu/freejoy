"""``commands.import_awin_reports``: flags, output, exit codes, settings and the Sentry event.

The real ``HttpxAwinClient`` runs over ``httpx.MockTransport`` (so the request the command makes is the one
asserted), the database is an in-memory fake. No socket, no database, a made-up token.
"""

import logging
import subprocess
import sys
from collections.abc import Callable, Mapping
from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from sentry_sdk.transport import Transport

from app.core.awin_client import HttpxAwinClient
from app.core.config import Settings, settings
from commands import import_awin_reports as command
from commands.import_awin_reports import FAILURE_MESSAGE, report_to_sentry
from tests._subprocess_support import BACKEND_ROOT, from_scratch_environment
from tests.awin_report_samples import NOW, raw_row, raw_rows
from tests.fakes_awin import FakeTime
from tests.fakes_network_reports import FakeLink, FakeReportDatabase

DSN = "https://publickey@o0.ingest.sentry.io/1"
TOKEN = "fake-awin-token-0123456789abcdef"
PUBLISHER_ID = 424242
FORBIDDEN = ("http", "awin1.com", "ref-", "GBP", "1.5", "30.0", TOKEN, "approved")
DAY_ARGS = ["--from", "2026-11-03", "--to", "2026-11-03"]


class Limiter:
    async def acquire(self) -> None:
        return None


class Server:
    """A scripted Awin: answers the GETs in order (the last answer repeats) and keeps the requests."""

    def __init__(
        self, *answers: list[Any] | int, on_request: Callable[[], None] | None = None
    ) -> None:
        self.answers = list(answers)
        self.requests: list[httpx.Request] = []
        self.on_request = on_request

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.on_request is not None:
            self.on_request()
        index = min(len(self.requests) - 1, len(self.answers) - 1)
        answer = self.answers[index] if self.answers else []
        if isinstance(answer, int):
            return httpx.Response(answer)
        return httpx.Response(200, json=answer)

    def starts(self) -> list[str]:
        return [request.url.params["startDate"] for request in self.requests]


class Reporter:
    def __init__(self, raises: Exception | None = None) -> None:
        self.calls: list[tuple[str, Mapping[str, Any], str]] = []
        self._raises = raises

    def __call__(
        self, message: str, context: Mapping[str, Any], *, level: str = "error"
    ) -> None:
        if self._raises is not None:
            raise self._raises
        self.calls.append((message, context, level))


def client_for(server: Server) -> HttpxAwinClient:
    return HttpxAwinClient(
        PUBLISHER_ID,
        SecretStr(TOKEN),
        transport=httpx.MockTransport(server),
        limiter=Limiter(),
        sleep=FakeTime().sleep,
    )


def run(
    args: list[str],
    server: Server,
    db: FakeReportDatabase | None = None,
    reporter: Reporter | None = None,
) -> int:
    database = db or FakeReportDatabase()
    return command.main(
        args,
        client=client_for(server),
        units=database.unit,
        reporter=reporter or Reporter(),
        now=NOW,
    )


@pytest.fixture
def good_feed() -> Server:
    rows = [
        raw_row(1),
        raw_row(2, commissionStatus="refunded"),
        raw_row(3, clickRefs=None),
        raw_row(4, commissionAmount={"amount": -1, "currency": "GBP"}),
        raw_row(5),
    ]
    return Server(rows)


# --- a run ---------------------------------------------------------------------------------


def test_a_run_requests_the_window_imports_and_prints_counts_only(
    good_feed: Server, capsys: pytest.CaptureFixture[str]
) -> None:
    db = FakeReportDatabase()
    db.add_link(FakeLink("l1", "o1", "https://x.example", "ref-1"))

    code = run(["--from", "2026-11-01", "--to", "2026-11-10"], good_feed, db)

    captured = capsys.readouterr()
    assert code == 0 and captured.err == ""
    assert (
        "fetched 5 transaction(s) in 1 page(s) for 2026-11-01 to 2026-11-10; "
        "3 valid, 2 skipped." in captured.out
    )
    assert "inserted 3, updated 0, unchanged 0, unmatched 2." in captured.out
    assert "skipped by reason: invalid_amount 1, unknown_status 1." in captured.out
    assert len(db.committed.rows) == 3 and len(db.committed.audits) == 1
    (request,) = good_feed.requests
    assert request.method == "GET"
    assert request.url.path == f"/publishers/{PUBLISHER_ID}/transactions/"
    assert request.url.params["startDate"] == "2026-11-01T00:00:00"
    assert request.url.params["endDate"] == "2026-11-10T23:59:59"
    assert request.headers["Authorization"] == f"Bearer {TOKEN}"


def test_nothing_that_could_be_a_report_field_a_url_or_a_secret_is_printed_or_logged(
    good_feed: Server,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)

    run(DAY_ARGS, good_feed)

    captured = capsys.readouterr()
    everything = captured.out + captured.err + caplog.text
    for fragment in FORBIDDEN:
        assert fragment not in everything, fragment
    assert "reason=unknown_status" in caplog.text  # reason codes are logged


def test_the_client_is_closed_when_the_run_ends() -> None:
    client = client_for(Server([]))

    code = command.main(
        DAY_ARGS,
        client=client,
        units=FakeReportDatabase().unit,
        reporter=Reporter(),
        now=NOW,
    )

    assert code == 0 and client.http_client.is_closed


def test_a_second_run_changes_nothing_and_says_so(
    capsys: pytest.CaptureFixture[str],
) -> None:
    db = FakeReportDatabase()
    rows = raw_rows(1, 3)
    run(DAY_ARGS, Server(rows), db)
    capsys.readouterr()

    code = run(DAY_ARGS, Server(rows), db)

    assert code == 0
    assert "inserted 0, updated 0, unchanged 3" in capsys.readouterr().out


def test_stopping_at_max_pages_is_reported_and_is_still_exit_0(
    capsys: pytest.CaptureFixture[str],
) -> None:
    server = Server(raw_rows(1, 200), raw_rows(1000, 200))

    code = run([*DAY_ARGS, "--max-pages", "1"], server)

    assert code == 0 and len(server.requests) == 1
    assert "stopped at --max-pages" in capsys.readouterr().out


def test_a_report_longer_than_31_days_needs_the_flag_and_is_read_in_windows() -> None:
    server = Server([])

    code = run(
        ["--from", "2026-09-01", "--to", "2026-10-15", "--allow-long-range"], server
    )

    assert code == 0
    assert server.starts() == ["2026-09-01T00:00:00", "2026-10-02T00:00:00"]


def test_exactly_31_days_is_allowed_without_the_flag() -> None:
    server = Server([])

    assert run(["--from", "2026-10-01", "--to", "2026-10-31"], server) == 0
    assert len(server.requests) == 1


# --- the dry run ---------------------------------------------------------------------------


def test_a_dry_run_validates_prints_and_writes_nothing(
    good_feed: Server, capsys: pytest.CaptureFixture[str]
) -> None:
    db = FakeReportDatabase()
    reporter = Reporter()

    code = run([*DAY_ARGS, "--dry-run"], good_feed, db, reporter)

    out = capsys.readouterr().out
    assert code == 0
    assert out.startswith("import-awin-reports: dry run, nothing was written.")
    assert "3 valid, 2 skipped" in out and "inserted" not in out
    assert (
        db.units_opened == 0 and db.committed.rows == {} and db.committed.audits == []
    )
    assert reporter.calls == []


# --- bad arguments -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["--from", "2026-11-03"],
        ["--to", "2026-11-03"],
        ["--from", "2026-11-04", "--to", "2026-11-03"],
        ["--from", "2026-13-01", "--to", "2026-11-03"],
        ["--from", "20261101", "--to", "2026-11-03"],
        ["--from", "yesterday", "--to", "2026-11-03"],
        ["--from", "2026-11-01", "--to", "2026-11-03T00:00:00"],
        ["--from", "2026-10-01", "--to", "2026-11-01"],  # 32 days
        [*DAY_ARGS, "--max-pages", "0"],
        [*DAY_ARGS, "--max-pages", "51"],
        [*DAY_ARGS, "--max-pages", "ten"],
        [*DAY_ARGS, "--nope"],
    ],
)
def test_bad_arguments_exit_2_without_a_request_or_a_write(
    args: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    server = Server([raw_row(1)])
    db = FakeReportDatabase()

    code = run(args, server, db)

    assert code == 2
    assert server.requests == [] and db.units_opened == 0
    assert TOKEN not in capsys.readouterr().err


def test_help_exits_zero_and_makes_no_request(
    capsys: pytest.CaptureFixture[str],
) -> None:
    server = Server([])

    assert run(["--help"], server) == 0
    out = capsys.readouterr().out
    assert server.requests == []
    assert "--from" in out and "--dry-run" in out and "--allow-long-range" in out


# --- settings ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("publisher_id", "token", "named"),
    [
        (None, None, ["AWIN_PUBLISHER_ID", "AWIN_OAUTH_API_TOKEN"]),
        (None, SecretStr(TOKEN), ["AWIN_PUBLISHER_ID"]),
        (4242, None, ["AWIN_OAUTH_API_TOKEN"]),
    ],
)
def test_missing_settings_exit_2_and_never_build_a_client(
    publisher_id: int | None,
    token: SecretStr | None,
    named: list[str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(settings, "AWIN_PUBLISHER_ID", publisher_id)
    monkeypatch.setattr(settings, "AWIN_OAUTH_API_TOKEN", token)
    built: list[object] = []
    monkeypatch.setattr(command, "build_client", lambda *a: built.append(a))

    code = command.main(DAY_ARGS, reporter=Reporter(), now=NOW)

    err = capsys.readouterr().err
    assert code == 2 and built == []
    assert all(name in err for name in named)
    assert TOKEN not in err


def test_the_real_client_is_built_from_the_settings_and_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "AWIN_PUBLISHER_ID", 4242)
    monkeypatch.setattr(settings, "AWIN_OAUTH_API_TOKEN", SecretStr(TOKEN))
    calls: list[tuple[int, str]] = []
    built: list[HttpxAwinClient] = []

    def factory(publisher_id: int, token: SecretStr) -> HttpxAwinClient:
        calls.append((publisher_id, token.get_secret_value()))
        client = client_for(Server([]))
        built.append(client)
        return client

    monkeypatch.setattr(command, "build_client", factory)

    code = command.main([*DAY_ARGS, "--dry-run"], reporter=Reporter(), now=NOW)

    assert code == 0 and calls == [(4242, TOKEN)] and built[0].http_client.is_closed


def test_invalid_or_missing_app_settings_exit_2_without_a_traceback_or_a_request() -> (
    None
):
    environment = from_scratch_environment(
        IGNORE_ENV_FILE="1"
    )  # no DATABASE_URL at all

    finished = subprocess.run(
        [sys.executable, "-m", "commands.import_awin_reports", *DAY_ARGS, "--dry-run"],
        cwd=BACKEND_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert finished.returncode == 2, finished.stderr
    assert "settings are invalid" in finished.stderr
    assert "Traceback" not in finished.stderr


# --- failure -------------------------------------------------------------------------------


def test_an_authentication_failure_exits_1_and_says_nothing_was_done(
    capsys: pytest.CaptureFixture[str],
) -> None:
    server = Server(401)

    code = run(DAY_ARGS, server)

    err = capsys.readouterr().err
    assert code == 1 and len(server.requests) == 1  # a 401 is never retried
    assert "failed (auth_failed) while fetching transactions" in err
    assert "inserted 0, updated 0" in err and "run it again to continue" in err
    assert TOKEN not in err


def test_a_failure_part_way_reports_what_was_committed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    db = FakeReportDatabase()
    server = Server(raw_rows(1, 200), 500)

    code = run(DAY_ARGS, server, db)

    err = capsys.readouterr().err
    assert code == 1
    assert "failed (server_error)" in err and "inserted 200" in err
    assert len(db.committed.rows) == 200


def test_a_database_failure_exits_1_with_the_error_type_only(
    capsys: pytest.CaptureFixture[str],
) -> None:
    db = FakeReportDatabase(fail_insert_at=2)

    code = run(DAY_ARGS, Server(raw_rows(1, 2)), db)

    err = capsys.readouterr().err
    assert code == 1
    assert "failed (RuntimeError) while writing reports" in err
    assert "simulated" not in err and db.committed.rows == {}


def test_a_dry_run_failure_says_nothing_was_written(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = run([*DAY_ARGS, "--dry-run"], Server(403))

    assert code == 1 and "nothing was written (dry run)" in capsys.readouterr().err


def test_a_repeating_page_fails_the_run(capsys: pytest.CaptureFixture[str]) -> None:
    code = run(DAY_ARGS, Server(raw_rows(1, 200), raw_rows(1, 200)))

    assert code == 1 and "pagination_repeat" in capsys.readouterr().err


def test_an_unexpected_error_before_the_run_exits_1_with_its_type(
    capsys: pytest.CaptureFixture[str],
) -> None:
    def explode() -> Any:
        raise OSError("dsn=https://user:pw@db.example/x")

    code = command.main(
        DAY_ARGS,
        client=client_for(Server([raw_row(1)])),
        units=explode,
        reporter=Reporter(),
        now=NOW,
    )

    err = capsys.readouterr().err
    assert code == 1 and "OSError" in err and "db.example" not in err


# --- Sentry --------------------------------------------------------------------------------


def test_one_failure_event_carries_stage_cause_counts_and_reason_codes_only() -> None:
    reporter = Reporter()
    rows = [raw_row(1), raw_row(2, commissionStatus="refunded"), *raw_rows(3, 198)]
    server = Server(rows, 500)

    run(DAY_ARGS, server, FakeReportDatabase(), reporter)

    ((message, context, level),) = reporter.calls
    assert message == FAILURE_MESSAGE and level == "error"
    assert context == {
        "stage": "while fetching transactions",
        "cause": "server_error",
        "dry_run": False,
        "from": "2026-11-03",
        "to": "2026-11-03",
        "pages": 1,
        "received": 200,
        "valid": 199,
        "inserted": 199,
        "updated": 0,
        "unchanged": 0,
        "unmatched": 199,
        "skipped": {"unknown_status": 1},
    }
    flat = repr(context)
    for fragment in (*FORBIDDEN, "https"):
        assert fragment not in flat


def test_no_sentry_event_for_a_run_that_completes_even_with_skips(
    good_feed: Server,
) -> None:
    reporter = Reporter()

    run(DAY_ARGS, good_feed, FakeReportDatabase(), reporter)

    assert reporter.calls == []


def test_a_failing_reporter_does_not_change_the_failure_exit_code(
    capsys: pytest.CaptureFixture[str],
) -> None:
    reporter = Reporter(raises=RuntimeError("dsn=https://k@s.example/1"))

    code = run(DAY_ARGS, Server(401), None, reporter)

    err = capsys.readouterr().err
    assert code == 1 and "could not send the Sentry event (RuntimeError)" in err
    assert "s.example" not in err


class RecordingTransport(Transport):
    def __init__(self, options: dict[str, Any] | None = None) -> None:
        super().__init__(options)
        self.events: list[dict[str, Any]] = []

    def capture_envelope(self, envelope: Any) -> None:
        for item in envelope.items:
            if item.type == "event":
                self.events.append(item.payload.json)


def _sentry_off() -> None:
    import sentry_sdk

    for scope in (
        sentry_sdk.get_global_scope(),
        sentry_sdk.get_isolation_scope(),
        sentry_sdk.get_current_scope(),
    ):
        scope.client = None  # type: ignore[assignment]


def test_sentry_starts_only_after_the_client_is_closed_and_gets_the_event() -> None:
    import sentry_sdk

    _sentry_off()
    transport = RecordingTransport()
    during_requests: list[bool] = []
    server = Server(
        401, on_request=lambda: during_requests.append(sentry_sdk.is_initialized())
    )
    client = client_for(server)
    closed_when_reported: list[bool] = []
    dsn_settings = Settings(  # type: ignore[call-arg]
        DATABASE_URL="postgresql+asyncpg://test:test@localhost:5432/test",
        ENVIRONMENT="test",
        SENTRY_DSN=SecretStr(DSN),
    )

    def reporter(
        message: str, context: Mapping[str, Any], *, level: str = "error"
    ) -> bool:
        closed_when_reported.append(client.http_client.is_closed)
        return report_to_sentry(
            message, context, dsn_settings, transport=transport, level=level
        )

    try:
        code = command.main(
            DAY_ARGS,
            client=client,
            units=FakeReportDatabase().unit,
            reporter=reporter,  # type: ignore[arg-type]
            now=NOW,
        )
    finally:
        _sentry_off()

    assert code == 1
    assert during_requests == [False] and closed_when_reported == [True]
    (event,) = transport.events
    assert event["message"] == FAILURE_MESSAGE and event["level"] == "error"
    assert event["contexts"]["awin_reports"]["cause"] == "auth_failed"
    assert "https://" not in repr(event["contexts"])


def test_without_a_dsn_the_reporter_sends_nothing() -> None:
    no_dsn = Settings(  # type: ignore[call-arg]
        DATABASE_URL="postgresql+asyncpg://test:test@localhost:5432/test",
        ENVIRONMENT="test",
    )

    assert report_to_sentry("m", {"cause": "x"}, no_dsn) is False
