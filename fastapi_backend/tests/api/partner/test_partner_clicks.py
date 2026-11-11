"""``GET /partner/v1/clicks`` without a database (ADR-013, FREEJ-59): the contract, the range rules, tenancy and limits.

The click store is a fake that filters by the merchant it is given, behind the real authenticator, surface and
routes. The Postgres proofs (the same counts as the admin report, the restricted role) are in
``test_partner_clicks_e2e.py``.
"""

import json
import logging
from datetime import date

import pytest

from app.core.config import settings
from app.core.rate_limit import PARTNER_CLICKS_POLICY, PARTNER_READ_POLICY
from app.main import app
from app.partner.clicks_report import CLICK_DEFINITION, MAX_OFFERS
from app.partner.clicks_routes import INVALID_RANGE_CODE, RANGE_MESSAGE
from tests.api.partner.support import RawResponse, call_raw
from tests.fakes_partner import KEY_ID, MERCHANT_ID
from tests.partner_world import PartnerWorld

CLICKS = "/partner/v1/clicks"
MARKER = "zz-echo-marker-41"
OTHER_MERCHANT = "merchant000000000000000b2"


@pytest.fixture(autouse=True)
def surface_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "PARTNER_API_ENABLED", True)


async def get_clicks(
    world: PartnerWorld, query: str, extra: list[tuple[str, str]] | None = None
) -> RawResponse:
    return await call_raw(
        app,
        "GET",
        CLICKS,
        headers=[*world.headers(), *(extra or [])],
        query_string=query,
    )


async def test_it_returns_the_days_of_each_offer_busiest_first(
    world: PartnerWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "PUBLIC_SITE_URL", "https://freejoy.test/")
    world.clicks.add(MERCHANT_ID, "quiet", {date(2026, 3, 2): 1})
    world.clicks.add(
        MERCHANT_ID, "busy", {date(2026, 3, 1): 4, date(2026, 3, 3): 2}, title="Busy"
    )

    response = await get_clicks(world, "from=2026-03-01&to=2026-03-31")

    assert response.status == 200
    data = response.json()["data"]
    assert [o["slug"] for o in data["offers"]] == ["busy", "quiet"]
    assert data["offers"][0] == {
        "title": "Busy",
        "slug": "busy",
        "url": "https://freejoy.test/freebies/busy",
        "total_clicks": 6,
        "days": [
            {"date": "2026-03-01", "clicks": 4},
            {"date": "2026-03-03", "clicks": 2},
        ],
    }
    assert data["from_date"] == "2026-03-01"
    assert data["to_date"] == "2026-03-31"
    assert data["offers_truncated"] is False


async def test_the_response_has_exactly_these_fields_and_no_internal_id(
    world: PartnerWorld,
) -> None:
    world.clicks.add(MERCHANT_ID, "soap", {date(2026, 3, 1): 1})

    response = await get_clicks(world, "from=2026-03-01&to=2026-03-02")

    body = response.json()
    assert set(body) == {"success", "data"}
    assert set(body["data"]) == {
        "from_date",
        "to_date",
        "offers",
        "offers_truncated",
        "retention",
        "definition",
    }
    assert set(body["data"]["offers"][0]) == {
        "title",
        "slug",
        "url",
        "total_clicks",
        "days",
    }
    assert set(body["data"]["retention"]) == {
        "days",
        "earliest_complete_date",
        "starts_before_retention",
    }
    text = response.body.decode()
    assert MERCHANT_ID not in text
    assert "internal-soap" not in text
    for word in ("bot", "referrer", "merchant_id", "offers_total", "user_agent"):
        assert word not in text.lower().replace(CLICK_DEFINITION.lower(), "")


async def test_the_definition_is_the_one_fixed_text(world: PartnerWorld) -> None:
    response = await get_clicks(world, "from=2026-03-01&to=2026-03-02")

    assert response.json()["data"]["definition"] == CLICK_DEFINITION


