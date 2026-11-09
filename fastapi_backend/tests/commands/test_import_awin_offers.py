"""``commands.import_awin_offers``: flags, output, exit codes, settings and the Sentry event. No socket, no database."""

import logging
from collections.abc import Mapping
from datetime import date
from typing import Any

import pytest
from pydantic import SecretStr
from sentry_sdk.transport import Transport

from app.core.awin_client import AwinError, AwinReason
from app.core.config import Settings, settings
from commands import import_awin_offers as command
from commands.import_awin_offers import FAILURE_MESSAGE, report_to_sentry
from tests.awin_samples import CODE, NOW, PUBLISHER_ID, raw_item, raw_page
from tests.fakes_awin import FakeAwinClient
from tests.fakes_awin_import import FakeDatabase

DSN = "https://publickey@o0.ingest.sentry.io/1"
TOKEN = "fake-awin-token-0123456789abcdef"
FORBIDDEN = ("http", "awin1.com", "acme", "Acme", "10% off", CODE, TOKEN, "awinaffid")


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


def run(
    args: list[str],
    client: FakeAwinClient,
    db: FakeDatabase | None = None,
    reporter: Reporter | None = None,
) -> int:
    database = db or FakeDatabase()
    return command.main(
        args,
        client=client,
        publisher_id=PUBLISHER_ID,
        units=database.unit,
        reporter=reporter or Reporter(),
        now=NOW,
    )


@pytest.fixture
def good_feed() -> FakeAwinClient:
    items = [
        raw_item(1),
        raw_item(2, type="promotion", voucher=None),
        raw_item(3, advertiser={"id": 9, "name": "Other", "joined": False}),
        raw_item(4, urlTracking="https://evil.example/click?token=abc"),
        raw_item(5),
    ]
    return FakeAwinClient([items])


# --- a run ---------------------------------------------------------------------------------


def test_a_run_imports_and_prints_counts_by_outcome_and_reason(
    good_feed: FakeAwinClient, capsys: pytest.CaptureFixture[str]
) -> None:
    db = FakeDatabase()

    code = run([], good_feed, db)

    captured = capsys.readouterr()
    assert code == 0 and captured.err == ""
    assert "fetched 5 promotion(s) in 1 page(s); 3 valid, 2 skipped." in captured.out
    assert "created 3, updated 0, unchanged 0" in captured.out
    assert "not_joined 1, tracking_host_not_allowed 1" in captured.out
    assert len(db.committed.offers) == 3
    assert good_feed.closed


def test_nothing_that_could_be_copy_a_url_or_a_secret_is_printed_or_logged(
    good_feed: FakeAwinClient,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)

    run([], good_feed)

    captured = capsys.readouterr()
    everything = captured.out + captured.err + caplog.text
    for fragment in FORBIDDEN:
        assert fragment not in everything, fragment
    assert "promotion_id=3 reason=not_joined" in caplog.text  # ids and codes are logged


def test_a_second_run_changes_nothing_and_says_so(
    capsys: pytest.CaptureFixture[str],
) -> None:
    db = FakeDatabase()
    feed = [raw_item(1), raw_item(2)]
    run([], FakeAwinClient([feed]), db)
    capsys.readouterr()
    before = (
        dict(db.committed.offers),
        dict(db.committed.imports),
        len(db.committed.audits),
    )

    code = run([], FakeAwinClient([feed]), db)

    assert code == 0
    assert "created 0, updated 0, unchanged 2" in capsys.readouterr().out
    assert (
        dict(db.committed.offers),
        dict(db.committed.imports),
        len(db.committed.audits),
    ) == before


