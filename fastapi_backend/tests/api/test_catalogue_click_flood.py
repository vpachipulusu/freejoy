"""The per-address recording limit of ``POST /v1/catalogue/offers/{id}/click`` (FREEJ-63).

A flood that does not identify itself as automated (an empty or ordinary browser user agent, so a device class of
``DESKTOP`` or ``OTHER``) used to fill an offer's whole recording budget from a handful of addresses. Now one
client address may have ``OUTBOUND_CLICK_OFFER_POLICY`` clicks on one offer recorded a minute, whatever its
class, and the main per-offer budget going spent is logged once a minute. The visitor is always still sent on.
The limiter is a counting fake that really enforces each policy per identifier.
"""

import json
import logging
from typing import Any, Self

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.auth import require_internal_key
from app.api.deps import get_rate_limiter
from app.api.rate_limit import limit_by_client_ip
from app.api.v1 import catalogue_click
from app.core.logging import JsonFormatter
from app.core.rate_limit import (
    CLICK_IPV6_GROUP_PREFIX,
    OUTBOUND_CLICK_OFFER_POLICY,
    OUTBOUND_CLICK_POLICY,
    OUTBOUND_OFFER_BOT_POLICY,
    OUTBOUND_OFFER_CAP_SIGNAL_POLICY,
    OUTBOUND_OFFER_POLICY,
    PUBLIC_CATALOGUE_POLICY,
)
from tests.api.test_catalogue_click_budget import (
    DESTINATION,
    KEY,
    PATH,
    ListHandler,
    build,
    click,
    scopes,
)
from tests.fakes import FakeRateLimiter, ScriptedRateLimiter, as_limiter

CLICK = OUTBOUND_CLICK_POLICY.scope
ADDRESS = OUTBOUND_CLICK_OFFER_POLICY.scope
MAIN = OUTBOUND_OFFER_POLICY.scope
BOT = OUTBOUND_OFFER_BOT_POLICY.scope
SIGNAL = OUTBOUND_OFFER_CAP_SIGNAL_POLICY.scope
CAP_MESSAGE = "offer recording cap reached"
PER_ADDRESS = OUTBOUND_CLICK_OFFER_POLICY.limit


def one_address(n: int) -> dict[str, str]:
    return {"X-Client-IP": f"10.{n // 250}.{n % 250}.1"}


async def post_from(
    http: AsyncClient,
    address: str,
    user_agent_class: str | None = "DESKTOP",
    path: str = PATH,
) -> Any:
    body = {} if user_agent_class is None else {"user_agent_class": user_agent_class}
    return await http.post(path, json=body, headers={"X-Client-IP": address})


class Logged:
    """Warning records from the click logger while the block runs."""

    def __init__(self) -> None:
        self.records: list[logging.LogRecord] = []
        self._handler = ListHandler(self.records)
        self._logger = logging.getLogger("yourapp.outbound")

    def __enter__(self) -> Self:
        self._logger.addHandler(self._handler)
        return self

    def __exit__(self, *exc: object) -> None:
        self._logger.removeHandler(self._handler)

    def messages(self) -> list[str]:
        return [record.getMessage() for record in self.records]


def test_the_policies_have_the_figures_the_docs_quote() -> None:
    assert (ADDRESS, PER_ADDRESS, 60) == ("outbound_click_offer", 5, 60)
    assert OUTBOUND_CLICK_OFFER_POLICY.fail_closed is False
    assert (SIGNAL, OUTBOUND_OFFER_CAP_SIGNAL_POLICY.limit) == (
        "outbound_offer_cap_signal",
        1,
    )
    assert OUTBOUND_OFFER_CAP_SIGNAL_POLICY.window_seconds == 60
    assert OUTBOUND_OFFER_CAP_SIGNAL_POLICY.fail_closed is False
    assert CLICK_IPV6_GROUP_PREFIX == 56
    assert PER_ADDRESS < OUTBOUND_CLICK_POLICY.limit < OUTBOUND_OFFER_POLICY.limit
    assert len({CLICK, ADDRESS, MAIN, BOT, SIGNAL}) == 5


# --- one address cannot use more than its share of an offer's budget ------------------