def test_the_definition_says_only_what_the_code_does() -> None:
    from app.core.click_retention import CLICK_RETENTION_DAYS

    assert "not a sale" in CLICK_DEFINITION
    assert "clicks twice counts twice" in CLICK_DEFINITION
    assert f"{CLICK_RETENTION_DAYS} days" in CLICK_DEFINITION
    assert "UTC" in CLICK_DEFINITION
    # Recording is rate limited, and the public text says so without a figure or a description of the limiter.
    assert "rate limited for each offer" in CLICK_DEFINITION
    assert "some clicks are not recorded" in CLICK_DEFINITION
    assert (
        "in a very busy minute, or when many clicks on one offer come from one network "
        "in a minute, some clicks are not recorded and the count can be low."
    ) in CLICK_DEFINITION
    # Automated traffic that does not identify itself is counted, and the text says the count can be too high (FREEJ-63).
    assert (
        "Counts can also be overstated by automated traffic that does not identify "
        "itself as automated." in CLICK_DEFINITION
    )
    assert "—" not in CLICK_DEFINITION and "bucket" not in CLICK_DEFINITION
    assert not any(
        ch.isdigit() for ch in CLICK_DEFINITION.replace(str(CLICK_RETENTION_DAYS), "")
    )
    assert "still sent on" not in CLICK_DEFINITION


async def test_the_merchant_is_the_keys_and_never_the_request(
    world: PartnerWorld,
) -> None:
    world.clicks.add(MERCHANT_ID, "mine", {date(2026, 3, 1): 1})
    world.clicks.add(OTHER_MERCHANT, "theirs", {date(2026, 3, 1): 9})

    response = await get_clicks(
        world,
        f"from=2026-03-01&to=2026-03-02&merchant_id={OTHER_MERCHANT}&offer_id=internal-theirs",
        extra=[("x-merchant-id", OTHER_MERCHANT)],
    )

    assert response.status == 200
    assert [o["slug"] for o in response.json()["data"]["offers"]] == ["mine"]
    assert {call[1] for call in world.clicks.calls} == {MERCHANT_ID}
    assert OTHER_MERCHANT not in response.body.decode()


async def test_two_keys_of_two_merchants_each_see_only_their_own_counts(
    world: PartnerWorld,
) -> None:
    second = world.store.add(
        key_id="secondkey0000000000000a1", merchant_id=OTHER_MERCHANT
    )
    world.clicks.add(MERCHANT_ID, "a-offer", {date(2026, 3, 1): 3})
    world.clicks.add(OTHER_MERCHANT, "b-offer", {date(2026, 3, 1): 7})

    first = await get_clicks(world, "from=2026-03-01&to=2026-03-02")
    other = await call_raw(
        app,
        "GET",
        CLICKS,
        headers=world.headers(second),
        query_string="from=2026-03-01&to=2026-03-02",
    )

    assert [o["slug"] for o in first.json()["data"]["offers"]] == ["a-offer"]
    assert [o["slug"] for o in other.json()["data"]["offers"]] == ["b-offer"]
    assert "b-offer" not in first.body.decode()
    assert "a-offer" not in other.body.decode()


async def test_at_most_a_hundred_offers_are_read_and_the_cut_is_reported(
    world: PartnerWorld,
) -> None:
    for number in range(MAX_OFFERS + 1):
        world.clicks.add(MERCHANT_ID, f"offer-{number:03d}", {date(2026, 3, 1): number})

    response = await get_clicks(world, "from=2026-03-01&to=2026-03-02")

    data = response.json()["data"]
    assert len(data["offers"]) == MAX_OFFERS == 100
    assert data["offers_truncated"] is True
    assert data["offers"][0]["slug"] == "offer-100"  # busiest first
    assert ("offers", MERCHANT_ID) == world.clicks.calls[0][:2]
    assert world.clicks.calls[0][4] == MAX_OFFERS


READ_HIT = (f"partner_read:key:{KEY_ID}", PARTNER_READ_POLICY)
CLICKS_HIT = (f"partner_clicks:key:{KEY_ID}", PARTNER_CLICKS_POLICY)


async def test_a_read_is_counted_once_under_the_read_policy_and_once_under_the_clicks_policy(
    world: PartnerWorld,
) -> None:
    response = await get_clicks(world, "from=2026-03-01&to=2026-03-02")

    assert world.limiter.hits == [READ_HIT, CLICKS_HIT]
    assert "x-ratelimit-limit" not in response.header_names()


