"""The tracking link is ours, the advertiser's, and goes where the item says (``awin_tracking``)."""

from typing import Any

import pytest

from app.services.awin_promotions import (
    Promotion,
    Rejected,
    SkipReason,
    parse_promotion,
)
from tests.awin_samples import ADVERTISER_ID, NOW, PUBLISHER_ID, raw_item

CREAD = "https://www.awin1.com/cread.php"
AWCLICK = "https://www.awin1.com/awclick.php"
MID, AFF = ADVERTISER_ID, PUBLISHER_ID
DEEP = "https%3A%2F%2Fwww.acme.example%2Fsale-1001"


def item(link: str, **changes: Any) -> dict[str, Any]:
    return raw_item(1001, urlTracking=link, **changes)


def parse(link: str, **changes: Any) -> Promotion | Rejected:
    return parse_promotion(item(link, **changes), NOW, PUBLISHER_ID)


def reason(link: str, **changes: Any) -> SkipReason:
    result = parse(link, **changes)
    assert isinstance(result, Rejected), result
    return result.reason


@pytest.mark.parametrize(
    "url",
    [
        f"{CREAD}?awinmid={MID}&awinaffid={AFF}",
        f"{CREAD}?awinaffid={AFF}&awinmid={MID}&clickref=x",
        f"{CREAD}?awinmid={MID}&awinaffid={AFF}&ued={DEEP}",
        f"{CREAD}?awinmid={MID}&awinaffid={AFF}&ued=https%3A%2F%2Facme.example%2Fother",
        f"{AWCLICK}?mid={MID}&id={AFF}",
        f"{AWCLICK}?mid={MID}&id={AFF}&p={DEEP}",
    ],
)
def test_both_documented_click_shapes_are_accepted_with_and_without_a_deep_link(
    url: str,
) -> None:
    result = parse(url)

    assert isinstance(result, Promotion) and result.tracking_url == url


@pytest.mark.parametrize(
    "url",
    [
        f"{CREAD}?awinmid={MID}&awinaffid=999",
        f"{CREAD}?awinmid=78&awinaffid={AFF}",
        f"{AWCLICK}?mid={MID}&id=999",
        f"{AWCLICK}?mid=78&id={AFF}",
        f"{CREAD}?awinmid={MID}&awinaffid=0{AFF}",
    ],
)
def test_a_link_for_another_publisher_or_advertiser_is_refused(url: str) -> None:
    assert reason(url) is SkipReason.TRACKING_OWNER_MISMATCH


@pytest.mark.parametrize(
    "url",
    [
        f"{CREAD}?awinmid={MID}",
        f"{CREAD}?awinaffid={AFF}",
        f"{CREAD}",
        f"{AWCLICK}?mid={MID}",
        f"{AWCLICK}?id={AFF}",
        f"{CREAD}?mid={MID}&id={AFF}",
        f"{CREAD}?awinmid={MID}&awinaffid={AFF}&awinaffid=999",
        f"{CREAD}?awinmid={MID}&awinaffid=999&awinaffid={AFF}",
        f"{CREAD}?awinmid={MID}&awinmid=78&awinaffid={AFF}",
        f"{CREAD}?awinmid={MID}&awinaffid={AFF}&ued={DEEP}&ued={DEEP}",
        f"{CREAD}?awinmid={MID}&awinaffid={AFF};awinaffid=999",
        f"{CREAD}?awinmid={MID};awinaffid={AFF}",
        f"{CREAD}?awinmid={MID}&awinaffid={AFF}&broken",
        f"{CREAD}?awinmid={MID}&awinaffid={AFF}#frag",
        f"https://www.awin1.com/other.php?awinmid={MID}&awinaffid={AFF}",
        f"https://www.awin1.com/cread.php/extra?awinmid={MID}&awinaffid={AFF}",
        f"https://www.awin1.com/CREAD.PHP?awinmid={MID}&awinaffid={AFF}",
        f"https://www.awin1.com/?awinmid={MID}&awinaffid={AFF}",
    ],
)
def test_a_link_whose_shape_is_not_recognised_is_refused(url: str) -> None:
    assert reason(url) is SkipReason.TRACKING_SHAPE_UNRECOGNISED


@pytest.mark.parametrize(
    "deep",
    [
        "javascript%3Aalert(1)",
        "http%3A%2F%2Fwww.acme.example%2Fsale",
        "https%3A%2F%2Fevil.example%2Fsale",
        "https%3A%2F%2Fwww.awin1.com%2Fcread.php",
        "https%3A%2F%2Fprf.hn%2Fclick%2F1",
        "https%3A%2F%2Facme.example.evil.example%2F",
        "https%3A%2F%2Fcdn.acme.example%2Fsale",
        "https%3A%2F%2F127.0.0.1%2Fsale",
        "https%3A%2F%2Fuser%40www.acme.example%2Fsale",
        "",
        "not-a-url",
    ],
)
def test_a_deep_link_that_is_unsafe_or_goes_elsewhere_than_the_item_is_refused(
    deep: str,
) -> None:
    url = f"{CREAD}?awinmid={MID}&awinaffid={AFF}&ued={deep}"

    assert reason(url) is SkipReason.TRACKING_DESTINATION_MISMATCH


def test_the_deep_link_host_may_differ_from_the_item_url_by_www_only() -> None:
    url = f"{CREAD}?awinmid={MID}&awinaffid={AFF}&ued=https%3A%2F%2FACME.example%2Fx"

    assert isinstance(parse(url, url="https://www.acme.example/sale"), Promotion)


def test_without_an_item_url_any_clean_non_network_deep_link_is_accepted() -> None:
    url = f"{CREAD}?awinmid={MID}&awinaffid={AFF}&ued=https%3A%2F%2Fwhoever.example%2Fx"

    assert isinstance(parse(url, url=None), Promotion)


def test_the_publisher_id_given_to_the_parser_is_the_one_that_is_checked() -> None:
    good = item(f"{CREAD}?awinmid={MID}&awinaffid={AFF}")

    assert isinstance(parse_promotion(good, NOW, AFF), Promotion)
    other = parse_promotion(good, NOW, AFF + 1)
    assert isinstance(other, Rejected)
    assert other.reason is SkipReason.TRACKING_OWNER_MISMATCH


def test_the_host_check_still_comes_first() -> None:
    url = f"https://evil.example/cread.php?awinmid={MID}&awinaffid={AFF}"

    assert reason(url) is SkipReason.TRACKING_HOST_NOT_ALLOWED
