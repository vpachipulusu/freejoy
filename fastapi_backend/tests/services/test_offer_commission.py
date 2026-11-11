"""OfferCommissionService, the click guard, and proof that they cannot disagree with the outbound click."""

import pytest

from app.models.platform.click_event import UserAgentClass
from app.models.platform.offer import CommercialBasis
from app.repositories.outbound_clicks import LinkRecord
from app.services.catalogue import CatalogueService
from app.services.offer_commission import OfferCommissionService
from app.services.outbound_clicks import (
    ClickRedirect,
    DisclosureNeeded,
    OutboundClickService,
)
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


def service_for(
    links: list[LinkRecord], allow_sample: bool = False
) -> OfferCommissionService:
    return OfferCommissionService(
        FakeClickStore(links), allow_sample_provider=allow_sample
    )


async def test_an_offer_whose_followed_link_is_a_network_earns_commission() -> None:
    store = FakeClickStore([network()])
    service = OfferCommissionService(store, allow_sample_provider=False)

    assert await service.earns_commission(make_offer()) is True
    assert store.asked == ["offer_1"]


async def test_a_direct_only_editorial_offer_earns_nothing() -> None:
    offer = make_offer(commercial_basis=CommercialBasis.EDITORIAL)

    assert await service_for([direct()]).earns_commission(offer) is False


async def test_an_offer_without_a_usable_link_earns_nothing_unless_affiliate() -> None:
    offer = make_offer()

    assert await service_for([broken("awin")]).earns_commission(offer) is False
    assert await service_for([]).earns_commission(offer) is False


@pytest.mark.parametrize(
    ("basis", "link", "earns"),
    [
        (CommercialBasis.AFFILIATE, "direct", True),
        (CommercialBasis.EDITORIAL, "direct", False),
        (CommercialBasis.SPONSORED, "direct", False),
        (CommercialBasis.EDITORIAL, "network", True),
        (CommercialBasis.SPONSORED, "network", True),
        (CommercialBasis.AFFILIATE, "network", True),
    ],
)
async def test_the_confirmed_basis_or_the_followed_network_makes_an_offer_earn(
    basis: CommercialBasis, link: str, earns: bool
) -> None:
    links = [direct() if link == "direct" else network()]

    flag = await service_for(links).earns_commission(make_offer(commercial_basis=basis))

    assert flag is earns


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


def click_service(
    links: list[LinkRecord], basis: CommercialBasis, allow_sample: bool
) -> tuple[OutboundClickService, FakeClickStore]:
    store = FakeClickStore(links)
    catalogue = CatalogueService(
        FakeCatalogueRepository(by_id=make_offer(commercial_basis=basis)),
        clock=lambda: NOW,
    )
    service = OutboundClickService(
        catalogue, store, FakeCommitter(), allow_sample_provider=allow_sample
    )
    return service, store


@pytest.mark.parametrize("basis", list(CommercialBasis))
@pytest.mark.parametrize("allow_sample", [False, True])
@pytest.mark.parametrize("name", list(SCENARIOS))
async def test_the_page_flag_and_the_click_always_use_the_same_link_and_rule(
    name: str, allow_sample: bool, basis: CommercialBasis
) -> None:
    """Same stored links, basis and setting: the click follows one link; the flag and the guard agree."""
    links = SCENARIOS[name]
    flag = await service_for(links, allow_sample).earns_commission(
        make_offer(commercial_basis=basis)
    )
    disclosed, store = click_service(links, basis, allow_sample)
    undisclosed, store_nd = click_service(links, basis, allow_sample)

    outcome = await disclosed.record_click("offer_1", None, UserAgentClass.DESKTOP)
    guarded = await undisclosed.record_click(
        "offer_1", None, UserAgentClass.DESKTOP, commission_disclosed=False
    )

    if outcome is None:
        assert guarded is None
        assert store.clicks == store_nd.clicks == []
        assert flag is (basis is CommercialBasis.AFFILIATE)
        return
    assert isinstance(outcome, ClickRedirect)
    followed = next(
        link for link in links if link.id == store.clicks[0]["affiliate_link_id"]
    )
    assert followed.destination_url == outcome.destination_url
    assert flag is (
        basis is CommercialBasis.AFFILIATE or followed.provider in NETWORK_PROVIDERS
    )
    if flag:
        assert guarded == DisclosureNeeded("free-shampoo")
        assert store_nd.clicks == []
    else:
        assert guarded == outcome
        assert len(store_nd.clicks) == 1
