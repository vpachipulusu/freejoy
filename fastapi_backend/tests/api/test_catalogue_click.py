"""POST /v1/catalogue/offers/{id}/click: routes, envelope, headers and refusals, over fakes.

The route, service, host allowlist, URL validation and rate limit are real; the offer lookup
and the click store are fakes.
"""

from datetime import timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.deps import get_rate_limiter
from app.api.v1.catalogue_click import get_click_service
from app.core.rate_limit import OUTBOUND_CLICK_POLICY, OUTBOUND_OFFER_POLICY
from app.main import create_app
from app.models.platform.click_event import UserAgentClass
from app.models.platform.offer import OfferStatus
from app.repositories.catalogue import OfferRecord
from app.repositories.outbound_clicks import LinkRecord
from app.services.catalogue import CatalogueService
from app.services.outbound_clicks import OutboundClickService
from tests.fakes import FakeRateLimiter, RaisingRateLimiter, as_limiter
from tests.fakes_catalogue import NOW, FakeCatalogueRepository, make_offer
from tests.fakes_outbound import FakeClickStore, FakeCommitter, make_link

KEY = "test-only-internal-service-key-not-for-deployment"
PATH = "/v1/catalogue/offers/offer_1/click"
DESTINATION = "https://www.awin1.com/cread.php?awinmid=1&awinaffid=2"


def build(
    offer: OfferRecord | None = None,
    links: list[LinkRecord] | None = None,
    limiter: FakeRateLimiter | RaisingRateLimiter | None = None,
    error: Exception | None = None,
) -> tuple[
    AsyncClient, FakeClickStore, FakeCommitter, FakeRateLimiter | RaisingRateLimiter
]:
    limiter = limiter or FakeRateLimiter()
    store = FakeClickStore([make_link()] if links is None else links)
    committer = FakeCommitter()
    catalogue = CatalogueService(
        FakeCatalogueRepository(by_id=offer, error=error), clock=lambda: NOW
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
    return client, store, committer, limiter


async def test_a_live_offer_returns_the_envelope_with_the_destination() -> None:
    http, store, committer, _ = build(make_offer())

    async with http:
        response = await http.post(PATH)

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "data": {"destination_url": DESTINATION},
    }
    assert response.headers["Cache-Control"] == "no-store"
    assert len(store.clicks) == 1 and committer.commits == 1


async def test_the_response_sets_no_cookie() -> None:
    http, _, _, _ = build(make_offer())

    async with http:
        response = await http.post(PATH)

    assert "set-cookie" not in response.headers


async def test_the_coarse_referrer_and_device_class_are_recorded() -> None:
    http, store, _, _ = build(make_offer())

    async with http:
        response = await http.post(
            PATH, json={"referrer_host": "News.Example", "user_agent_class": "MOBILE"}
        )

    assert response.status_code == 200
    assert store.clicks[0]["referrer_host"] == "news.example"
    assert store.clicks[0]["user_agent_class"] is UserAgentClass.MOBILE


async def test_a_referrer_that_is_not_a_host_is_dropped_not_stored() -> None:
    http, store, _, _ = build(make_offer())

    async with http:
        response = await http.post(
            PATH, json={"referrer_host": "https://x.example/a?b=1"}
        )

    assert response.status_code == 200
    assert store.clicks[0]["referrer_host"] is None


@pytest.mark.parametrize(
    "body",
    [
        {"ip": "203.0.113.7"},
        {"user_agent": "Mozilla/5.0"},
        {"cookie_id": "abc"},
        {"user_agent_class": "TOASTER"},
        {"referrer_host": "a" * 300},
    ],
)
async def test_other_body_fields_are_rejected_and_nothing_is_written(
    body: dict[str, Any],
) -> None:
    http, store, committer, _ = build(make_offer())

    async with http:
        response = await http.post(PATH, json=body)

    assert response.status_code == 422
    assert response.json() == {"success": False, "error": "Invalid request"}
    assert store.clicks == [] and committer.commits == 0


