"""Whether an offer's public page may say that Freejoy earns commission from its link.

The answer describes the link the outbound redirect would follow right now, chosen by the same
function the click uses (``app.core.followed_link``), not "a network link exists". Reads the links
only; nothing is written and no provider, link id or destination leaves this service.
"""

from collections.abc import Sequence
from typing import Protocol

from app.core.followed_link import followed_link_earns_commission
from app.repositories.outbound_clicks import LinkRecord


class LinkReader(Protocol):
    async def list_active_links(self, offer_id: str) -> Sequence[LinkRecord]: ...


class OfferCommissionService:
    def __init__(self, links: LinkReader, *, allow_sample_provider: bool) -> None:
        self._links = links
        self._allow_sample = allow_sample_provider

    async def earns_commission(self, offer_id: str) -> bool:
        links = await self._links.list_active_links(offer_id)
        return followed_link_earns_commission(links, allow_sample=self._allow_sample)
