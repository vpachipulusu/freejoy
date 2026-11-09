"""Which URLs of live offers are checked, and what is reported about them (report only).

Targets, for offers that are ``PUBLISHED`` and not ended:

* the offer's ``source_url``, and
* its active affiliate links whose provider is exactly ``direct`` and whose host is one
  ``/go/`` would follow for that merchant (``affiliate_hosts.destination_allowed``).

A network provider's link (awin, cj, partnerize, rakuten, tradedoubler, anything that is not
``direct``) is never requested, not even for a HEAD: a request to a tracking link is a click at the
network, and it would be counted against the commission programme. The repository leaves those
links out in SQL and ``collect_targets`` refuses them again, so one mistake does not reach the
network. ``sample`` links are not requested either (they are ``example.com`` placeholders).

Nothing here edits an offer or a link. An unreachable destination is a ``Finding`` for a human to
act on; the offer stays live. ``Finding`` and the log lines carry offer ids, link ids and reason
codes, never a URL (``CheckTarget.url`` is hidden from ``repr`` too).
"""

import asyncio
import enum
import logging
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol
from urllib.parse import urlsplit

from app.core.affiliate_hosts import DIRECT_PROVIDER, destination_allowed
from app.core.catalogue_urls import InvalidUrl, validate_https_url
from app.core.url_probe import ProbeReason, ProbeResult, ProbeStatus, UrlProbe
from app.repositories.offer_health import DirectLinkTarget, SourceUrlTarget

logger = logging.getLogger("yourapp.offer_health")

DEFAULT_LIMIT = 500
DEFAULT_CONCURRENCY = 5
MAX_EVENT_FINDINGS = 50
_LONGEST_AGO = datetime.min.replace(tzinfo=None)


class TargetKind(enum.StrEnum):
    SOURCE_URL = "source_url"
    AFFILIATE_LINK = "affiliate_link"


class ReachabilityStore(Protocol):
    async def list_source_url_targets(
        self, now: datetime, limit: int
    ) -> list[SourceUrlTarget]: ...
    async def list_direct_link_targets(
        self, now: datetime, limit: int
    ) -> list[DirectLinkTarget]: ...


@dataclass(frozen=True)
class CheckTarget:
    kind: TargetKind
    offer_id: str
    link_id: str | None
    url: str = field(repr=False)
    last_verified_at: datetime | None = field(repr=False)


@dataclass(frozen=True)
class Targets:
    items: tuple[CheckTarget, ...]
    not_followable: (
        int  # direct links skipped because /go/ would not follow them either
    )


@dataclass(frozen=True)
class Finding:
    offer_id: str
    link_id: str | None
    kind: TargetKind
    status: ProbeStatus
    reason: ProbeReason


@dataclass(frozen=True)
class ReachabilityReport:
    findings: tuple[Finding, ...]
    not_followable: int = 0

    @property
    def checked(self) -> int:
        return len(self.findings)

    def count(self, status: ProbeStatus) -> int:
        return sum(1 for finding in self.findings if finding.status is status)

    @property
    def problems(self) -> tuple[Finding, ...]:
        """Findings a human should look at: unreachable, or refused by a safety rule."""
        return tuple(
            f
            for f in self.findings
            if f.status in (ProbeStatus.UNREACHABLE, ProbeStatus.REFUSED)
        )

    def reason_counts(self) -> dict[str, int]:
        return dict(Counter(finding.reason.value for finding in self.problems))