@pytest.mark.parametrize("offer_id", ["a b", "a" * 65, "id.with.dots", "id;x"])
async def test_a_malformed_offer_id_is_a_422_and_the_database_is_not_asked(
    offer_id: str,
) -> None:
    http, store, _, _ = build(make_offer())

    async with http:
        response = await http.post(f"/v1/catalogue/offers/{offer_id}/click")

    assert response.status_code == 422
    assert store.asked == [] and store.clicks == []


async def test_unknown_and_unpublished_offers_get_one_identical_uncached_404() -> None:
    unknown, _, _, _ = build(None)
    draft, store, committer, _ = build(
        make_offer(status=OfferStatus.DRAFT, last_verified_at=None)
    )

    async with unknown, draft:
        first = await unknown.post(PATH)
        second = await draft.post(PATH)

    assert first.status_code == second.status_code == 404
    assert (
        first.json() == second.json() == {"success": False, "error": "Offer not found"}
    )
    assert (
        first.headers["Cache-Control"] == second.headers["Cache-Control"] == "no-store"
    )
    assert store.clicks == [] and committer.commits == 0


async def test_an_ended_offer_answers_410_with_the_catalogue_stub_and_no_click() -> (
    None
):
    http, store, committer, _ = build(make_offer(end_at=NOW - timedelta(hours=1)))

    async with http:
        response = await http.post(PATH)

    body = response.json()
    assert response.status_code == 410
    assert body["success"] is False and body["expired"] is True
    assert body["data"]["slug"] == "free-shampoo"
    assert "destination_url" not in response.text
    assert response.headers["Cache-Control"] == "no-store"
    assert store.clicks == [] and committer.commits == 0


async def test_a_live_offer_without_a_usable_link_is_the_same_404() -> None:
    link = make_link(destination_url="https://evil.example/")
    http, store, _, _ = build(make_offer(), [link])

    async with http:
        response = await http.post(PATH)

    assert response.status_code == 404
    assert response.json() == {"success": False, "error": "Offer not found"}
    assert store.clicks == []


async def test_a_missing_or_wrong_internal_key_is_401_and_nothing_is_read() -> None:
    http, store, _, _ = build(make_offer())

    async with http:
        wrong = await http.post(PATH, headers={"X-Internal-Key": "nope"})
        missing = await http.post(PATH, headers={"X-Internal-Key": ""})

    assert wrong.status_code == missing.status_code == 401
    assert store.asked == [] and store.clicks == []


async def test_rate_limit_is_keyed_by_the_forwarded_client_ip_and_by_the_offer() -> (
    None
):
    http, _, _, limiter = build(make_offer())

    async with http:
        await http.post(PATH, headers={"X-Client-IP": "203.0.113.7"})

    assert isinstance(limiter, FakeRateLimiter)
    assert limiter.hits == [
        ("outbound_click:ip:203.0.113.7", OUTBOUND_CLICK_POLICY),
        ("outbound_offer:offer:offer_1", OUTBOUND_OFFER_POLICY),
    ]


async def test_past_the_offer_cap_the_visitor_still_gets_the_destination_but_no_click() -> (
    None
):
    http, store, committer, _ = build(make_offer())
    limit = OUTBOUND_OFFER_POLICY.limit

    async with http:
        responses = [
            await http.post(PATH, headers={"X-Client-IP": f"10.{n // 250}.{n % 250}.1"})
            for n in range(limit + 5)
        ]

    assert [r.status_code for r in responses] == [200] * (limit + 5)
    assert all(r.json()["data"]["destination_url"] == DESTINATION for r in responses)
    assert all("Retry-After" not in r.headers for r in responses)
    assert len(store.clicks) == limit
    assert committer.commits == limit


async def test_the_per_client_limit_still_refuses_with_429() -> None:
    http, store, _, _ = build(make_offer())

    async with http:
        responses = [
            await http.post(PATH, headers={"X-Client-IP": "203.0.113.9"})
            for _ in range(OUTBOUND_CLICK_POLICY.limit + 1)
        ]

    assert responses[-1].status_code == 429
    assert "Retry-After" in responses[-1].headers
    assert len(store.clicks) == OUTBOUND_CLICK_POLICY.limit


