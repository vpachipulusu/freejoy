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


def test_the_publisher_id_given_to_the_parser_is_the_one_that_is_checked() -> None:
    good = item(f"{CREAD}?awinmid={MID}&awinaffid={AFF}")

    assert isinstance(parse_promotion(good, NOW, AFF), Promotion)
    other = parse_promotion(good, NOW, AFF + 1)
    assert isinstance(other, Rejected)
    assert other.reason is SkipReason.TRACKING_OWNER_MISMATCH


def test_the_host_check_still_comes_first() -> None:
    url = f"https://evil.example/cread.php?awinmid={MID}&awinaffid={AFF}"

    assert reason(url) is SkipReason.TRACKING_HOST_NOT_ALLOWED


ROGUE = "https%3A%2F%2Fevil.example%2F"
OWN = f"{CREAD}?awinmid={MID}&awinaffid={AFF}"
OWN_CLICK = f"{AWCLICK}?mid={MID}&id={AFF}"
OWN_DEEP = "https%3A%2F%2Fwww.acme.example%2Fsale-1001"


@pytest.mark.parametrize(
    "url",
    [
        f"{OWN}&ued={OWN_DEEP}&p={ROGUE}",  # a second deep link under the other name
        f"{OWN_CLICK}&p={OWN_DEEP}&ued={ROGUE}",
        f"{OWN}&UED={ROGUE}",
        f"{OWN}&ued[]={ROGUE}",
        f"{OWN}&p[]={ROGUE}",
        f"{OWN}&AwinAffId=999",
        f"{OWN}&awinaffid[]=999",
        f"{OWN}&+awinaffid=999",
        f"{OWN}&awinmid[]=78",
        f"{OWN}&AWINMID=78",
        f"{OWN}&awinaffid%20=999",
        f"{OWN}&platform=dl",
        f"{OWN}&clickref7=x",
        f"{OWN}&clickref0=x",
        f"{OWN}&clickReF=x",
        f"{OWN_CLICK}&awinmid=78",  # the other shape's id names on this path
        f"{OWN}&mid=78",
        f"{OWN}&clickref={ROGUE}",
        f"{OWN}&clickref=" + "a" * 65,
        f"{OWN}&clickref=sp%20ace",
        f"{OWN}&clickref=semi%3Bcolon",
        f"{OWN}&clickref=" + "%C3%A9",
    ],
)
def test_a_parameter_the_importer_does_not_know_is_never_let_through(url: str) -> None:
    assert reason(url) is SkipReason.TRACKING_SHAPE_UNRECOGNISED


@pytest.mark.parametrize(
    "url",
    [
        f"{OWN}&p={ROGUE}",
        f"{OWN_CLICK}&ued={ROGUE}",
    ],
)
def test_a_single_rogue_deep_link_under_either_name_is_refused(url: str) -> None:
    # The name does not matter: a deep link is judged against the item's own url whatever it is called.
    # (These URLs are rejected earlier, as an unknown shape, by the name allowlist; the assertion is "refused".)
    result = parse(url)

    assert isinstance(result, Rejected)


@pytest.mark.parametrize(
    "url",
    [
        f"{OWN}&clickref=x",
        f"{OWN}&clickref=Campaign_1.a-b",
        f"{OWN}&clickref=",
        f"{OWN}&clickref=x&clickref2=y&clickref3=z&clickref4=1&clickref5=2&clickref6=3",
        f"{OWN}&p={OWN_DEEP}",  # p on cread, pointing at the item's own host
        f"{OWN_CLICK}&ued={OWN_DEEP}",  # ued on awclick
        f"{OWN}&ued={OWN_DEEP}",
        f"{OWN_CLICK}&p={OWN_DEEP}&clickref=x",
    ],
)
def test_known_names_and_passive_clickrefs_are_accepted(url: str) -> None:
    assert isinstance(parse(url), Promotion)


def test_more_than_one_deep_link_in_total_is_refused_even_when_all_are_good() -> None:
    url = f"{OWN}&ued={OWN_DEEP}&p={OWN_DEEP}"

    assert reason(url) is SkipReason.TRACKING_SHAPE_UNRECOGNISED


