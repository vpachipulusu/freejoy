"""The one rule for which link ``/go/`` follows, and whether that link earns commission."""

import pytest

from app.core.followed_link import (
    REFUSED_HOST,
    REFUSED_NOT_HTTPS,
    first_usable_link,
    offer_earns_commission,
    provider_earns_commission,
    usable_destination,
)
from app.repositories.outbound_clicks import LinkRecord
from tests.fakes_outbound import make_link

SHOP = "https://www.acme.example/free-sample"
AWIN = "https://www.awin1.com/cread.php?awinmid=1&awinaffid=2"
SAMPLE = "https://example.com/free-sample"


def network(link_id: str = "net") -> LinkRecord:
    return make_link(id=link_id, provider="awin", destination_url=AWIN)


def direct(link_id: str = "dir") -> LinkRecord:
    return make_link(id=link_id, provider="direct", destination_url=SHOP)


def sample(link_id: str = "smp") -> LinkRecord:
    return make_link(id=link_id, provider="sample", destination_url=SAMPLE)


def broken(link_id: str = "bad") -> LinkRecord:
    """A network link whose host the provider may not use."""
    return make_link(
        id=link_id, provider="awin", destination_url="https://evil.example/x"
    )


@pytest.mark.parametrize(
    ("provider", "earns"),
    [
        ("awin", True),
        ("cj", True),
        ("partnerize", True),
        ("rakuten", True),
        ("tradedoubler", True),
        ("direct", False),
        ("sample", False),
    ],
)
def test_only_affiliate_networks_earn_commission(provider: str, earns: bool) -> None:
    assert provider_earns_commission(provider) is earns


def test_the_first_usable_link_is_chosen_in_the_order_given() -> None:
    found = first_usable_link([direct(), network()], allow_sample=False)

    assert found is not None
    link, destination = found
    assert (link.id, destination) == ("dir", SHOP)


def test_an_unusable_first_link_is_skipped() -> None:
    found = first_usable_link([broken(), network()], allow_sample=False)

    assert found is not None
    assert found[0].id == "net"


def test_no_links_and_only_unusable_links_choose_nothing() -> None:
    assert first_usable_link([], allow_sample=False) is None
    assert first_usable_link([broken(), sample()], allow_sample=False) is None


def test_the_sample_provider_is_followed_only_when_allowed() -> None:
    assert first_usable_link([sample()], allow_sample=True) is not None
    assert first_usable_link([sample()], allow_sample=False) is None


def test_refusals_are_reported_by_id_and_reason_never_by_destination() -> None:
    refused: list[tuple[str, str]] = []
    not_https = make_link(id="http", destination_url="http://www.awin1.com/x")

    first_usable_link(
        [not_https, broken("off"), network()],
        allow_sample=False,
        on_refused=lambda link_id, reason: refused.append((link_id, reason)),
    )

    assert refused == [("http", REFUSED_NOT_HTTPS), ("off", REFUSED_HOST)]


def test_usable_destination_is_none_for_a_refused_link_without_a_callback() -> None:
    assert usable_destination(broken(), allow_sample=False) is None
    assert usable_destination(network(), allow_sample=False) == AWIN


@pytest.mark.parametrize(
    ("links", "earns"),
    [
        ([], False),
        ([network()], True),
        ([direct()], False),
        ([sample()], False),
        ([network(), direct()], True),
        ([direct(), network()], False),
        ([broken(), network()], True),
        ([broken(), direct()], False),
        ([broken()], False),
        ([sample(), network()], False),
    ],
)
def test_the_flag_follows_the_link_that_would_be_followed(
    links: list[LinkRecord], earns: bool
) -> None:
    assert (
        offer_earns_commission(links, allow_sample=True, basis_is_affiliate=False)
        is earns
    )


def test_a_sample_link_that_is_not_allowed_is_skipped_before_the_flag_is_read() -> None:
    assert offer_earns_commission(
        [sample(), network()], allow_sample=False, basis_is_affiliate=False
    )


@pytest.mark.parametrize(
    "links",
    [[direct()], [sample()], [broken()], [network()], []],
    ids=["direct", "sample", "unusable", "network", "no links"],
)
def test_an_affiliate_basis_earns_whatever_the_followed_link_is(
    links: list[LinkRecord],
) -> None:
    assert offer_earns_commission(links, allow_sample=True, basis_is_affiliate=True)


@pytest.mark.parametrize(
    ("links", "earns"),
    [([direct()], False), ([sample()], False), ([network()], True), ([], False)],
    ids=["direct", "sample", "network", "no links"],
)
def test_a_basis_that_is_not_affiliate_leaves_it_to_the_followed_link(
    links: list[LinkRecord], earns: bool
) -> None:
    assert (
        offer_earns_commission(links, allow_sample=True, basis_is_affiliate=False)
        is earns
    )
