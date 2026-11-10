"""``PromotionFilters.membership`` and the envelope facts ``PromotionsPage`` carries (FREEJ-52)."""

import json
from datetime import date
from typing import Any, get_args

import httpx
import pytest
from pydantic import SecretStr

from app.core.awin_client import (
    BASE_URL,
    HttpxAwinClient,
    Membership,
    PromotionFilters,
    PromotionsPage,
    parse_promotions,
)
from tests.awin_samples import PUBLISHER_ID, raw_item

TOKEN = "fake-awin-token-0123456789abcdef"


def test_the_documented_membership_values_are_the_only_ones() -> None:
    assert set(get_args(Membership)) == {"joined", "notJoined", "all"}


@pytest.mark.parametrize("value", ["joined", "notJoined", "all"])
def test_the_filter_json_carries_each_membership(value: Any) -> None:
    filters = PromotionFilters(membership=value)

    assert filters.to_json() == {
        "membership": value,
        "type": "all",
        "status": "active",
        "regionCodes": ["GB"],
    }


def test_the_default_membership_is_joined_and_the_json_is_unchanged() -> None:
    filters = PromotionFilters(updated_since=date(2026, 11, 1))

    assert filters.membership == "joined"
    assert list(filters.to_json().items()) == [
        ("membership", "joined"),
        ("type", "all"),
        ("status", "active"),
        ("regionCodes", ["GB"]),
        ("updatedSince", "2026-11-01"),
    ]
    assert PromotionFilters(date(2026, 11, 1)).updated_since == date(2026, 11, 1)


async def test_the_default_request_body_is_byte_for_byte_the_old_one() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[])

    client = HttpxAwinClient(
        PUBLISHER_ID, SecretStr(TOKEN), transport=httpx.MockTransport(handler)
    )
    await client.fetch_promotions(PromotionFilters(), page=1, page_size=20)
    await client.aclose()

    legacy = {
        "filters": {
            "membership": "joined",
            "type": "all",
            "status": "active",
            "regionCodes": ["GB"],
        },
        "pagination": {"page": 1, "pageSize": 20},
    }
    expected = httpx.Request("POST", f"{BASE_URL}/x", json=legacy).content
    assert seen[0].content == expected


async def test_a_non_default_membership_reaches_the_request_body() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[])

    client = HttpxAwinClient(
        PUBLISHER_ID, SecretStr(TOKEN), transport=httpx.MockTransport(handler)
    )
    await client.fetch_promotions(
        PromotionFilters(membership="all"), page=1, page_size=20
    )
    await client.aclose()

    assert json.loads(seen[0].content)["filters"]["membership"] == "all"


def test_a_bare_list_is_recorded_as_a_list_envelope() -> None:
    page = parse_promotions([raw_item(1)])

    assert page is not None
    assert page.envelope == "list" and page.total_pages_present is False


def test_an_object_envelope_records_whether_total_pages_was_present() -> None:
    with_total = parse_promotions({"data": [], "pagination": {"totalPages": 3}})
    without = parse_promotions({"data": [], "pagination": {"page": 1}})
    bad_type = parse_promotions({"data": [], "pagination": {"totalPages": "3"}})
    no_pagination = parse_promotions({"data": []})

    assert with_total == PromotionsPage((), 3, "object", True)
    assert without == PromotionsPage((), None, "object", False)
    # present but unusable: present is True, the usable total stays None
    assert bad_type == PromotionsPage((), None, "object", True)
    assert no_pagination == PromotionsPage((), None, "object", False)