async def test_one_address_sending_600_desktop_clicks_gets_5_recorded_and_all_are_sent_on() -> (
    None
):
    # The per-address answer of 429 (60 a minute) is switched off here, to show the new limit alone.
    limiter = ScriptedRateLimiter(unlimited_scopes={CLICK})
    http, store, committer = build(limiter)
    flood = OUTBOUND_OFFER_POLICY.limit

    async with http:
        responses = [await post_from(http, "198.51.100.7") for _ in range(flood)]
        other = await post_from(http, "198.51.100.8")

    assert [r.status_code for r in responses] == [200] * flood
    assert {r.json()["data"]["destination_url"] for r in responses} == {DESTINATION}
    assert all("Retry-After" not in r.headers for r in responses)
    assert len(store.clicks) == PER_ADDRESS + 1
    assert committer.commits == PER_ADDRESS + 1
    assert other.status_code == 200
    assert scopes(limiter).count(MAIN) == PER_ADDRESS + 1
    assert scopes(limiter).count(ADDRESS) == flood + 1


async def test_with_the_per_address_429_on_a_flood_is_cut_off_at_60_and_5_are_recorded() -> (
    None
):
    limiter = FakeRateLimiter()
    http, store, _ = build(limiter)
    flood = OUTBOUND_OFFER_POLICY.limit

    async with http:
        responses = [await post_from(http, "198.51.100.7") for _ in range(flood)]

    statuses = [r.status_code for r in responses]
    assert statuses.count(200) == OUTBOUND_CLICK_POLICY.limit
    assert statuses.count(429) == flood - OUTBOUND_CLICK_POLICY.limit
    assert len(store.clicks) == PER_ADDRESS
    assert scopes(limiter).count(MAIN) == PER_ADDRESS
    assert scopes(limiter).count(ADDRESS) == OUTBOUND_CLICK_POLICY.limit


@pytest.mark.parametrize("user_agent_class", ["DESKTOP", "OTHER", "MOBILE", None])
async def test_the_limit_applies_to_every_class_that_is_not_bot(
    user_agent_class: str | None,
) -> None:
    limiter = FakeRateLimiter()
    http, store, _ = build(limiter)

    async with http:
        for _ in range(PER_ADDRESS + 3):
            response = await post_from(http, "198.51.100.7", user_agent_class)
            assert response.status_code == 200

    assert len(store.clicks) == PER_ADDRESS


async def test_the_limit_is_per_offer() -> None:
    limiter = FakeRateLimiter()
    http, store, _ = build(limiter)
    other_path = "/v1/catalogue/offers/offer_2/click"

    async with http:
        for _ in range(PER_ADDRESS + 2):
            await post_from(http, "198.51.100.7")
            await post_from(http, "198.51.100.7", path=other_path)

    # The fake catalogue answers every id with the one offer, so the clicks are counted together.
    assert len(store.clicks) == 2 * PER_ADDRESS
    assert (
        "outbound_click_offer:ip:198.51.100.7:offer:offer_1",
        OUTBOUND_CLICK_OFFER_POLICY,
    ) in limiter.hits
    assert (
        "outbound_click_offer:ip:198.51.100.7:offer:offer_2",
        OUTBOUND_CLICK_OFFER_POLICY,
    ) in limiter.hits


async def test_a_bot_past_the_address_limit_does_not_hit_the_bot_bucket() -> None:
    limiter = FakeRateLimiter()
    http, store, _ = build(limiter)

    async with http:
        responses = [
            await post_from(http, "198.51.100.7", "BOT") for _ in range(PER_ADDRESS + 2)
        ]

    assert [r.status_code for r in responses] == [200] * (PER_ADDRESS + 2)
    assert len(store.clicks) == PER_ADDRESS
    assert scopes(limiter).count(BOT) == PER_ADDRESS
    assert MAIN not in scopes(limiter)


async def test_a_denied_address_hits_the_click_limit_and_the_address_limit_and_nothing_else() -> (
    None
):
    limiter = FakeRateLimiter()
    http, _, _ = build(limiter)

    async with http:
        for _ in range(PER_ADDRESS):
            await post_from(http, "198.51.100.7")
        limiter.hits.clear()
        response = await post_from(http, "198.51.100.7")

    assert response.status_code == 200
    assert limiter.hits == [
        ("outbound_click:ip:198.51.100.7", OUTBOUND_CLICK_POLICY),
        (
            "outbound_click_offer:ip:198.51.100.7:offer:offer_1",
            OUTBOUND_CLICK_OFFER_POLICY,
        ),
    ]


