"""``build_shape_report``: names only, bounded, sanitised, and never a value (FREEJ-52)."""

from dataclasses import fields
from typing import Any

import pytest

from app.core.awin_client import PromotionsPage
from app.services import awin_shape_report as module
from app.services.awin_shape_report import (
    MAX_ENTRIES,
    MAX_NAME_LENGTH,
    NameList,
    ShapeReport,
    build_shape_report,
    sanitise_name,
)
from tests.awin_samples import CODE, PUBLISHER_ID, raw_item


def page(*items: Any, **kwargs: Any) -> PromotionsPage:
    return PromotionsPage(tuple(items), **kwargs)


def name_lists(report: ShapeReport) -> list[NameList]:
    return [
        getattr(report, f.name)
        for f in fields(report)
        if isinstance(getattr(report, f.name), NameList)
    ]


def all_names(report: ShapeReport) -> list[str]:
    return [name for group in name_lists(report) for name in group.names]


def all_text(report: ShapeReport) -> str:
    """Every name and number the report holds, flattened, to search for leaks."""
    numbers = [str(getattr(report, f.name)) for f in fields(report)]
    return "\n".join([*all_names(report), *numbers])


# --- content -------------------------------------------------------------------------------


def test_a_bare_list_of_items_is_described_by_names() -> None:
    items = [raw_item(1), raw_item(2, voucher=None)]

    report = build_shape_report([page(*items)])

    assert report.pages == 1 and report.pages_bare_list == 1
    assert report.pages_object == 0 and report.total_pages_present == 0
    assert report.items == 2 and report.non_object_items == 0
    assert "promotionId" in report.item_keys.names
    assert "urlTracking" in report.item_keys.names
    assert report.advertiser_keys.names == ("id", "joined", "name")
    assert report.voucher_keys.names == ("attributable", "code", "exclusive")
    assert report.regions_keys.names == ("all", "list")
    assert report.url_hosts.names == ("www.acme.example",)
    assert report.tracking_hosts.names == ("www.awin1.com",)
    assert report.tracking_paths.names == ("/cread.php",)
    assert report.tracking_params.names == ("awinaffid", "awinmid", "clickref", "ued")
    assert (report.joined_true, report.joined_false, report.joined_other) == (2, 0, 0)


def test_an_object_envelope_records_total_pages_presence_per_page() -> None:
    pages = [
        page(raw_item(1), envelope="object", total_pages=2, total_pages_present=True),
        page(raw_item(2), envelope="object", total_pages_present=False),
        page(raw_item(3)),
    ]

    report = build_shape_report(pages)

    assert (report.pages, report.pages_object, report.pages_bare_list) == (3, 2, 1)
    assert report.total_pages_present == 1


def test_joined_is_counted_true_false_and_missing_or_not_a_boolean() -> None:
    items = [
        raw_item(1),
        raw_item(2, advertiser={"id": 1, "joined": False}),
        raw_item(3, advertiser={"id": 1}),
        raw_item(4, advertiser={"id": 1, "joined": "true"}),
        raw_item(5, advertiser={"id": 1, "joined": 1}),
        raw_item(6, advertiser="Acme"),
        raw_item(7, advertiser=None),
    ]

    report = build_shape_report([page(*items)])

    assert (report.joined_true, report.joined_false, report.joined_other) == (1, 1, 5)


def test_an_empty_run_gives_an_empty_report() -> None:
    report = build_shape_report([])

    assert report.pages == 0 and report.items == 0
    assert report.item_keys == NameList()


def test_non_object_items_are_counted_not_printed() -> None:
    items = ["SECRET-STRING", 5, None, ["x"], True, raw_item(1)]

    report = build_shape_report([page(*items)])

    assert report.items == 6 and report.non_object_items == 5
    assert "SECRET-STRING" not in all_text(report)
    assert (report.joined_true, report.joined_false, report.joined_other) == (1, 0, 0)


def test_non_object_sub_objects_contribute_no_keys() -> None:
    items = [raw_item(1, advertiser="x", voucher=["a"], regions=None)]

    report = build_shape_report([page(*items)])

    assert report.advertiser_keys == NameList()
    assert report.voucher_keys == NameList()
    assert report.regions_keys == NameList()


# --- urls ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [None, 5, ["https://x.example/"], "not a url", "", "/relative/path", "http://[bad"],
)
def test_urls_that_do_not_parse_are_counted(value: Any) -> None:
    report = build_shape_report([page(raw_item(1, urlTracking=value, url=value))])

    assert report.tracking_unreadable == 1 and report.url_unreadable == 1
    assert report.tracking_hosts == NameList() and report.url_hosts == NameList()
    assert report.tracking_paths == NameList() and report.tracking_params == NameList()


def test_only_the_host_of_a_url_is_kept_not_userinfo_or_port() -> None:
    link = "https://user:hunter2@WWW.Awin1.com:8443/cread.php?awinmid=1#frag"

    report = build_shape_report([page(raw_item(1, urlTracking=link, url=link))])

    assert report.tracking_hosts.names == ("www.awin1.com",)
    assert report.url_hosts.names == ("www.awin1.com",)
    text = all_text(report)
    assert "hunter2" not in text and "user" not in text and "8443" not in text
    assert "frag" not in text