async def test_a_refused_range_is_counted_under_both_policies_too(
    world: PartnerWorld,
) -> None:
    await get_clicks(world, "from=2026-03-02&to=2026-03-01")

    assert world.limiter.hits == [READ_HIT, CLICKS_HIT]


def test_the_clicks_policy_is_twenty_a_minute_per_key_and_fails_closed() -> None:
    assert PARTNER_CLICKS_POLICY.scope == "partner_clicks"
    assert (PARTNER_CLICKS_POLICY.limit, PARTNER_CLICKS_POLICY.window_seconds) == (
        20,
        60,
    )
    assert PARTNER_CLICKS_POLICY.fail_closed is True


async def test_the_twenty_first_read_in_a_minute_is_a_429_and_reads_nothing(
    world: PartnerWorld,
) -> None:
    world.clicks.add(MERCHANT_ID, "soap", {date(2026, 3, 1): 1})
    for _ in range(PARTNER_CLICKS_POLICY.limit):
        assert (await get_clicks(world, "from=2026-03-01&to=2026-03-02")).status == 200
    reads_before = len(world.clicks.calls)

    response = await get_clicks(world, "from=2026-03-01&to=2026-03-02")

    assert response.status == 429
    assert response.json() == {"success": False, "error": "Too many requests"}
    assert response.header("retry-after") is not None
    assert response.header("cache-control") == "no-store, private"
    assert len(world.clicks.calls) == reads_before


async def test_the_clicks_budget_is_per_key_and_does_not_touch_me(
    world: PartnerWorld,
) -> None:
    second = world.store.add(
        key_id="secondkey0000000000000a1", merchant_id=OTHER_MERCHANT
    )
    for _ in range(PARTNER_CLICKS_POLICY.limit + 1):
        await get_clicks(world, "from=2026-03-01&to=2026-03-02")

    other = await call_raw(
        app,
        "GET",
        CLICKS,
        headers=world.headers(second),
        query_string="from=2026-03-01&to=2026-03-02",
    )
    me = await call_raw(app, "GET", "/partner/v1/me", headers=world.headers())

    assert other.status == 200
    assert me.status == 200


