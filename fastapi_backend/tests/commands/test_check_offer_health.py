"""``commands.check_offer_health``: flags, output, exit codes, Sentry summary. No socket, no database."""

import logging
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import SecretStr
from sentry_sdk.transport import Transport

from app.core.config import Settings
from app.core.url_probe import ProbeReason, ProbeResult, ProbeStatus
from app.models.platform.offer import OfferStatus
from app.repositories.offer_health import DirectLinkTarget, DueOffer, SourceUrlTarget
from commands import check_offer_health as command
from commands.check_offer_health import Unit, build_user_agent, report_to_sentry
from tests.fakes_offer_health import (
    FakeAuditor,
    FakeCommitter,
    FakeHealthStore,
    FakeUrlProbe,
)

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
ENDED = NOW - timedelta(days=1)
DEAD = ProbeResult(ProbeStatus.UNREACHABLE, ProbeReason.DNS_FAILURE)
BLOCKED = ProbeResult(ProbeStatus.REFUSED, ProbeReason.BLOCKED_ADDRESS)
DSN = "https://publickey@o0.ingest.sentry.io/1"
SECRET_URL = "https://b.example/dead?token=abc123"


def units_for(
    store: FakeHealthStore,
    *,
    auditor: FakeAuditor | None = None,
    committer: FakeCommitter | None = None,
) -> Any:
    unit = Unit(store, committer or FakeCommitter(), auditor or FakeAuditor())

    @asynccontextmanager
    async def open_unit() -> AsyncIterator[Unit]:
        yield unit

    return open_unit


def store_with(*, ended: int = 0) -> FakeHealthStore:
    return FakeHealthStore(
        offers=[DueOffer(f"e{n}", OfferStatus.PUBLISHED, ENDED) for n in range(ended)],
        sources=[
            SourceUrlTarget("o1", "https://a.example/ok", NOW),
            SourceUrlTarget("o2", SECRET_URL, NOW),
            SourceUrlTarget("o3", "https://c.example/x", NOW),
        ],
        links=[
            DirectLinkTarget(
                "o4",
                "l4",
                "direct",
                "https://www.shop.example/d",
                "https://shop.example",
                NOW,
            )
        ],
    )


def probe_with_problems() -> FakeUrlProbe:
    return FakeUrlProbe({SECRET_URL: DEAD, "https://c.example/x": BLOCKED})


class Reporter:
    def __init__(self, raises: Exception | None = None) -> None:
        self.contexts: list[Mapping[str, Any]] = []
        self._raises = raises

    def __call__(self, context: Mapping[str, Any]) -> None:
        if self._raises is not None:
            raise self._raises
        self.contexts.append(context)


def run(
    args: list[str],
    store: FakeHealthStore,
    probe: FakeUrlProbe | None = None,
    reporter: Reporter | None = None,
    **units: Any,
) -> int:
    return command.main(
        args,
        units=units_for(store, **units),
        probe=probe or FakeUrlProbe(),
        reporter=reporter or Reporter(),
        now=NOW,
    )


def test_a_run_expires_ended_offers_checks_urls_and_prints_counts(
    capsys: pytest.CaptureFixture[str],
) -> None:
    store = store_with(ended=3)

    code = run([], store, probe_with_problems())

    out = capsys.readouterr().out
    assert code == 0
    assert "expired 3 offer(s)" in out
    assert "checked 4 URL(s)" in out
    assert "reachable 2" in out and "unreachable 1" in out and "refused 1" in out
    assert "dns_failure 1" in out and "blocked_address 1" in out


def test_unreachable_destinations_do_not_change_the_exit_code_or_any_offer() -> None:
    store = store_with(ended=0)

    code = run([], store, probe_with_problems())

    assert code == 0
    assert store.expired_batches == []


def test_skip_reachability_expires_only_and_never_asks_for_targets(
    capsys: pytest.CaptureFixture[str],
) -> None:
    store = store_with(ended=2)
    probe = FakeUrlProbe()
    reporter = Reporter()

    code = run(["--skip-reachability"], store, probe, reporter)

    assert code == 0
    assert probe.urls == [] and reporter.contexts == []
    out = capsys.readouterr().out
    assert "expired 2 offer(s)" in out and "reachability checks skipped" in out


def test_limit_caps_the_urls_checked() -> None:
    probe = FakeUrlProbe()

    run(["--limit", "2"], store_with(), probe)

    assert len(probe.urls) == 2


def test_a_rerun_expires_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    store = store_with(ended=2)
    run(["--skip-reachability"], store)
    capsys.readouterr()

    run(["--skip-reachability"], store)

    assert "expired 0 offer(s)" in capsys.readouterr().out


