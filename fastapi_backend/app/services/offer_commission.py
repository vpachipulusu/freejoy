"""Whether an offer's public page may say that Freejoy earns commission from its link.

The answer is ``app.core.followed_link.offer_earns_commission``, the same function the outbound click
uses for its disclosure guard: the offer's confirmed basis is AFFILIATE, or the link the redirect
would follow right now is on an affiliate network. Reads the links only; nothing is written and no
provider, link id or destination leaves this service.
"""

from collections.abc import Sequence
from typing import Protocol

from app.core.followed_link import offer_earns_commission
from app.models.platform.offer import CommercialBasis
from app.repositories.catalogue import OfferRecord
from app.repositories.outbound_clicks import LinkRecord


class LinkReader(Protocol):
    async def list_active_links(self, offer_id: str) -> Sequence[LinkRecord]: ...


class OfferCommissionService:
    def __init__(self, links: LinkReader, *, allow_sample_provider: bool) -> None:
        self._links = links
        self._allow_sample = allow_sample_provider

    async def earns_commission(self, offer: OfferRecord) -> bool:
        links = await self._links.list_active_links(offer.id)
        return offer_earns_commission(
            links,
            allow_sample=self._allow_sample,
            basis_is_affiliate=offer.commercial_basis is CommercialBasis.AFFILIATE,
        )