async def test_a_request_the_per_address_429_refuses_hits_no_recording_limit() -> None:
    limiter = ScriptedRateLimiter(denied_scopes={CLICK})
    http, store, _ = build(limiter)

    async with http:
        response = await post_from(http, "198.51.100.7")

    assert response.status_code == 429
    assert scopes(limiter) == [CLICK]
    assert store.clicks == []


async def test_a_refused_body_hits_no_recording_limit() -> None:
    limiter = FakeRateLimiter()
    http, store, _ = build(limiter)

    async with http:
        response = await http.post(PATH, json={"user_agent": "curl/8"})

    assert response.status_code == 422
    assert scopes(limiter) == [CLICK]
    assert store.clicks == []


# --- the main budget spent is signalled once a minute --------------------------------


async def test_a_full_main_bucket_logs_one_warning_however_many_requests_are_denied() -> (
    None
):
    limiter = FakeRateLimiter()
    http, store, _ = build(limiter)
    addresses = OUTBOUND_OFFER_POLICY.limit // PER_ADDRESS  # 120

    with Logged() as logged:
        async with http:
            for n in range(addresses):
                for _ in range(PER_ADDRESS):
                    assert (await post_from(http, f"10.1.{n}.1")).status_code == 200
            assert len(store.clicks) == OUTBOUND_OFFER_POLICY.limit
            assert logged.messages() == []

            first = await post_from(http, "10.2.0.1")
            more = [await post_from(http, f"10.2.{n}.1") for n in range(1, 6)]

    assert first.status_code == 200
    assert first.json()["data"]["destination_url"] == DESTINATION
    assert [r.status_code for r in more] == [200] * 5
    assert len(store.clicks) == OUTBOUND_OFFER_POLICY.limit
    assert logged.messages() == [CAP_MESSAGE]
    record = logged.records[0]
    assert record.levelno == logging.WARNING
    assert record.__dict__["offer_id"] == "offer_1"
    assert record.__dict__["scope"] == MAIN
    assert record.__dict__["request_id"] == first.headers["X-Request-ID"]
    assert record.exc_info is None
    # Only the first denial spent the signal; each later one found it spent.
    assert scopes(limiter).count(SIGNAL) == 6
    assert (f"{SIGNAL}:offer:offer_1", OUTBOUND_OFFER_CAP_SIGNAL_POLICY) in limiter.hits


async def test_the_warning_names_the_offer_and_scope_and_nothing_about_the_visitor() -> (
    None
):
    limiter = ScriptedRateLimiter(denied_scopes={MAIN})
    http, _, _ = build(limiter)

    with Logged() as logged:
        async with http:
            await http.post(
                PATH,
                json={"user_agent_class": "DESKTOP", "referrer_host": "news.example"},
                headers={"X-Client-IP": "203.0.113.99", "User-Agent": "Secret/9.9"},
            )

    [record] = logged.records
    payload = json.loads(JsonFormatter().format(record))
    assert payload["message"] == CAP_MESSAGE
    assert payload["offer_id"] == "offer_1"
    assert payload["scope"] == MAIN
    assert payload["level"] == "WARNING"
    dump = json.dumps(payload)
    for fragment in ("awin", "203.0.113.99", "Secret", "news.example"):
        assert fragment not in dump