@pytest.mark.parametrize(
    "args",
    [
        ["--limit", "0"],
        ["--limit", "-1"],
        ["--batch-size", "0"],
        ["--concurrency", "0"],
        ["--concurrency", "21"],
    ],
)
def test_out_of_range_values_are_refused_with_exit_2(
    args: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    store = store_with(ended=1)

    code = run(args, store)

    assert code == 2
    assert store.expired_batches == []
    assert capsys.readouterr().err.startswith("check-offer-health:")


def test_an_unknown_flag_exits_2_without_doing_anything() -> None:
    store = store_with(ended=1)

    with pytest.raises(SystemExit) as raised:
        run(["--nope"], store)

    assert raised.value.code == 2
    assert store.expired_batches == []


def test_a_failure_part_way_exits_1_with_the_count_so_far_and_only_the_error_type(
    capsys: pytest.CaptureFixture[str],
) -> None:
    store = store_with(ended=5)

    code = run(["--batch-size", "2"], store, auditor=FakeAuditor(fail_on=3))

    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == ""
    assert "2 offer(s) were already expired" in captured.err
    assert "RuntimeError" in captured.err
    assert "audit write failed" not in captured.err


def test_a_failure_before_anything_was_expired_says_zero(
    capsys: pytest.CaptureFixture[str],
) -> None:
    class DownStore(FakeHealthStore):
        async def lock_due_offers(self, now: datetime, limit: int) -> list[DueOffer]:
            raise ConnectionError("password=hunter2 host=db.internal")

    code = run([], DownStore())

    err = capsys.readouterr().err
    assert code == 1
    assert "0 offer(s) were already expired" in err and "ConnectionError" in err
    assert "hunter2" not in err and "db.internal" not in err


def test_a_failure_reading_targets_reports_the_offers_already_expired(
    capsys: pytest.CaptureFixture[str],
) -> None:
    class NoTargets(FakeHealthStore):
        async def list_source_url_targets(
            self, now: datetime, limit: int
        ) -> list[SourceUrlTarget]:
            raise TimeoutError("db.internal")

    code = run([], NoTargets(offers=[DueOffer("e1", OfferStatus.PUBLISHED, ENDED)]))

    err = capsys.readouterr().err
    assert code == 1
    assert "1 offer(s) were already expired" in err and "TimeoutError" in err
    assert "db.internal" not in err


def test_one_sentry_summary_with_ids_and_reason_codes_only() -> None:
    reporter = Reporter()

    run([], store_with(ended=2), probe_with_problems(), reporter)

    assert len(reporter.contexts) == 1
    context = reporter.contexts[0]
    assert context["expired"] == 2 and context["checked"] == 4
    assert context["counts"]["unreachable"] == 1 and context["counts"]["refused"] == 1
    assert {
        (f["offer_id"], f["link_id"], f["reason"]) for f in context["findings"]
    } == {
        ("o2", None, "dns_failure"),
        ("o3", None, "blocked_address"),
    }
    assert "example" not in repr(context) and "abc123" not in repr(context)


def test_no_sentry_event_when_nothing_is_wrong() -> None:
    reporter = Reporter()

    code = run([], store_with(ended=1), FakeUrlProbe(), reporter)

    assert code == 0 and reporter.contexts == []


def test_a_failing_reporter_does_not_fail_the_run(
    capsys: pytest.CaptureFixture[str],
) -> None:
    reporter = Reporter(raises=RuntimeError("dsn=https://key@sentry.example/1"))

    code = run([], store_with(), probe_with_problems(), reporter)

    err = capsys.readouterr().err
    assert code == 0
    assert "RuntimeError" in err and "sentry.example" not in err


def test_no_url_reaches_stdout_stderr_or_logs(
    caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    caplog.set_level(logging.DEBUG)

    run([], store_with(ended=1), probe_with_problems())

    captured = capsys.readouterr()
    everything = caplog.text + captured.out + captured.err
    assert "o2" in caplog.text and "dns_failure" in caplog.text  # the ids are logged
    for fragment in ("example", "abc123", "token", "https://", "/dead"):
        assert fragment not in everything


def test_the_user_agent_names_the_site_in_plain_ascii() -> None:
    agent = build_user_agent("Freejoy API", "https://freejoy.example")

    assert agent == "Freejoy-LinkCheck/1.0 (+https://freejoy.example)"
    assert agent.isascii()


def test_the_user_agent_falls_back_when_the_app_name_has_no_usable_word() -> None:
    assert build_user_agent("   ", "https://x.example").startswith("Site-LinkCheck/1.0")


class RecordingTransport(Transport):
    def __init__(self, options: dict[str, Any] | None = None) -> None:
        super().__init__(options)
        self.events: list[dict[str, Any]] = []

    def capture_envelope(self, envelope: Any) -> None:
        for item in envelope.items:
            if item.type == "event":
                self.events.append(item.payload.json)


def settings_with(dsn: str | None) -> Settings:
    return Settings(  # type: ignore[call-arg]
        DATABASE_URL="postgresql+asyncpg://test:test@localhost:5432/test",
        ENVIRONMENT="test",
        SENTRY_DSN=SecretStr(dsn) if dsn else None,
    )


def test_without_a_dsn_the_sentry_reporter_sends_nothing() -> None:
    assert report_to_sentry({"expired": 1}, settings_with(None)) is False


def test_with_a_dsn_the_sentry_reporter_sends_one_summary_event() -> None:
    import sentry_sdk

    transport = RecordingTransport()
    context = {"expired": 1, "findings": [{"offer_id": "o2", "reason": "dns_failure"}]}
    try:
        sent = report_to_sentry(context, settings_with(DSN), transport=transport)
    finally:  # leave Sentry off for every later test, whichever scope holds the client
        for scope in (
            sentry_sdk.get_global_scope(),
            sentry_sdk.get_isolation_scope(),
            sentry_sdk.get_current_scope(),
        ):
            scope.client = None  # type: ignore[assignment]

    assert sent is True
    assert len(transport.events) == 1
    event = transport.events[0]
    assert event["message"] == command.EVENT_MESSAGE
    assert event["contexts"]["offer_health"]["findings"][0]["offer_id"] == "o2"
