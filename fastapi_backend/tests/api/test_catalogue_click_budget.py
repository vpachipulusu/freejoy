"""The per-offer recording budget of ``POST /v1/catalogue/offers/{id}/click`` (FREEJ-62).

A request classed ``BOT`` spends its own small per-offer bucket, never the main one, so automated traffic
cannot use up the budget genuine clicks need. Every other class, and a missing class, spends the main bucket as
before. The limiter is a counting fake that really enforces each policy per identifier.
"""

import logging
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.deps import get_rate_limiter
from app.api.v1 import catalogue_click
from app.api.v1.catalogue_click import get_click_service
from app.core.rate_limit import (
    OUTBOUND_CLICK_POLICY,
    OUTBOUND_OFFER_BOT_POLICY,
    OUTBOUND_OFFER_POLICY,
)
from app.main import create_app
from app.models.platform.click_event import UserAgentClass
from app.services.catalogue import CatalogueService
from app.services.outbound_clicks import OutboundClickService
from tests.fakes import FakeRateLimiter, ScopedOutageRateLimiter, as_limiter
from tests.fakes_catalogue import NOW, FakeCatalogueRepository, make_offer
from tests.fakes_outbound import FakeClickStore, FakeCommitter, make_link

KEY = "test-only-internal-service-key-not-for-deployment"
PATH = "/v1/catalogue/offers/offer_1/click"
DESTINATION = "https://www.awin1.com/cread.php?awinmid=1&awinaffid=2"
MAIN = OUTBOUND_OFFER_POLICY.scope
BOT = OUTBOUND_OFFER_BOT_POLICY.scope
NON_BOT_CLASSES = ["DESKTOP", "MOBILE", "TABLET", "OTHER", None]


def build(
    limiter: FakeRateLimiter,
) -> tuple[AsyncClient, FakeClickStore, FakeCommitter]:
    store = FakeClickStore([make_link()])
    committer = FakeCommitter()
    catalogue = CatalogueService(
        FakeCatalogueRepository(by_id=make_offer()), clock=lambda: NOW
    )
    service = OutboundClickService(
        catalogue, store, committer, allow_sample_provider=False
    )
    app = create_app()
    app.dependency_overrides[get_click_service] = lambda: service
    app.dependency_overrides[get_rate_limiter] = lambda: as_limiter(limiter)
    client = AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
        headers={"X-Internal-Key": KEY},
    )
    return client, store, committer


class ListHandler(logging.Handler):
    """Collects warning records from the click logger (the app's logging does not propagate to caplog)."""

    def __init__(self, records: list[logging.LogRecord]) -> None:
        super().__init__(level=logging.WARNING)
        self._records = records

    def emit(self, record: logging.LogRecord) -> None:
        self._records.append(record)


def body_for(user_agent_class: str | None) -> dict[str, Any]:
    return {} if user_agent_class is None else {"user_agent_class": user_agent_class}


async def click(http: AsyncClient, n: int, user_agent_class: str | None) -> Any:
    """One click from its own client address, so the per-address limit never interferes."""
    return await http.post(
        PATH,
        json=body_for(user_agent_class),
        headers={"X-Client-IP": f"10.{n // 250}.{n % 250}.1"},
    )


def scopes(limiter: FakeRateLimiter) -> list[str]:
    return [policy.scope for _, policy in limiter.hits]


def test_the_two_policies_are_distinct_and_the_bot_bucket_is_small() -> None:
    assert BOT != MAIN
    assert OUTBOUND_OFFER_BOT_POLICY.limit == 60
    assert OUTBOUND_OFFER_BOT_POLICY.window_seconds == 60
    assert OUTBOUND_OFFER_BOT_POLICY.fail_closed is False
    assert OUTBOUND_OFFER_BOT_POLICY.limit < OUTBOUND_OFFER_POLICY.limit


async def test_a_flood_of_bot_requests_leaves_the_budget_for_genuine_clicks() -> None:
    limiter = FakeRateLimiter()
    http, store, _ = build(limiter)
    flood = OUTBOUND_OFFER_POLICY.limit

    async with http:
        bots = [await click(http, n, "BOT") for n in range(flood)]
        desktop = await click(http, flood, "DESKTOP")

    assert all(r.status_code == 200 for r in bots)
    assert desktop.status_code == 200
    assert desktop.json()["data"]["destination_url"] == DESTINATION
    classes = [c["user_agent_class"] for c in store.clicks]
    assert classes.count(UserAgentClass.DESKTOP) == 1
    assert classes.count(UserAgentClass.BOT) == OUTBOUND_OFFER_BOT_POLICY.limit
    assert scopes(limiter).count(MAIN) == 1