def _host_of(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def _link_is_followable(link: DirectLinkTarget) -> bool:
    """Whether a ``direct`` link is one the redirect would follow.

    A URL that fails validation is let through on purpose: the probe refuses it and reports
    ``invalid_url``, which is the signal an editor needs. Only a valid URL on a host the merchant
    does not own is held back.
    """
    if link.provider != DIRECT_PROVIDER:
        return False
    try:
        host = urlsplit(validate_https_url(link.url)).hostname or ""
    except InvalidUrl:
        return True
    return destination_allowed(
        link.provider, host, merchant_website=link.merchant_website, allow_sample=False
    )


def _by_staleness(target: CheckTarget) -> tuple[datetime, str, str]:
    verified = target.last_verified_at
    when = verified.replace(tzinfo=None) if verified else _LONGEST_AGO
    return when, target.offer_id, target.link_id or ""


async def collect_targets(
    store: ReachabilityStore, now: datetime, *, limit: int = DEFAULT_LIMIT
) -> Targets:
    """Up to ``limit`` URLs to check, offers least recently verified first."""
    sources = await store.list_source_url_targets(now, limit)
    direct_links = await store.list_direct_link_targets(now, limit)
    followable = [link for link in direct_links if _link_is_followable(link)]
    items = [
        CheckTarget(TargetKind.SOURCE_URL, s.offer_id, None, s.url, s.last_verified_at)
        for s in sources
    ] + [
        CheckTarget(
            TargetKind.AFFILIATE_LINK,
            link.offer_id,
            link.link_id,
            link.url,
            link.last_verified_at,
        )
        for link in followable
    ]
    ordered = sorted(items, key=_by_staleness)[:limit]
    skipped = sum(1 for link in direct_links if link.provider == DIRECT_PROVIDER) - len(
        followable
    )
    return Targets(tuple(ordered), skipped)


async def _check(probe: UrlProbe, url: str) -> ProbeResult:
    try:
        return await probe.check(url)
    except Exception:  # noqa: BLE001  (one bad probe must not stop the run; no detail is kept)
        return ProbeResult(ProbeStatus.INCONCLUSIVE, ProbeReason.PROBE_ERROR)


def _log_problem(finding: Finding) -> None:
    logger.warning(
        "offer link problem: offer_id=%s link_id=%s target=%s status=%s reason=%s",
        finding.offer_id,
        finding.link_id or "-",
        finding.kind.value,
        finding.status.value,
        finding.reason.value,
    )


async def probe_targets(
    probe: UrlProbe,
    targets: Sequence[CheckTarget],
    *,
    concurrency: int = DEFAULT_CONCURRENCY,
) -> list[Finding]:
    """Check every target, at most ``concurrency`` at once and one at a time per host.

    Findings come back in the order of ``targets``. Unreachable and refused results are logged
    (ids and reason code only).
    """
    gate = asyncio.Semaphore(concurrency)
    host_locks: dict[str, asyncio.Lock] = {}

    async def one(target: CheckTarget) -> Finding:
        lock = host_locks.setdefault(_host_of(target.url), asyncio.Lock())
        async with (
            lock,
            gate,
        ):  # the host lock first, so a queue on one host holds no slot
            result = await _check(probe, target.url)
        return Finding(
            target.offer_id, target.link_id, target.kind, result.status, result.reason
        )

    findings = list(await asyncio.gather(*(one(target) for target in targets)))
    for finding in findings:
        if finding.status in (ProbeStatus.UNREACHABLE, ProbeStatus.REFUSED):
            _log_problem(finding)
    return findings


def event_context(expired: int, report: ReachabilityReport) -> dict[str, Any]:
    """The run summary a monitoring event carries: counts, offer ids, link ids and reason codes.

    No URL, host or error text is ever in it. At most ``MAX_EVENT_FINDINGS`` problems are listed;
    ``findings_omitted`` says how many more there were.
    """
    problems = report.problems
    return {
        "expired": expired,
        "checked": report.checked,
        "not_followable": report.not_followable,
        "counts": {status.value: report.count(status) for status in ProbeStatus},
        "reasons": report.reason_counts(),
        "findings": [
            {
                "offer_id": finding.offer_id,
                "link_id": finding.link_id,
                "target": finding.kind.value,
                "status": finding.status.value,
                "reason": finding.reason.value,
            }
            for finding in problems[:MAX_EVENT_FINDINGS]
        ],
        "findings_omitted": max(0, len(problems) - MAX_EVENT_FINDINGS),
    }