def test_stopping_at_the_limit_is_reported_and_is_still_exit_0(
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeAwinClient([raw_page(1, 10)])

    code = run(["--limit", "10"], client)

    assert code == 0
    assert "stopped at --limit" in capsys.readouterr().out


def test_stopping_at_max_pages_is_reported_and_is_still_exit_0(
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeAwinClient([raw_page(1, 200), raw_page(201, 200)])

    code = run(["--limit", "500", "--max-pages", "1"], client)

    assert code == 0 and len(client.requests) == 1
    assert "stopped at --max-pages" in capsys.readouterr().out


# --- flags ---------------------------------------------------------------------------------


def test_the_flags_reach_the_client() -> None:
    client = FakeAwinClient([[]])

    run(["--limit", "30", "--max-pages", "2", "--updated-since", "2026-11-01"], client)

    filters, page, size = client.requests[0]
    assert filters.updated_since == date(2026, 11, 1)
    assert (page, size) == (1, 30)


def test_a_dry_run_validates_prints_and_writes_nothing(
    good_feed: FakeAwinClient, capsys: pytest.CaptureFixture[str]
) -> None:
    db = FakeDatabase()
    reporter = Reporter()

    code = run(["--dry-run"], good_feed, db, reporter)

    out = capsys.readouterr().out
    assert code == 0
    assert out.startswith("import-awin-offers: dry run, nothing was written.")
    assert "3 valid, 2 skipped" in out and "created" not in out
    assert db.units_opened == 0 and db.committed.offers == {}
    assert reporter.calls == []


@pytest.mark.parametrize(
    "args",
    [
        ["--limit", "0"],
        ["--limit", "-1"],
        ["--limit", "5001"],
        ["--max-pages", "0"],
        ["--max-pages", "51"],
        ["--max-seconds", "0"],
        ["--max-seconds", "86401"],
        ["--updated-since", "2026-13-01"],
        ["--updated-since", "yesterday"],
        ["--updated-since", "20261101"],
        ["--limit", "ten"],
        ["--nope"],
    ],
)
def test_bad_arguments_exit_2_without_a_request_or_a_write(
    args: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    client = FakeAwinClient([[raw_item(1)]])
    db = FakeDatabase()

    code = run(args, client, db)

    assert code == 2
    assert client.requests == [] and db.units_opened == 0
    assert TOKEN not in capsys.readouterr().err


def test_help_exits_zero_and_makes_no_request(
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeAwinClient([[]])

    assert run(["--help"], client) == 0
    assert client.requests == [] and "--dry-run" in capsys.readouterr().out


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

    code = command.main([], reporter=Reporter(), now=NOW)

    err = capsys.readouterr().err
    assert code == 2 and built == []
    assert all(name in err for name in named)
    assert TOKEN not in err


def test_the_real_client_is_built_from_the_settings_and_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "AWIN_PUBLISHER_ID", 4242)
    monkeypatch.setattr(settings, "AWIN_OAUTH_API_TOKEN", SecretStr(TOKEN))
    fake = FakeAwinClient([[]])
    calls: list[tuple[int, str]] = []

    def factory(publisher_id: int, token: SecretStr) -> FakeAwinClient:
        calls.append((publisher_id, token.get_secret_value()))
        return fake

    monkeypatch.setattr(command, "build_client", factory)

    code = command.main(["--dry-run"], reporter=Reporter(), now=NOW)

    assert code == 0 and calls == [(4242, TOKEN)] and fake.closed


# --- failure -------------------------------------------------------------------------------


def test_an_authentication_failure_exits_1_and_says_nothing_was_done(
    capsys: pytest.CaptureFixture[str],
) -> None:
    reporter = Reporter()
    client = FakeAwinClient([AwinError(AwinReason.AUTH_FAILED)])

    code = run([], client, FakeDatabase(), reporter)

    err = capsys.readouterr().err
    assert code == 1
    assert "failed (auth_failed) while fetching promotions" in err
    assert "created 0, updated 0" in err and "run it again to continue" in err
    assert client.closed


def test_a_failure_part_way_reports_what_was_committed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    db = FakeDatabase()
    client = FakeAwinClient([raw_page(1, 200), AwinError(AwinReason.SERVER_ERROR)])

    code = run(["--limit", "500"], client, db)

    err = capsys.readouterr().err
    assert code == 1
    assert "failed (server_error)" in err and "created 200" in err
    assert len(db.committed.offers) == 200


def test_a_database_failure_exits_1_with_the_count_and_the_error_type_only(
    capsys: pytest.CaptureFixture[str],
) -> None:
    db = FakeDatabase(fail_insert_offer_at=2)

    code = run([], FakeAwinClient([[raw_item(1), raw_item(2)]]), db)

    err = capsys.readouterr().err
    assert code == 1
    assert "failed (RuntimeError) while writing offers" in err
    assert "simulated" not in err and db.committed.offers == {}


def test_a_dry_run_failure_says_nothing_was_written(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = run(["--dry-run"], FakeAwinClient([AwinError(AwinReason.TIMEOUT)]))

    err = capsys.readouterr().err
    assert code == 1 and "nothing was written (dry run)" in err


def test_a_repeating_page_fails_the_run(capsys: pytest.CaptureFixture[str]) -> None:
    client = FakeAwinClient([raw_page(1, 200), raw_page(1, 200)])

    code = run(["--limit", "500"], client)

    assert code == 1 and "pagination_repeat" in capsys.readouterr().err


def test_an_unexpected_error_before_the_run_exits_1_with_its_type(
    capsys: pytest.CaptureFixture[str],
) -> None:
    def explode() -> Any:
        raise OSError("dsn=https://user:pw@db.example/x")

    code = command.main(
        [],
        client=FakeAwinClient([[raw_item(1)]]),
        publisher_id=PUBLISHER_ID,
        units=explode,
        reporter=Reporter(),
        now=NOW,
    )

    err = capsys.readouterr().err
    assert code == 1 and "OSError" in err and "db.example" not in err


# --- Sentry --------------------------------------------------------------------------------


def test_one_failure_event_carries_stage_cause_counts_and_reason_codes_only() -> None:
    reporter = Reporter()
    items = [raw_item(1), raw_item(2, type="cashback"), *raw_page(3, 198)]
    client = FakeAwinClient([items, AwinError(AwinReason.RATE_LIMITED)])

    run(["--limit", "500"], client, FakeDatabase(), reporter)

    ((message, context, level),) = reporter.calls
    assert message == FAILURE_MESSAGE and level == "error"
    assert context == {
        "stage": "while fetching promotions",
        "cause": "rate_limited",
        "dry_run": False,
        "pages": 1,
        "received": 200,
        "valid": 199,
        "created": 199,
        "updated": 0,
        "unchanged": 0,
        "source_changed": 0,
        "skipped": {"unsupported_type": 1},
    }
    flat = repr(context)
    for fragment in (*FORBIDDEN, "https"):
        assert fragment not in flat


def test_no_sentry_event_for_a_run_that_completes_even_with_skips(
    good_feed: FakeAwinClient,
) -> None:
    reporter = Reporter()

    run([], good_feed, FakeDatabase(), reporter)

    assert reporter.calls == []


def test_a_failing_reporter_does_not_change_the_failure_exit_code(
    capsys: pytest.CaptureFixture[str],
) -> None:
    reporter = Reporter(raises=RuntimeError("dsn=https://k@s.example/1"))

    code = run([], FakeAwinClient([AwinError(AwinReason.AUTH_FAILED)]), None, reporter)

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


def test_sentry_starts_only_after_the_last_request_and_gets_the_event() -> None:
    import sentry_sdk

    _sentry_off()
    transport = RecordingTransport()
    during_requests: list[bool] = []
    client = FakeAwinClient(
        [AwinError(AwinReason.AUTH_FAILED)],
        on_fetch=lambda: during_requests.append(sentry_sdk.is_initialized()),
    )
    dsn_settings = Settings(  # type: ignore[call-arg]
        DATABASE_URL="postgresql+asyncpg://test:test@localhost:5432/test",
        ENVIRONMENT="test",
        SENTRY_DSN=SecretStr(DSN),
    )

    def reporter(
        message: str, context: Mapping[str, Any], *, level: str = "error"
    ) -> bool:
        return report_to_sentry(
            message, context, dsn_settings, transport=transport, level=level
        )

    try:
        code = run([], client, FakeDatabase(), reporter)  # type: ignore[arg-type]
    finally:
        _sentry_off()

    assert code == 1
    assert during_requests == [False]
    (event,) = transport.events
    assert event["message"] == FAILURE_MESSAGE and event["level"] == "error"
    assert event["contexts"]["awin_import"]["cause"] == "auth_failed"
    assert "https://" not in repr(event["contexts"])


def test_without_a_dsn_the_reporter_sends_nothing() -> None:
    no_dsn = Settings(  # type: ignore[call-arg]
        DATABASE_URL="postgresql+asyncpg://test:test@localhost:5432/test",
        ENVIRONMENT="test",
    )

    assert report_to_sentry("m", {"cause": "x"}, no_dsn) is False


def test_invalid_or_missing_app_settings_exit_2_without_a_traceback_or_a_request() -> (
    None
):
    import subprocess
    import sys

    from tests._subprocess_support import BACKEND_ROOT, from_scratch_environment

    environment = from_scratch_environment(
        IGNORE_ENV_FILE="1"
    )  # no DATABASE_URL at all

    finished = subprocess.run(
        [sys.executable, "-m", "commands.import_awin_offers", "--dry-run"],
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


def test_a_link_for_another_publisher_is_skipped_and_counted(
    capsys: pytest.CaptureFixture[str],
) -> None:
    other = raw_item(
        1, urlTracking="https://www.awin1.com/cread.php?awinmid=77&awinaffid=999"
    )

    code = run([], FakeAwinClient([[other, raw_item(2)]]))

    assert code == 0
    assert "tracking_owner_mismatch 1" in capsys.readouterr().out


def test_a_run_past_its_deadline_exits_1_with_what_was_committed(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    class Stalls(FakeAwinClient):
        async def fetch_promotions(
            self, filters: Any, *, page: int, page_size: int
        ) -> Any:
            if page == 2:
                await asyncio.sleep(3600)  # cancelled by the deadline, never waited for
            return await super().fetch_promotions(
                filters, page=page, page_size=page_size
            )

    real_timeout = asyncio.timeout
    # --max-seconds is at least 1; the deadline is brought down to zero so nothing really waits
    monkeypatch.setattr(asyncio, "timeout", lambda _seconds: real_timeout(0))
    db = FakeDatabase()

    code = run(["--limit", "500"], Stalls([raw_page(1, 200), raw_page(201, 200)]), db)

    err = capsys.readouterr().err
    assert code == 1 and "failed (run_deadline)" in err and "created 200" in err