async def test_with_the_limiter_down_the_clicks_route_is_a_fixed_503_and_reads_nothing(
    world: PartnerWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core import rate_limiter_provider
    from tests.fakes import RaisingRateLimiter

    monkeypatch.setattr(rate_limiter_provider, "_rate_limiter", RaisingRateLimiter())

    response = await get_clicks(world, "from=2026-03-01&to=2026-03-02")

    assert response.status == 503
    assert response.json() == {
        "success": False,
        "error": "Service temporarily unavailable",
    }
    assert response.header("retry-after") is not None
    assert world.clicks.calls == []


async def test_success_and_a_refused_range_are_no_store(world: PartnerWorld) -> None:
    good = await get_clicks(world, "from=2026-03-01&to=2026-03-02")
    bad = await get_clicks(world, "from=2026-03-02&to=2026-03-01")

    for response in (good, bad):
        assert response.header("cache-control") == "no-store, private"
        assert response.header("x-freejoy-surface") == "partner"
        assert response.header("set-cookie") is None


LONGEST_OK = "from=2026-01-01&to=2026-04-02"  # 31 + 28 + 31 + 2 = 92 days, both ends
TOO_LONG = "from=2026-01-01&to=2026-04-03"  # 93 days


async def test_the_longest_range_is_ninety_two_days_counting_both_ends(
    world: PartnerWorld,
) -> None:
    assert (await get_clicks(world, LONGEST_OK)).status == 200
    assert (await get_clicks(world, TOO_LONG)).status == 422


async def test_one_day_and_the_bounds_of_the_calendar_are_accepted(
    world: PartnerWorld,
) -> None:
    for query in (
        "from=2026-03-01&to=2026-03-01",
        "from=2000-01-01&to=2000-01-02",
        "from=2999-12-30&to=2999-12-31",
    ):
        assert (await get_clicks(world, query)).status == 200, query


@pytest.mark.parametrize(
    "query",
    [
        "from=2026-03-02&to=2026-03-01",  # reversed
        TOO_LONG,
        "from=1999-12-31&to=2000-01-01",  # before 2000
        "from=2999-12-31&to=3000-01-01",  # after 2999
        "from=0001-01-01&to=0001-01-02",
        "from=2026-3-1&to=2026-03-02",  # not zero padded
        "from=20260301&to=20260302",
        "from=2026-03-01T00:00:00Z&to=2026-03-02",
        "from=1772323200&to=1772409600",  # Unix times
        "from=2026-02-30&to=2026-03-02",  # not a real day
        "from=2026-03-01",  # missing to
        "to=2026-03-01",  # missing from
        "",
        "from=&to=",
        f"from={MARKER}&to={MARKER}",
        f"from=2026-03-01&to={MARKER}",
        "from=2026-03-01&from=2026-03-02&to=",
        "from=%E2%82%AC&to=2026-03-02",
    ],
)
async def test_a_refused_range_is_one_fixed_422_with_a_stable_code(
    world: PartnerWorld, query: str
) -> None:
    response = await get_clicks(world, query)

    assert response.status == 422
    assert response.json() == {
        "success": False,
        "error": RANGE_MESSAGE,
        "code": INVALID_RANGE_CODE,
    }
    assert MARKER not in response.body.decode()
    assert world.clicks.calls == []  # nothing was read


async def test_a_refused_range_does_not_echo_the_dates_it_was_given(
    world: PartnerWorld,
) -> None:
    response = await get_clicks(world, "from=2026-03-02&to=2026-03-01")

    assert "2026-03-02" not in response.body.decode()
    assert "2026-03-01" not in response.body.decode()


async def test_the_range_text_states_the_rule_it_enforces() -> None:
    assert "YYYY-MM-DD" in RANGE_MESSAGE
    assert "92" in RANGE_MESSAGE
    assert "2000" in RANGE_MESSAGE and "2999" in RANGE_MESSAGE


async def test_it_logs_one_info_line_with_the_key_id_the_range_and_the_count(
    world: PartnerWorld, caplog: pytest.LogCaptureFixture
) -> None:
    world.clicks.add(MERCHANT_ID, "soap", {date(2026, 3, 1): 1})
    caplog.set_level(logging.INFO, logger="yourapp.partner")

    await get_clicks(world, "from=2026-03-01&to=2026-03-05")

    lines = [r.getMessage() for r in caplog.records if "clicks read" in r.getMessage()]
    assert lines == [
        f"partner clicks read: key_id={KEY_ID} from=2026-03-01 to=2026-03-05 offers=1"
    ]
    assert world.key.value not in caplog.text
    assert world.key.digest not in caplog.text


async def test_without_a_key_the_route_is_a_401_before_anything_is_read(
    world: PartnerWorld,
) -> None:
    response = await call_raw(
        app, "GET", CLICKS, query_string="from=2026-03-01&to=2026-03-02"
    )

    assert response.status == 401
    assert world.clicks.calls == []


async def test_other_methods_are_not_allowed(world: PartnerWorld) -> None:
    response = await call_raw(app, "POST", CLICKS, headers=world.headers())

    assert response.status == 405
    assert json.loads(response.body)["error"] == "Method Not Allowed"


@pytest.mark.parametrize(
    "query",
    [
        "from=2026-03-01&from=2026-03-01&to=2026-03-02",
        "from=2026-03-01&to=2026-03-02&to=2026-03-02",
        "from=2026-03-01&from=2026-03-02&to=2026-03-02",
        "from=2026-03-01&to=2026-03-02&to=2026-03-03",
    ],
)
async def test_a_repeated_from_or_to_is_refused_not_resolved_to_the_last(
    world: PartnerWorld, query: str
) -> None:
    response = await get_clicks(world, query)

    assert response.status == 422
    assert response.json()["code"] == INVALID_RANGE_CODE
    assert world.clicks.calls == []


async def test_a_database_that_cannot_answer_is_the_fixed_503_with_retry_after(
    world: PartnerWorld, caplog: pytest.LogCaptureFixture
) -> None:
    from sqlalchemy.exc import DBAPIError

    secret_sql = (
        f"SELECT offers.id FROM offers WHERE offers.merchant_id = '{MERCHANT_ID}'"
    )
    world.clicks.error = DBAPIError(
        secret_sql, {"merchant_id": MERCHANT_ID}, Exception("canceling statement")
    )
    caplog.set_level(logging.INFO)

    response = await get_clicks(world, "from=2026-03-01&to=2026-03-02")

    assert response.status == 503
    assert response.json() == {
        "success": False,
        "error": "Service temporarily unavailable",
    }
    assert response.header("retry-after") is not None
    assert response.header("cache-control") == "no-store, private"
    lines = [r for r in caplog.records if "database unavailable" in r.getMessage()]
    assert [r.getMessage() for r in lines] == [
        f"partner database unavailable: key_id={KEY_ID} error=DBAPIError"
    ]
    assert all(r.exc_info is None for r in lines)
    assert MERCHANT_ID not in caplog.text
    assert "SELECT" not in caplog.text
    assert "canceling statement" not in caplog.text


@pytest.mark.parametrize(
    "error",
    [ConnectionRefusedError("refused"), TimeoutError("slow"), OSError("reset")],
)
async def test_an_operating_system_error_on_the_connection_is_the_same_503(
    world: PartnerWorld, error: Exception
) -> None:
    world.clicks.error = error

    response = await get_clicks(world, "from=2026-03-01&to=2026-03-02")

    assert response.status == 503
    assert response.json()["error"] == "Service temporarily unavailable"


async def test_the_partner_session_providers_own_refusals_are_the_same_503(
    world: PartnerWorld,
) -> None:
    from app.core.database import PartnerDatabaseUnavailable, PartnerRoleNotRestricted

    for error in (
        PartnerDatabaseUnavailable("unset"),
        PartnerRoleNotRestricted("too much"),
    ):
        world.clicks.error = error

        response = await get_clicks(world, "from=2026-03-01&to=2026-03-02")

        assert response.status == 503


async def test_a_bug_that_is_not_a_database_failure_is_still_a_500(
    world: PartnerWorld,
) -> None:
    world.clicks.error = ValueError("a bug")

    response = await get_clicks(world, "from=2026-03-01&to=2026-03-02")

    assert response.status == 500
    assert response.json() == {"success": False, "error": "Internal server error"}


async def test_another_partner_route_with_from_and_to_parameters_keeps_the_shared_422(
    world: PartnerWorld,
) -> None:
    from typing import Annotated

    from fastapi import Query

    from app.partner.app import create_partner_api, create_partner_authenticator
    from app.partner.surface import PartnerSurface

    api = create_partner_api()

    @api.get("/other")
    async def other(
        first: Annotated[int, Query(alias="from")],
        last: Annotated[int, Query(alias="to")],
    ) -> dict[str, int]:  # pragma: no cover - never reached
        return {"x": first + last}

    surface = PartnerSurface(
        api, enabled=lambda: True, authenticator=create_partner_authenticator()
    )

    response = await call_raw(
        surface, "GET", "/other", headers=world.headers(), query_string="from=x&to=y"
    )

    assert response.status == 422
    assert response.json() == {"success": False, "error": "Invalid request"}


async def test_a_body_error_on_another_partner_route_keeps_the_shared_text(
    world: PartnerWorld,
) -> None:
    from pydantic import BaseModel

    from app.partner.app import create_partner_api, create_partner_authenticator
    from app.partner.surface import PartnerSurface

    class Item(BaseModel):
        count: int

    api = create_partner_api()

    @api.post("/things")
    async def create(item: Item) -> None:  # pragma: no cover - never reached
        return None

    surface = PartnerSurface(
        api, enabled=lambda: True, authenticator=create_partner_authenticator()
    )

    body = await call_raw(
        surface,
        "POST",
        "/things",
        headers=[*world.headers(), ("content-type", "application/json")],
        chunks=[b'{"count": "x"}'],
    )

    assert body.status == 422
    assert body.json() == {"success": False, "error": "Invalid request"}
