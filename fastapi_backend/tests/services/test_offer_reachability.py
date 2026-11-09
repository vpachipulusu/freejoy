"""Reachability targets and results: which URLs are requested, and what is reported about them."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.core.affiliate_hosts import PROVIDER_HOSTS
from app.core.url_probe import ProbeReason, ProbeResult, ProbeStatus
from app.repositories.offer_health import DirectLinkTarget, SourceUrlTarget
from app.services.offer_reachability import (
    MAX_EVENT_FINDINGS,
    MAX_HOST_FAILURES,
    CheckTarget,
    Finding,
    ReachabilityReport,
    TargetKind,
    collect_targets,
    event_context,
    probe_targets,
)
from tests.fakes_offer_health import FakeHealthStore, FakeUrlProbe

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
SHOP = "https://www.shop.example/"
OK = ProbeResult(ProbeStatus.REACHABLE, ProbeReason.OK)
DEAD = ProbeResult(ProbeStatus.UNREACHABLE, ProbeReason.CLIENT_ERROR)


def source(offer_id: str, url: str, verified: datetime | None = NOW) -> SourceUrlTarget:
    return SourceUrlTarget(offer_id, url, verified)


def link(
    offer_id: str,
    link_id: str,
    url: str,
    *,
    provider: str = "direct",
    website: str | None = SHOP,
    verified: datetime | None = NOW,
) -> DirectLinkTarget:
    return DirectLinkTarget(offer_id, link_id, provider, url, website, verified)


async def targets_of(store: FakeHealthStore, limit: int = 100) -> list[CheckTarget]:
    return list((await collect_targets(store, NOW, limit=limit)).items)


async def test_source_urls_and_direct_links_are_collected_with_their_ids() -> None:
    store = FakeHealthStore(
        sources=[source("o1", "https://a.example/offer")],
        links=[link("o2", "l2", "https://www.shop.example/deal")],
    )

    found = await targets_of(store)

    assert {(t.kind, t.offer_id, t.link_id) for t in found} == {
        (TargetKind.SOURCE_URL, "o1", None),
        (TargetKind.AFFILIATE_LINK, "o2", "l2"),
    }


@pytest.mark.parametrize(
    "provider",
    [
        "awin",
        "cj",
        "partnerize",
        "rakuten",
        "tradedoubler",
        "sample",
        "impact",
        "Direct",
        "",
    ],
)
async def test_a_link_of_any_provider_but_direct_is_never_collected(
    provider: str,
) -> None:
    store = FakeHealthStore(
        links=[
            link("o1", "l1", "https://www.awin1.com/cread.php?x=1", provider=provider)
        ]
    )

    collected = await collect_targets(store, NOW, limit=10)

    assert collected.items == ()


async def test_a_direct_link_is_collected_only_when_it_points_at_the_merchants_own_host() -> (
    None
):
    store = FakeHealthStore(
        links=[
            link("o1", "same", "https://www.shop.example/a"),
            link("o2", "bare", "https://shop.example/a"),
            link("o3", "other", "https://elsewhere.example/a"),
            link("o4", "sub", "https://tracking.shop.example/a"),
            link("o5", "nosite", "https://www.shop.example/a", website=None),
        ]
    )

    collected = await collect_targets(store, NOW, limit=10)

    assert [t.link_id for t in collected.items] == ["same", "bare"]
    assert collected.not_followable == 3


async def test_a_direct_link_with_an_unusable_url_is_passed_on_so_the_probe_can_refuse_it() -> (
    None
):
    store = FakeHealthStore(links=[link("o1", "l1", "http://www.shop.example/a")])

    collected = await collect_targets(store, NOW, limit=10)

    assert [t.link_id for t in collected.items] == ["l1"]
    assert collected.not_followable == 0


async def test_least_recently_verified_come_first_and_the_limit_truncates() -> None:
    old, new = NOW - timedelta(days=30), NOW - timedelta(days=1)
    store = FakeHealthStore(
        sources=[
            source("fresh", "https://a.example/1", new),
            source("never", "https://a.example/2", None),
        ],
        links=[link("stale", "l1", "https://www.shop.example/3", verified=old)],
    )

    found = await targets_of(store, limit=2)

    assert [t.offer_id for t in found] == ["never", "stale"]


async def test_the_limit_is_passed_to_the_store_queries() -> None:
    limits: list[int] = []

    class CountingStore(FakeHealthStore):
        async def list_source_url_targets(
            self, now: datetime, limit: int
        ) -> list[SourceUrlTarget]:
            limits.append(limit)
            return []

    store = CountingStore()

    await collect_targets(store, NOW, limit=7)

    assert limits == [7]


def test_a_target_never_shows_its_url_in_its_repr() -> None:
    target = CheckTarget(
        TargetKind.SOURCE_URL, "o1", None, "https://secret.example/x?t=1", NOW
    )

    assert "secret.example" not in repr(target) and "t=1" not in repr(target)


def target(offer_id: str, url: str, link_id: str | None = None) -> CheckTarget:
    kind = TargetKind.AFFILIATE_LINK if link_id else TargetKind.SOURCE_URL
    return CheckTarget(kind, offer_id, link_id, url, NOW)


async def test_every_outcome_category_is_recorded_against_its_offer_and_link() -> None:
    outcomes = {
        "https://a.example/ok": OK,
        "https://b.example/dead": DEAD,
        "https://c.example/blocked": ProbeResult(
            ProbeStatus.REFUSED, ProbeReason.BLOCKED_ADDRESS
        ),
        "https://d.example/auth": ProbeResult(
            ProbeStatus.INCONCLUSIVE, ProbeReason.AUTH_REQUIRED
        ),
    }
    probe = FakeUrlProbe(outcomes)
    targets = [
        target("o1", "https://a.example/ok"),
        target("o2", "https://b.example/dead", "l2"),
        target("o3", "https://c.example/blocked"),
        target("o4", "https://d.example/auth", "l4"),
    ]

    findings = await probe_targets(probe, targets)

    assert findings == [
        Finding(
            "o1", None, TargetKind.SOURCE_URL, ProbeStatus.REACHABLE, ProbeReason.OK
        ),
        Finding(
            "o2",
            "l2",
            TargetKind.AFFILIATE_LINK,
            ProbeStatus.UNREACHABLE,
            ProbeReason.CLIENT_ERROR,
        ),
        Finding(
            "o3",
            None,
            TargetKind.SOURCE_URL,
            ProbeStatus.REFUSED,
            ProbeReason.BLOCKED_ADDRESS,
        ),
        Finding(
            "o4",
            "l4",
            TargetKind.AFFILIATE_LINK,
            ProbeStatus.INCONCLUSIVE,
            ProbeReason.AUTH_REQUIRED,
        ),
    ]


async def test_an_unreachable_destination_is_reported_and_nothing_is_changed() -> None:
    probe = FakeUrlProbe({"https://b.example/dead": DEAD})

    findings = await probe_targets(probe, [target("o2", "https://b.example/dead")])

    assert [f.status for f in findings] == [ProbeStatus.UNREACHABLE]
    assert probe.urls == [
        "https://b.example/dead"
    ]  # the one request; no store is involved


async def test_a_probe_that_raises_becomes_an_inconclusive_finding_and_the_rest_continue() -> (
    None
):
    class FlakyProbe(FakeUrlProbe):
        async def check(self, url: str) -> ProbeResult:
            if "boom" in url:
                raise RuntimeError("bug")
            return await super().check(url)

    findings = await probe_targets(
        FlakyProbe(),
        [target("o1", "https://boom.example/"), target("o2", "https://a.example/")],
    )

    assert findings[0].status is ProbeStatus.INCONCLUSIVE
    assert findings[0].reason is ProbeReason.PROBE_ERROR
    assert findings[1].status is ProbeStatus.REACHABLE


async def test_requests_run_concurrently_but_never_beyond_the_bound() -> None:
    probe = FakeUrlProbe()
    targets = [target(f"o{n}", f"https://host{n}.example/") for n in range(12)]

    await probe_targets(probe, targets, concurrency=3)

    assert 1 < probe.peak_in_flight <= 3


async def test_one_host_is_never_asked_twice_at_once() -> None:
    probe = FakeUrlProbe()
    targets = [target(f"o{n}", f"https://same.example/{n}") for n in range(6)]

    await probe_targets(probe, targets, concurrency=5)

    assert probe.peak_in_flight == 1 and len(probe.urls) == 6


async def test_problems_are_logged_with_ids_and_reason_codes_only(
    caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    caplog.set_level(logging.DEBUG)
    probe = FakeUrlProbe({"https://b.example/dead?token=abc123": DEAD})
    targets = [
        target("offer_1", "https://b.example/dead?token=abc123", "link_9"),
        target("offer_2", "https://a.example/fine"),
    ]

    await probe_targets(probe, targets)

    logged = caplog.text + capsys.readouterr().out
    assert "offer_1" in logged and "link_9" in logged and "client_error" in logged
    assert "offer_2" not in logged  # reachable results are not logged one by one
    for fragment in ("b.example", "abc123", "token", "dead"):
        assert fragment not in logged


def finding(offer_id: str, status: ProbeStatus, reason: ProbeReason) -> Finding:
    return Finding(offer_id, None, TargetKind.SOURCE_URL, status, reason)


def test_the_event_context_summarises_counts_and_lists_problems_by_id_and_reason() -> (
    None
):
    report = ReachabilityReport(
        (
            finding("o1", ProbeStatus.REACHABLE, ProbeReason.OK),
            finding("o2", ProbeStatus.UNREACHABLE, ProbeReason.DNS_FAILURE),
            finding("o3", ProbeStatus.UNREACHABLE, ProbeReason.DNS_FAILURE),
            finding("o4", ProbeStatus.INCONCLUSIVE, ProbeReason.AUTH_REQUIRED),
            finding("o5", ProbeStatus.REFUSED, ProbeReason.BLOCKED_ADDRESS),
        ),
        not_followable=2,
    )

    context = event_context(3, report)

    assert context["expired"] == 3 and context["checked"] == 5
    assert context["not_followable"] == 2
    assert context["counts"] == {
        "reachable": 1,
        "inconclusive": 1,
        "unreachable": 2,
        "refused": 1,
    }
    assert context["reasons"] == {"dns_failure": 2, "blocked_address": 1}
    assert [f["offer_id"] for f in context["findings"]] == ["o2", "o3", "o5"]
    assert context["findings"][0] == {
        "offer_id": "o2",
        "link_id": None,
        "target": "source_url",
        "status": "unreachable",
        "reason": "dns_failure",
    }
    assert context["findings_omitted"] == 0


def test_the_event_context_lists_a_bounded_number_of_problems() -> None:
    many = tuple(
        finding(f"o{n}", ProbeStatus.UNREACHABLE, ProbeReason.TIMEOUT)
        for n in range(MAX_EVENT_FINDINGS + 5)
    )

    context = event_context(0, ReachabilityReport(many))

    assert len(context["findings"]) == MAX_EVENT_FINDINGS
    assert context["findings_omitted"] == 5


TRACKING_HOSTS = sorted(
    {host for hosts in PROVIDER_HOSTS.values() for host in hosts}
    | {f"www.{host}" for hosts in PROVIDER_HOSTS.values() for host in hosts}
    | {"click.linksynergy.com"}
)


@pytest.mark.parametrize("host", TRACKING_HOSTS)
async def test_a_tracking_host_is_never_collected_whatever_field_holds_it(
    host: str,
) -> None:
    store = FakeHealthStore(
        sources=[source("o1", f"https://{host}/x?a=1")],
        links=[
            link("o2", "l2", f"https://{host}/y", website=f"https://{host}"),
            link("o3", "l3", f"https://{host}/z", website=None),
        ],
    )
    probe = FakeUrlProbe()

    collected = await collect_targets(store, NOW, limit=10)
    await probe_targets(probe, collected.items)

    assert collected.items == ()
    assert collected.tracking_host_skipped == 3
    assert collected.not_followable == 0
    assert probe.urls == []


async def test_a_look_alike_of_a_tracking_host_is_still_checked() -> None:
    store = FakeHealthStore(sources=[source("o1", "https://awin1.com.evil.example/x")])

    collected = await collect_targets(store, NOW, limit=10)

    assert len(collected.items) == 1 and collected.tracking_host_skipped == 0


class TimeoutProbe(FakeUrlProbe):
    """Every request to a host whose name starts with ``dead`` times out."""

    async def check(self, url: str) -> ProbeResult:
        await super().check(url)
        if "//dead" in url:
            return ProbeResult(ProbeStatus.UNREACHABLE, ProbeReason.TIMEOUT)
        return OK


async def test_a_host_that_keeps_timing_out_is_skipped_after_three_requests() -> None:
    probe = TimeoutProbe()
    targets = [target(f"o{n}", f"https://dead.example/{n}") for n in range(10)]

    findings = await probe_targets(probe, targets)

    assert MAX_HOST_FAILURES == 3
    assert len(probe.urls) == 3
    skipped = [f for f in findings if f.reason is ProbeReason.HOST_SKIPPED]
    assert len(skipped) == 7
    assert {f.status for f in skipped} == {ProbeStatus.INCONCLUSIVE}
    assert [f.reason for f in findings[:3]] == [ProbeReason.TIMEOUT] * 3


async def test_connection_errors_also_trip_the_breaker_and_other_hosts_are_unaffected() -> (
    None
):
    class Mixed(FakeUrlProbe):
        async def check(self, url: str) -> ProbeResult:
            await super().check(url)
            if "//dead" in url:
                return ProbeResult(
                    ProbeStatus.UNREACHABLE, ProbeReason.CONNECTION_ERROR
                )
            return OK

    probe = Mixed()
    targets = [target(f"d{n}", f"https://dead.example/{n}") for n in range(5)] + [
        target("fine", "https://fine.example/")
    ]

    findings = await probe_targets(probe, targets)

    assert len(probe.urls) == 4
    assert findings[-1].status is ProbeStatus.REACHABLE


async def test_a_success_or_another_failure_resets_the_host_failure_count() -> None:
    answers = iter(
        [ProbeReason.TIMEOUT, ProbeReason.TIMEOUT, ProbeReason.OK]
        + [ProbeReason.TIMEOUT, ProbeReason.CLIENT_ERROR]
        + [ProbeReason.TIMEOUT, ProbeReason.TIMEOUT]
    )

    class Scripted(FakeUrlProbe):
        async def check(self, url: str) -> ProbeResult:
            await super().check(url)
            reason = next(answers)
            status = (
                ProbeStatus.REACHABLE
                if reason is ProbeReason.OK
                else ProbeStatus.UNREACHABLE
            )
            return ProbeResult(status, reason)

    probe = Scripted()
    targets = [target(f"o{n}", f"https://same.example/{n}") for n in range(7)]

    findings = await probe_targets(probe, targets)

    assert len(probe.urls) == 7
    assert all(f.reason is not ProbeReason.HOST_SKIPPED for f in findings)


async def test_unfinished_targets_are_reported_as_not_checked_when_time_runs_out() -> (
    None
):
    class Sleepy(FakeUrlProbe):
        async def check(self, url: str) -> ProbeResult:
            if "fast" in url:
                return OK
            await asyncio.sleep(10)
            return OK

    targets = [target("a", "https://fast.example/")] + [
        target(f"s{n}", f"https://slow{n}.example/") for n in range(4)
    ]

    started = asyncio.get_running_loop().time()
    findings = await probe_targets(Sleepy(), targets, concurrency=5, max_seconds=0.05)

    assert asyncio.get_running_loop().time() - started < 2
    assert findings[0].status is ProbeStatus.REACHABLE
    unfinished = findings[1:]
    assert [f.reason for f in unfinished] == [ProbeReason.NOT_CHECKED] * 4
    assert {f.status for f in unfinished} == {ProbeStatus.INCONCLUSIVE}
    assert [f.offer_id for f in findings] == ["a", "s0", "s1", "s2", "s3"]


async def test_staleness_compares_instants_not_wall_clock_digits() -> None:
    earlier_but_later_digits = datetime(
        2026, 1, 1, 0, 30, tzinfo=timezone(timedelta(hours=1))
    )
    later = datetime(2025, 12, 31, 23, 45, tzinfo=UTC)
    store = FakeHealthStore(
        sources=[
            source("later", "https://a.example/1", later),
            source("earlier", "https://a.example/2", earlier_but_later_digits),
        ]
    )

    assert [t.offer_id for t in await targets_of(store)] == ["earlier", "later"]


def test_the_repositorys_url_bearing_records_hide_their_urls_in_repr() -> None:
    shown = repr(source("o1", "https://secret.example/x?t=1")) + repr(
        link("o2", "l2", "https://secret.example/y?t=2", website="https://site.example")
    )

    assert "secret.example" not in shown and "site.example" not in shown
    assert "t=1" not in shown and "t=2" not in shown


async def test_after_the_deadline_every_cancelled_request_has_finished_unwinding() -> (
    None
):
    cleaned: list[str] = []

    class SlowCleanup(FakeUrlProbe):
        async def check(self, url: str) -> ProbeResult:
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                await asyncio.sleep(0.05)  # cleanup that itself awaits
                cleaned.append(url)
                raise
            return OK

    targets = [target(f"o{n}", f"https://slow{n}.example/") for n in range(4)]

    findings = await probe_targets(
        SlowCleanup(), targets, concurrency=4, max_seconds=0.05
    )

    assert len(cleaned) == 4
    assert asyncio.all_tasks() == {asyncio.current_task()}
    assert {f.reason for f in findings} == {ProbeReason.NOT_CHECKED}


async def test_an_unexpected_failure_inside_a_target_is_a_probe_error_not_dropped() -> (
    None
):
    class Broken(FakeUrlProbe):
        async def check(self, url: str) -> ProbeResult:
            raise RuntimeError("bug")

    findings = await probe_targets(Broken(), [target("o1", "https://a.example/")])

    assert (findings[0].status, findings[0].reason) == (
        ProbeStatus.INCONCLUSIVE,
        ProbeReason.PROBE_ERROR,
    )


async def test_probe_targets_itself_refuses_a_hand_built_tracking_host_target() -> None:
    probe = FakeUrlProbe()
    targets = [
        target("o1", "https://www.awin1.com/cread.php?x=1"),
        target("o2", "https://click.linksynergy.com/y", "l2"),
        target("o3", "https://a.example/ok"),
    ]

    findings = await probe_targets(probe, targets)

    assert probe.urls == ["https://a.example/ok"]
    assert [f.reason for f in findings[:2]] == [ProbeReason.TRACKING_HOST] * 2
    assert {f.status for f in findings[:2]} == {ProbeStatus.INCONCLUSIVE}


async def test_refusals_and_probe_errors_between_timeouts_do_not_reset_the_breaker() -> (
    None
):
    answers = iter(
        [
            ProbeResult(ProbeStatus.UNREACHABLE, ProbeReason.TIMEOUT),
            ProbeResult(ProbeStatus.REFUSED, ProbeReason.INVALID_URL),
            ProbeResult(ProbeStatus.UNREACHABLE, ProbeReason.TIMEOUT),
            ProbeResult(ProbeStatus.REFUSED, ProbeReason.BLOCKED_ADDRESS),
            ProbeResult(ProbeStatus.INCONCLUSIVE, ProbeReason.PROBE_ERROR),
            ProbeResult(ProbeStatus.UNREACHABLE, ProbeReason.TIMEOUT),
        ]
    )

    class Scripted(FakeUrlProbe):
        async def check(self, url: str) -> ProbeResult:
            await super().check(url)
            return next(answers)

    probe = Scripted()
    targets = [target(f"o{n}", f"https://same.example/{n}") for n in range(8)]

    findings = await probe_targets(probe, targets)

    assert len(probe.urls) == 6
    assert [f.reason for f in findings[6:]] == [ProbeReason.HOST_SKIPPED] * 2