def test_an_item_with_no_url_cannot_vouch_for_a_deep_link() -> None:
    url = f"{OWN}&ued={OWN_DEEP}"

    assert reason(url, url=None) is SkipReason.TRACKING_DESTINATION_MISMATCH
    assert reason(f"{OWN_CLICK}&p={OWN_DEEP}", url=None) is (
        SkipReason.TRACKING_DESTINATION_MISMATCH
    )


def test_an_item_with_no_url_and_no_deep_link_is_accepted() -> None:
    assert isinstance(parse(OWN, url=None), Promotion)


def test_the_module_function_returns_none_without_url_and_without_deep_link() -> None:
    from app.services.awin_tracking import tracking_problem

    assert (
        tracking_problem(OWN, publisher_id=AFF, advertiser_id=MID, source_url=None)
        is None
    )
    assert (
        tracking_problem(
            f"{OWN}&ued={OWN_DEEP}",
            publisher_id=AFF,
            advertiser_id=MID,
            source_url=None,
        )
        == "tracking_destination_mismatch"
    )


# --- passive_values (the network report ingest matches a reported click reference against these) ---


def test_passive_values_lists_every_clickref_value_in_the_query() -> None:
    from app.services.awin_tracking import passive_values

    url = (
        "https://www.awin1.com/cread.php?awinmid=77&awinaffid=424242"
        "&clickref=a1&clickref2=b2&clickref6=f6&clickref3="
    )

    assert passive_values(url) == {"a1", "b2", "f6"}


def test_passive_values_ignores_ids_deep_links_and_unknown_names() -> None:
    from app.services.awin_tracking import passive_values

    url = "https://www.awin1.com/cread.php?awinmid=77&awinaffid=424242&ued=x&other=y"

    assert passive_values(url) == frozenset()


def test_passive_values_of_a_malformed_query_is_empty() -> None:
    from app.services.awin_tracking import passive_values

    assert passive_values("https://www.awin1.com/cread.php?clickref=a;clickref2=b") == (
        frozenset()
    )
    assert passive_values("https://www.awin1.com/cread.php?clickref") == frozenset()


def test_passive_values_can_be_limited_to_named_slots() -> None:
    from app.services.awin_tracking import passive_values

    url = (
        "https://www.awin1.com/cread.php?awinmid=77&awinaffid=1&clickref=a&clickref2=b"
    )

    assert passive_values(url, frozenset({"clickref"})) == {"a"}
    assert passive_values(url, frozenset({"clickref2"})) == {"b"}
    assert passive_values(url, frozenset()) == frozenset()


def test_link_advertiser_id_reads_the_advertiser_of_either_click_shape() -> None:
    from app.services.awin_tracking import link_advertiser_id

    cread = "https://www.awin1.com/cread.php?awinmid=77&awinaffid=424242&clickref=a"
    awclick = "https://www.awin1.com/awclick.php?mid=88&id=424242"

    assert link_advertiser_id(cread) == 77
    assert link_advertiser_id(awclick) == 88


@pytest.mark.parametrize(
    "url",
    [
        "https://www.awin1.com/other.php?awinmid=77",
        "https://www.awin1.com/cread.php?awinaffid=424242",
        "https://www.awin1.com/cread.php?awinmid=77&awinmid=78",
        "https://www.awin1.com/cread.php?awinmid=abc",
        "https://www.awin1.com/cread.php?awinmid=",
        "https://www.awin1.com/cread.php?awinmid=-5",
        "https://www.awin1.com/cread.php?awinmid=7;awinaffid=1",
        "https://www.awin1.com/awclick.php?awinmid=77",
        "https://x.example/landing",
        "not a url",
    ],
)
def test_link_advertiser_id_is_none_for_anything_not_a_recognised_click_link(
    url: str,
) -> None:
    from app.services.awin_tracking import link_advertiser_id

    assert link_advertiser_id(url) is None