async def test_the_signal_is_sent_to_sentry_never(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reported: list[BaseException] = []
    monkeypatch.setattr(catalogue_click, "report_exception", reported.append)
    http, _, _ = build(ScriptedRateLimiter(denied_scopes={MAIN}))

    async with http:
        await post_from(http, "198.51.100.7")

    assert reported == []


async def test_the_bot_bucket_and_the_address_limit_give_no_signal() -> None:
    bots = ScriptedRateLimiter(denied_scopes={BOT})
    addresses = FakeRateLimiter()

    with Logged() as logged:
        http, _, _ = build(bots)
        async with http:
            await post_from(http, "198.51.100.7", "BOT")
        http, _, _ = build(addresses)
        async with http:
            for _ in range(PER_ADDRESS + 2):
                await post_from(http, "198.51.100.7")

    assert logged.messages() == []
    assert SIGNAL not in scopes(bots) + scopes(addresses)


async def test_an_allowed_main_hit_costs_no_signal_hit() -> None:
    limiter = FakeRateLimiter()
    http, _, _ = build(limiter)

    async with http:
        await post_from(http, "198.51.100.7")

    assert scopes(limiter) == [CLICK, ADDRESS, MAIN]


# --- limiter outages -----------------------------------------------------------------


async def test_an_outage_on_the_address_limit_records_warns_and_reports_and_still_hits_the_offer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reported: list[BaseException] = []
    monkeypatch.setattr(catalogue_click, "report_exception", reported.append)
    limiter = ScriptedRateLimiter(failing_scopes={ADDRESS})
    http, store, _ = build(limiter)

    with Logged() as logged:
        async with http:
            response = await click(http, 1, "DESKTOP")

    assert response.status_code == 200
    assert len(store.clicks) == 1
    assert len(reported) == 1
    assert logged.messages() == [
        "click address limiter unavailable, recording the click"
    ]
    assert logged.records[0].__dict__["request_id"] == response.headers["X-Request-ID"]
    assert scopes(limiter) == [CLICK, ADDRESS, MAIN]


async def test_an_outage_on_the_signal_hit_is_silent_and_changes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reported: list[BaseException] = []
    monkeypatch.setattr(catalogue_click, "report_exception", reported.append)
    limiter = ScriptedRateLimiter(denied_scopes={MAIN}, failing_scopes={SIGNAL})
    http, store, _ = build(limiter)

    with Logged() as logged:
        async with http:
            response = await click(http, 1, "DESKTOP")

    assert response.status_code == 200
    assert response.json()["data"]["destination_url"] == DESTINATION
    assert store.clicks == []
    assert logged.records == []
    assert reported == []
    assert scopes(limiter) == [CLICK, ADDRESS, MAIN, SIGNAL]


# --- IPv6 is grouped by /56 for the click keys only ----------------------------------


async def test_two_64s_in_one_56_share_the_click_key_and_the_address_key() -> None:
    limiter = FakeRateLimiter()
    http, store, _ = build(limiter)

    async with http:
        for n in range(PER_ADDRESS + 2):
            await post_from(http, f"2001:db8:1:2a{n:02x}::1")  # one /56, a /64 each
        other = await post_from(http, "2001:db8:1:2b00::1")  # another /56

    keys = {identifier for identifier, _ in limiter.hits}
    assert "outbound_click:ip:2001:db8:1:2a00::/56" in keys
    assert "outbound_click_offer:ip:2001:db8:1:2a00::/56:offer:offer_1" in keys
    assert "outbound_click:ip:2001:db8:1:2b00::/56" in keys
    assert other.status_code == 200
    assert len(store.clicks) == PER_ADDRESS + 1


async def test_a_non_click_policy_still_separates_the_64s() -> None:
    app = FastAPI()

    @app.get(
        "/probe",
        dependencies=[
            Depends(require_internal_key),
            Depends(limit_by_client_ip(PUBLIC_CATALOGUE_POLICY)),
        ],
    )
    async def probe() -> dict[str, bool]:
        return {"ok": True}

    limiter = FakeRateLimiter()
    app.dependency_overrides[get_rate_limiter] = lambda: as_limiter(limiter)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-Internal-Key": KEY},
    ) as http:
        for address in ("2001:db8:1:2a01::1", "2001:db8:1:2a02::1"):
            await http.get("/probe", headers={"X-Client-IP": address})

    assert [identifier for identifier, _ in limiter.hits] == [
        "public_catalogue:ip:2001:db8:1:2a01::/64",
        "public_catalogue:ip:2001:db8:1:2a02::/64",
    ]


async def test_ipv4_keys_are_unchanged() -> None:
    limiter = FakeRateLimiter()
    http, _, _ = build(limiter)

    async with http:
        await post_from(http, "203.0.113.7")
        await post_from(http, "::ffff:203.0.113.8")

    assert [identifier for identifier, _ in limiter.hits][:2] == [
        "outbound_click:ip:203.0.113.7",
        "outbound_click_offer:ip:203.0.113.7:offer:offer_1",
    ]
    assert "outbound_click:ip:203.0.113.8" in {i for i, _ in limiter.hits}