async def test_the_request_past_the_bot_cap_is_redirected_not_recorded_and_the_main_bucket_is_untouched() -> (
    None
):
    limiter = FakeRateLimiter()
    http, store, committer = build(limiter)
    cap = OUTBOUND_OFFER_BOT_POLICY.limit

    async with http:
        responses = [await click(http, n, "BOT") for n in range(cap + 1)]

    assert [r.status_code for r in responses] == [200] * (cap + 1)
    assert responses[-1].json()["data"]["destination_url"] == DESTINATION
    assert "Retry-After" not in responses[-1].headers
    assert responses[-1].headers["Cache-Control"] == "no-store"
    assert len(store.clicks) == cap
    assert committer.commits == cap
    assert MAIN not in scopes(limiter)


@pytest.mark.parametrize("user_agent_class", NON_BOT_CLASSES)
async def test_every_other_class_and_a_missing_class_spends_only_the_main_bucket(
    user_agent_class: str | None,
) -> None:
    limiter = FakeRateLimiter()
    http, store, _ = build(limiter)

    async with http:
        response = await click(http, 1, user_agent_class)

    assert response.status_code == 200
    assert limiter.hits == [
        ("outbound_click:ip:10.0.1.1", OUTBOUND_CLICK_POLICY),
        ("outbound_offer:offer:offer_1", OUTBOUND_OFFER_POLICY),
    ]
    assert len(store.clicks) == 1


async def test_a_bot_request_spends_only_the_bot_bucket_under_its_own_identifier() -> (
    None
):
    limiter = FakeRateLimiter()
    http, store, _ = build(limiter)

    async with http:
        response = await click(http, 1, "BOT")

    assert response.status_code == 200
    assert limiter.hits == [
        ("outbound_click:ip:10.0.1.1", OUTBOUND_CLICK_POLICY),
        ("outbound_offer_bot:offer:offer_1", OUTBOUND_OFFER_BOT_POLICY),
    ]
    assert len(store.clicks) == 1


async def test_each_offer_has_its_own_bot_bucket() -> None:
    limiter = FakeRateLimiter()
    http, _, _ = build(limiter)

    async with http:
        await http.post(
            "/v1/catalogue/offers/other_offer/click", json={"user_agent_class": "BOT"}
        )

    assert limiter.hits[1][0] == "outbound_offer_bot:offer:other_offer"


@pytest.mark.parametrize(
    ("user_agent_class", "failing_scope"),
    [("BOT", BOT), ("DESKTOP", MAIN), (None, MAIN)],
)
async def test_a_limiter_outage_on_either_bucket_records_warns_and_reports(
    user_agent_class: str | None,
    failing_scope: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reported: list[BaseException] = []
    warnings: list[logging.LogRecord] = []
    monkeypatch.setattr(catalogue_click, "report_exception", reported.append)
    handler = ListHandler(warnings)
    limiter = ScopedOutageRateLimiter({failing_scope})
    http, store, _ = build(limiter)

    logger = logging.getLogger("yourapp.outbound")
    logger.addHandler(handler)
    try:
        async with http:
            response = await click(http, 1, user_agent_class)
    finally:
        logger.removeHandler(handler)

    assert response.status_code == 200
    assert len(store.clicks) == 1
    assert len(reported) == 1
    assert [w.getMessage() for w in warnings] == [
        "offer cap limiter unavailable, recording the click"
    ]
    assert warnings[0].__dict__["request_id"] == response.headers["X-Request-ID"]
    assert scopes(limiter).count(failing_scope) == 1


async def test_a_refused_body_spends_no_offer_budget() -> None:
    limiter = FakeRateLimiter()
    http, store, _ = build(limiter)

    async with http:
        response = await http.post(PATH, json={"user_agent": "curl/8"})

    assert response.status_code == 422
    assert scopes(limiter) == [OUTBOUND_CLICK_POLICY.scope]
    assert store.clicks == []


@pytest.mark.parametrize(
    "request_kwargs",
    [
        {"json": {"user_agent_class": "DESKTOP", "referrer_host": "news.example"}},
        {"json": {"user_agent_class": "DESKTOP"}},
        {"json": {}},
        {"content": b"null", "headers": {"Content-Type": "application/json"}},
        {},
    ],
    ids=["full body", "class only", "empty object", "json null", "no body"],
)
async def test_every_body_shape_still_works_with_the_class_read_by_the_dependency(
    request_kwargs: dict[str, Any],
) -> None:
    limiter = FakeRateLimiter()
    http, store, _ = build(limiter)

    async with http:
        response = await http.post(PATH, **request_kwargs)

    assert response.status_code == 200
    assert response.json()["data"]["destination_url"] == DESTINATION
    assert len(store.clicks) == 1
    assert scopes(limiter) == [OUTBOUND_CLICK_POLICY.scope, MAIN]
