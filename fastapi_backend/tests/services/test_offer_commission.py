"""OfferCommissionService, and proof that it cannot disagree with the outbound click."""

import pytest

from app.models.platform.click_event import UserAgentClass
from app.repositories.outbound_clicks import LinkRecord
from app.services.catalogue import CatalogueService
from app.services.offer_commission import OfferCommissionService
from app.services.outbound_clicks import ClickRedirect, OutboundClickService
from tests.fakes_catalogue import NOW, FakeCatalogueRepository, make_offer
from tests.fakes_outbound import FakeClickStore, FakeCommitter, make_link

SHOP = "https://www.acme.example/free-sample"
AWIN = "https://www.awin1.com/cread.php?awinmid=1&awinaffid=2"
SAMPLE = "https://example.com/free-sample"
NETWORK_PROVIDERS = {"awin", "cj", "partnerize", "rakuten", "tradedoubler"}


def network(link_id: str = "net") -> LinkRecord:
    return make_link(id=link_id, provider="awin", destination_url=AWIN)


def direct(link_id: str = "dir") -> LinkRecord:
    return make_link(id=link_id, provider="direct", destination_url=SHOP)


def sample(link_id: str = "smp") -> LinkRecord:
    return make_link(id=link_id, provider="sample", destination_url=SAMPLE)


def broken(provider: str, link_id: str = "bad") -> LinkRecord:
    return make_link(
        id=link_id, provider=provider, destination_url="https://evil.example/x"
    )


async def test_an_offer_whose_followed_link_is_a_network_earns_commission() -> None:
    store = FakeClickStore([network()])
    service = OfferCommissionService(store, allow_sample_provider=False)

    assert await service.earns_commission("offer_1") is True
    assert store.asked == ["offer_1"]


async def test_an_offer_with_only_a_direct_link_earns_nothing() -> None:
    service = OfferCommissionService(
        FakeClickStore([direct()]), allow_sample_provider=False
    )

    assert await service.earns_commission("offer_1") is False


async def test_an_offer_without_a_usable_link_earns_nothing() -> None:
    unusable = OfferCommissionService(
        FakeClickStore([broken("awin")]), allow_sample_provider=False
    )
    empty = OfferCommissionService(FakeClickStore([]), allow_sample_provider=False)

    assert await unusable.earns_commission("offer_1") is False
    assert await empty.earns_commission("offer_1") is False


SCENARIOS: dict[str, list[LinkRecord]] = {
    "network only": [network()],
    "direct only": [direct()],
    "network then direct": [network(), direct()],
    "direct then network": [direct(), network()],
    "unusable network then direct": [broken("awin"), direct()],
    "unusable direct then network": [broken("direct"), network()],
    "unusable only": [broken("awin")],
    "sample then network": [sample(), network()],
    "network then sample": [network(), sample()],
    "two networks": [network("a"), network("b")],
    "none": [],
}


@pytest.mark.parametrize("allow_sample", [False, True])
@pytest.mark.parametrize("name", list(SCENARIOS))
async def test_the_page_flag_and_the_click_always_use_the_same_link(
    name: str, allow_sample: bool
) -> None:
    """Same stored links and setting: the click follows one link, the flag describes that link."""
    links = SCENARIOS[name]
    click_store = FakeClickStore(links)
    clicks = OutboundClickService(
        CatalogueService(
            FakeCatalogueRepository(by_id=make_offer()), clock=lambda: NOW
        ),
        click_store,
        FakeCommitter(),
        allow_sample_provider=allow_sample,
    )
    flag = await OfferCommissionService(
        FakeClickStore(links), allow_sample_provider=allow_sample
    ).earns_commission("offer_1")

    outcome = await clicks.record_click("offer_1", None, UserAgentClass.DESKTOP)

    if outcome is None:
        assert flag is False
        assert click_store.clicks == []
        return
    assert isinstance(outcome, ClickRedirect)
    followed = next(
        link for link in links if link.id == click_store.clicks[0]["affiliate_link_id"]
    )
    assert followed.destination_url == outcome.destination_url
    assert flag is (followed.provider in NETWORK_PROVIDERS)
