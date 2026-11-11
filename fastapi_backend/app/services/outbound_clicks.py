"""The outbound click: decide whether a visitor may be sent to the provider, and count it once.

Rules, in order:

1. The offer must be public by the same rule as the catalogue API (``CatalogueService``): an
   unknown, draft, archived, never-live, hidden-category or inactive-merchant offer is ``None``
   (the caller answers 404); an offer that was public and has ended is an ``ExpiredOffer`` (410).
   Neither writes a click.
2. The offer needs a usable active affiliate link, chosen by ``app.core.followed_link`` (the same
   function the public offer page uses for its "earns commission" flag): the first whose stored
   destination is a clean https URL on a host its provider is allowed to use (``affiliate_hosts``).
   Links that fail are skipped; if none is left the answer is ``None`` and no click is written.
3. Exactly one click event is written and committed, then the destination is returned.

Nothing here logs a destination URL: logs carry link ids and a reason code only.
"""

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from app.core.followed_link import first_usable_link
from app.models.platform.click_event import UserAgentClass
from app.repositories.catalogue import OfferRecord
from app.repositories.outbound_clicks import LinkRecord
from app.services.catalogue import ExpiredOffer

logger = logging.getLogger("yourapp.outbound")

MAX_HOST_LENGTH = 253
_HOST = re.compile(
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*"
)
_NUMERIC_LABEL = re.compile(r"\d+")


def _log_refused(link_id: str, reason: str) -> None:
    logger.warning("affiliate link %s refused: %s", link_id, reason)


class OfferResolver(Protocol):
    async def get_offer_by_id(
        self, offer_id: str
    ) -> OfferRecord | ExpiredOffer | None: ...


class ClickStore(Protocol):
    async def list_active_links(self, offer_id: str) -> Sequence[LinkRecord]: ...

    async def add_click(
        self,
        *,
        offer_id: str,
        affiliate_link_id: str,
        referrer_host: str | None,
        user_agent_class: UserAgentClass | None,
    ) -> None: ...


class Committer(Protocol):
    async def commit(self) -> None: ...


@dataclass(frozen=True)
class ClickRedirect:
    """Where to send the visitor: a validated https URL on an allowed host."""

    destination_url: str


ClickOutcome = ClickRedirect | ExpiredOffer | None


def clean_referrer_host(raw: str | None) -> str | None:
    """A DNS host name in lower case, or None for anything else.

    The caller extracted this from a ``Referer`` header, which a visitor controls, so it is
    checked again here: no path, port, user info, address literal or whitespace gets through.
    """
    host = (raw or "").strip().lower()
    if not host or len(host) > MAX_HOST_LENGTH or not _HOST.fullmatch(host):
        return None
    if _NUMERIC_LABEL.fullmatch(host.rsplit(".", 1)[-1]):
        return None
    return host


class OutboundClickService:
    def __init__(
        self,
        offers: OfferResolver,
        store: ClickStore,
        session: Committer,
        *,
        allow_sample_provider: bool,
    ) -> None:
        self._offers = offers
        self._store = store
        self._session = session
        self._allow_sample = allow_sample_provider

    async def record_click(
        self,
        offer_id: str,
        referrer_host: str | None,
        user_agent_class: UserAgentClass | None,
        *,
        record: bool = True,
    ) -> ClickOutcome:
        """Resolve the destination and write one click event, or write nothing.

        ``record=False`` (the per-offer cap is spent) still returns the destination but writes no click.
        """
        offer = await self._offers.get_offer_by_id(offer_id)
        if offer is None or isinstance(offer, ExpiredOffer):
            return offer
        found = await self._first_usable_link(offer.id)
        if found is None:
            return None
        link, destination = found
        if record:
            await self._store.add_click(
                offer_id=offer.id,
                affiliate_link_id=link.id,
                referrer_host=clean_referrer_host(referrer_host),
                user_agent_class=user_agent_class,
            )
            await self._session.commit()
        return ClickRedirect(destination)

    async def _first_usable_link(self, offer_id: str) -> tuple[LinkRecord, str] | None:
        """The shared choice (``app.core.followed_link``), with a log line per skipped link."""
        found = first_usable_link(
            await self._store.list_active_links(offer_id),
            allow_sample=self._allow_sample,
            on_refused=_log_refused,
        )
        if found is None:
            logger.warning("offer %s has no usable affiliate link", offer_id)
        return found