async def test_a_capped_offer_still_refuses_what_it_would_refuse_anyway() -> None:
    bad = make_link(destination_url="https://evil.example/")
    http, store, _, _ = build(make_offer(), [bad])
    limit = OUTBOUND_OFFER_POLICY.limit

    async with http:
        for n in range(limit):
            await http.post(PATH, headers={"X-Client-IP": f"10.{n // 250}.{n % 250}.1"})
        over = await http.post(PATH, headers={"X-Client-IP": "10.9.9.9"})

    assert over.status_code == 404
    assert store.clicks == []


async def test_an_offer_cap_outage_records_the_click_as_usual() -> None:
    class OfferKeyOutage(FakeRateLimiter):
        async def hit(self, identifier, policy):  # type: ignore[no-untyped-def]
            if identifier.startswith("outbound_offer:"):
                raise RuntimeError("redis down")
            return await super().hit(identifier, policy)

    http, store, _, _ = build(make_offer(), limiter=OfferKeyOutage())

    async with http:
        response = await http.post(PATH)

    assert response.status_code == 200
    assert len(store.clicks) == 1


async def test_the_offer_cap_is_per_offer() -> None:
    http, _, _, limiter = build(make_offer())

    async with http:
        await http.post("/v1/catalogue/offers/other_offer/click")

    assert isinstance(limiter, FakeRateLimiter)
    assert limiter.hits[1][0] == "outbound_offer:offer:other_offer"


async def test_requests_over_the_limit_get_429_and_write_no_click() -> None:
    http, store, _, _ = build(make_offer())

    async with http:
        responses = [
            await http.post(PATH) for _ in range(OUTBOUND_CLICK_POLICY.limit + 1)
        ]

    assert responses[-1].status_code == 429
    assert "Retry-After" in responses[-1].headers
    assert len(store.clicks) == OUTBOUND_CLICK_POLICY.limit


async def test_a_rate_limiter_outage_fails_open() -> None:
    http, store, _, _ = build(make_offer(), limiter=RaisingRateLimiter())

    async with http:
        response = await http.post(PATH)

    assert response.status_code == 200
    assert len(store.clicks) == 1


async def test_a_database_error_is_a_500_envelope_with_no_destination() -> None:
    http, store, _, _ = build(make_offer(), error=RuntimeError("database is down"))

    async with http:
        response = await http.post(PATH)

    assert response.status_code == 500
    assert response.json() == {"success": False, "error": "Internal server error"}
    assert store.clicks == []


# --- the disclosure guard (FREEJ-60) -----------------------------------------------


async def test_an_undisclosed_click_on_a_commission_link_is_a_coded_409_with_the_slug_only() -> (
    None
):
    http, store, committer, _ = build(make_offer())

    async with http:
        response = await http.post(PATH, json={"commission_disclosed": False})

    assert response.status_code == 409
    assert response.json() == {
        "success": False,
        "error": "Disclosure required",
        "code": "disclosure_required",
        "data": {"slug": "free-shampoo"},
    }
    assert response.headers["Cache-Control"] == "no-store"
    assert "set-cookie" not in response.headers
    assert "awin" not in response.text and "destination" not in response.text
    assert store.clicks == [] and committer.commits == 0


async def test_an_undisclosed_click_on_a_non_commission_link_redirects_as_usual() -> (
    None
):
    link = make_link(
        provider="direct", destination_url="https://www.acme.example/free-sample"
    )
    http, store, _, _ = build(make_offer(), [link])

    async with http:
        response = await http.post(PATH, json={"commission_disclosed": False})

    assert response.status_code == 200
    assert response.json()["data"]["destination_url"].startswith("https://www.acme")
    assert len(store.clicks) == 1


@pytest.mark.parametrize("body", [None, {}, {"commission_disclosed": True}])
async def test_a_disclosed_or_absent_flag_is_the_ordinary_click(
    body: dict[str, Any] | None,
) -> None:
    http, store, _, _ = build(make_offer())

    async with http:
        response = await http.post(PATH, json=body)

    assert response.status_code == 200
    assert len(store.clicks) == 1


async def test_a_non_boolean_flag_is_a_422_and_nothing_is_written() -> None:
    http, store, _, _ = build(make_offer())

    async with http:
        response = await http.post(PATH, json={"commission_disclosed": "maybe"})

    assert response.status_code == 422
    assert store.clicks == []
