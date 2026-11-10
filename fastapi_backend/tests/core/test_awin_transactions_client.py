"""``HttpxAwinClient.fetch_transactions`` over ``httpx.MockTransport``: the request shape and the failures.

The retry, throttle, size cap and TLS behaviour are the promotions call's (tested in
``test_awin_client.py``); these tests pin what is specific to the transactions endpoint and that the
shared machinery is really shared.
"""

import json
from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
from pydantic import SecretStr

from app.core.awin_client import (
    BASE_URL,
    MAX_ATTEMPTS,
    TRANSACTIONS_MAX_RANGE,
    AwinError,
    AwinReason,
    HttpxAwinClient,
    TransactionsPage,
)
from tests.core.test_awin_client import PUBLISHER_ID, TOKEN, Recorder, ok
from tests.fakes_awin import FakeTime

START = datetime(2026, 11, 1, 0, 0, 0, tzinfo=UTC)
END = datetime(2026, 11, 1, 23, 59, 59, tzinfo=UTC)
ROW = {"id": 1}


class Limiter:
    def __init__(self) -> None:
        self.acquired = 0

    async def acquire(self) -> None:
        self.acquired += 1


def build(recorder: Recorder) -> tuple[HttpxAwinClient, Limiter, FakeTime]:
    fake, limiter = FakeTime(), Limiter()
    client = HttpxAwinClient(
        PUBLISHER_ID,
        SecretStr(TOKEN),
        transport=httpx.MockTransport(recorder),
        limiter=limiter,
        sleep=fake.sleep,
    )
    return client, limiter, fake


async def fetch(client: HttpxAwinClient, **kwargs: Any) -> TransactionsPage:
    return await client.fetch_transactions(
        kwargs.pop("start", START),
        kwargs.pop("end", END),
        page=kwargs.pop("page", 1),
        page_size=kwargs.pop("page_size", 200),
    )


async def test_the_request_is_one_get_to_the_fixed_host_with_the_documented_parameters() -> (
    None
):
    recorder = Recorder(ok([ROW]))
    client, _, _ = build(recorder)

    await fetch(client, page=3, page_size=150)

    request = recorder.requests[0]
    assert request.method == "GET"
    assert request.url.scheme == "https" and request.url.host == "api.awin.com"
    assert request.url.path == f"/publishers/{PUBLISHER_ID}/transactions/"
    assert str(request.url).startswith(f"{BASE_URL}/publishers/")
    assert {k: v[0] for k, v in parse_qs(request.url.query.decode()).items()} == {
        "startDate": "2026-11-01T00:00:00",
        "endDate": "2026-11-01T23:59:59",
        "dateType": "transaction",
        "timezone": "UTC",
        "page": "3",
        "pageSize": "150",
    }
    assert request.content == b""


async def test_the_token_travels_only_in_the_authorization_header() -> None:
    recorder = Recorder(ok([ROW]))
    client, _, _ = build(recorder)

    await fetch(client)

    request = recorder.requests[0]
    assert request.headers["Authorization"] == f"Bearer {TOKEN}"
    assert TOKEN not in str(request.url)
    assert "accessToken" not in str(request.url)
    assert TOKEN not in repr(client)


async def test_a_zone_aware_instant_is_sent_as_utc() -> None:
    recorder = Recorder(ok([]))
    client, _, _ = build(recorder)
    plus_one = timezone(timedelta(hours=1))

    await fetch(
        client,
        start=datetime(2026, 11, 1, 1, 0, 0, tzinfo=plus_one),
        end=datetime(2026, 11, 2, 0, 59, 59, tzinfo=plus_one),
    )

    query = parse_qs(recorder.requests[0].url.query.decode())
    assert query["startDate"] == ["2026-11-01T00:00:00"]
    assert query["endDate"] == ["2026-11-01T23:59:59"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"start": datetime(2026, 11, 1), "end": END},  # naive
        {"start": START, "end": datetime(2026, 11, 2)},  # naive
        {"start": END, "end": START},  # end before start
        {"end": START + TRANSACTIONS_MAX_RANGE + timedelta(seconds=1)},  # > 31 days
        {"page": 0},
        {"page_size": 9},
        {"page_size": 201},
    ],
)
async def test_arguments_outside_the_documented_limits_are_refused_before_any_request(
    kwargs: dict[str, Any],
) -> None:
    recorder = Recorder(ok([]))
    client, limiter, _ = build(recorder)

    with pytest.raises(ValueError):
        await fetch(client, **kwargs)

    assert recorder.requests == [] and limiter.acquired == 0


async def test_a_bare_list_is_a_page() -> None:
    client, _, _ = build(Recorder(ok([ROW, ROW])))

    page = await fetch(client)

    assert page == TransactionsPage((ROW, ROW), None)


async def test_an_object_with_data_and_a_total_is_a_page() -> None:
    payload = {"data": [ROW], "pagination": {"totalPages": 4}}
    client, _, _ = build(Recorder(ok(payload)))

    assert await fetch(client) == TransactionsPage((ROW,), 4)


@pytest.mark.parametrize("payload", [{"rows": []}, {"data": "x"}, "text", 5])
async def test_any_other_envelope_is_unrecognised(payload: Any) -> None:
    client, _, _ = build(Recorder(ok(payload)))

    with pytest.raises(AwinError) as caught:
        await fetch(client)

    assert caught.value.reason is AwinReason.UNRECOGNISED_RESPONSE


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (401, AwinReason.AUTH_FAILED),
        (403, AwinReason.AUTH_FAILED),
        (400, AwinReason.CLIENT_ERROR),
        (302, AwinReason.REDIRECTED),
    ],
)
async def test_a_failure_status_is_one_attempt_and_a_reason_code(
    status: int, reason: AwinReason
) -> None:
    recorder = Recorder(httpx.Response(status, text=f"secret body {TOKEN}"))
    client, limiter, _ = build(recorder)

    with pytest.raises(AwinError) as caught:
        await fetch(client)

    assert caught.value.reason is reason
    assert str(caught.value) == reason.value and TOKEN not in str(caught.value)
    assert len(recorder.requests) == 1 and limiter.acquired == 1


async def test_a_server_error_is_retried_through_the_limiter_then_reported() -> None:
    recorder = Recorder(httpx.Response(503))
    client, limiter, fake = build(recorder)

    with pytest.raises(AwinError) as caught:
        await fetch(client)

    assert caught.value.reason is AwinReason.SERVER_ERROR
    assert len(recorder.requests) == MAX_ATTEMPTS == limiter.acquired
    assert fake.sleeps == [1.0, 2.0, 4.0]


async def test_a_body_that_is_not_json_is_invalid_json() -> None:
    client, _, _ = build(Recorder(httpx.Response(200, content=b"<html>")))

    with pytest.raises(AwinError) as caught:
        await fetch(client)

    assert caught.value.reason is AwinReason.INVALID_JSON


async def test_promotions_are_unchanged_by_the_shared_machinery() -> None:
    recorder = Recorder(ok([{"promotionId": 1}]))
    client, _, _ = build(recorder)

    from app.core.awin_client import PromotionFilters

    page = await client.fetch_promotions(PromotionFilters(), page=1, page_size=10)

    assert page.items == ({"promotionId": 1},)
    assert recorder.requests[0].method == "POST"
    assert json.loads(recorder.requests[0].content)["pagination"] == {
        "page": 1,
        "pageSize": 10,
    }