def test_query_parameter_names_are_kept_and_values_are_not() -> None:
    link = "https://www.awin1.com/cread.php?a=VALUE1&b=VALUE2&b=VALUE3&bare&=orphan&c="

    report = build_shape_report([page(raw_item(1, urlTracking=link))])

    assert report.tracking_params.names == ("a", "b", "c")
    assert "VALUE" not in all_text(report) and "orphan" not in all_text(report)
    assert "bare" not in all_text(report)


# --- what never appears --------------------------------------------------------------------


def test_no_value_ever_reaches_the_report() -> None:
    tracking = (
        f"https://www.awin1.com/cread.php?awinmid=77&awinaffid={PUBLISHER_ID}"
        "&ued=https%3A%2F%2Fwww.acme.example%2Fsale-1&clickref=ref-SECRET"
    )
    item = raw_item(
        1,
        urlTracking=tracking,
        title="TITLE-TEXT",
        description="DESCRIPTION-TEXT",
        terms="TERMS-TEXT",
    )

    report = build_shape_report([page(item, envelope="object", total_pages=1)])

    text = all_text(report)
    for secret in (
        CODE,
        str(PUBLISHER_ID),
        "TITLE-TEXT",
        "DESCRIPTION-TEXT",
        "TERMS-TEXT",
        "ref-SECRET",
        "sale-1",
        "Acme Stores",
        "http",
        "10% off",
    ):
        assert secret not in text, secret


# --- bounds and sanitising -----------------------------------------------------------------


def test_each_set_is_capped_and_says_how_many_more() -> None:
    keys = {f"key{n:05d}": 1 for n in range(5000)}

    report = build_shape_report([page(keys)])

    assert len(report.item_keys.names) == MAX_ENTRIES
    assert report.item_keys.more == 5000 - MAX_ENTRIES
    assert list(report.item_keys.names) == sorted(report.item_keys.names)
    assert report.item_keys.names[0] == "key00000"


def test_every_set_is_capped_not_only_item_keys() -> None:
    many = {f"k{n:04d}": 1 for n in range(300)}
    link = "https://h.example/p?" + "&".join(f"q{n:04d}=1" for n in range(300))
    items = [
        raw_item(1, advertiser=many, voucher=many, regions=many, urlTracking=link),
        *[raw_item(n, url=f"https://h{n:04d}.example/") for n in range(2, 302)],
    ]

    report = build_shape_report([page(*items)])

    for names in (
        report.advertiser_keys,
        report.voucher_keys,
        report.regions_keys,
        report.tracking_params,
        report.url_hosts,
    ):
        assert len(names.names) == MAX_ENTRIES and names.more > 0


def test_a_very_long_name_is_cut_to_the_maximum_length() -> None:
    report = build_shape_report([page({"k" * 100_000: 1, "short": 2})])

    assert max(len(name) for name in report.item_keys.names) == MAX_NAME_LENGTH
    assert report.item_keys.names[-1] == "short"


def test_names_that_become_equal_after_cutting_count_once() -> None:
    report = build_shape_report([page({"a" * 100 + "1": 1, "a" * 100 + "2": 1})])

    assert len(report.item_keys.names) == 1 and report.item_keys.more == 0


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("plain", "plain"),
        ("with space", "with space"),
        ("line\nbreak", "line?break"),
        ("tab\there", "tab?here"),
        ("\x1b[31mred\x1b[0m", "?[31mred?[0m"),
        ("nul\x00byte", "nul?byte"),
        ("del\x7fchar", "del?char"),
        ("caf\u00e9", "caf?"),
        ("\u202eevil", "?evil"),
        ("emoji\U0001f600", "emoji?"),
        ("\r\n", "??"),
    ],
)
def test_names_keep_only_printable_ascii(raw: str, expected: str) -> None:
    assert sanitise_name(raw) == expected


def test_sanitising_applies_to_keys_hosts_paths_and_parameter_names() -> None:
    evil_key = "k\x1b[2J\nboom\u00e9"
    link = "https://h\u00e9llo.example/pa\x1bth\u00e9?q%1b%5b31m%0aX=v&caf%C3%A9=1&\u202eRLO=1"
    item = {evil_key: 1, "advertiser": {evil_key: 1}, "urlTracking": link, "url": link}

    report = build_shape_report([page(item)])

    names = all_names(report)
    assert names, "the evil input should still produce (sanitised) names"
    for name in names:
        assert all(" " <= char <= "~" for char in name), repr(name)
    assert "k?[2J?boom?" in report.item_keys.names
    assert "caf?" in report.tracking_params.names


def test_a_name_is_cut_before_it_is_sanitised_so_huge_names_stay_cheap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(module, "MAX_NAME_LENGTH", 10)

    assert sanitise_name("x" * 1000) == "xxxxxxx..."
    assert len(sanitise_name("\u00e9" * 1000)) == 10
