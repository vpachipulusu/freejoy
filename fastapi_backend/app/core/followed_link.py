"""Which affiliate link ``/go/<offer id>`` follows, and whether that link earns commission.

One rule, two callers. The outbound click service uses it to pick the destination of a click and to
refuse a click whose page did not disclose a commission; the public catalogue uses it to say whether
the offer page may show the "Ad" label and the commission sentence. Because both call the same
functions, then for the same stored links at the same moment the page describes the very link a
visitor is sent to. (The page can be a little older than the click; the click guard covers that.)

The rule: take the active links in the order given (the repository orders them oldest first), skip
any whose stored destination is not a clean https URL (``validate_https_url``, re-checked because
rows can be written outside the admin API) or whose host the link's provider may not use
(``destination_allowed``), and follow the first that is left. None left means nothing is followed.

An offer earns commission when its confirmed commercial basis is AFFILIATE (a ``direct`` link to a
merchant-domain programme can pay commission too) or when the link that is followed is on an affiliate
network. The basis arrives as a plain boolean so that ``core`` does not import models.

Pure: no I/O and no logging. A caller that wants to know why a link was skipped passes ``on_refused``.
"""

from collections.abc import Callable, Iterable
from typing import Protocol
from urllib.parse import urlsplit

from app.core.affiliate_hosts import (
    DIRECT_PROVIDER,
    SAMPLE_PROVIDER,
    destination_allowed,
)
from app.core.catalogue_urls import InvalidUrl, validate_https_url

REFUSED_NOT_HTTPS = "not a clean https URL"
REFUSED_HOST = "host not allowed"


class FollowableLink(Protocol):
    """What the rule reads from a stored link."""

    @property
    def id(self) -> str: ...

    @property
    def provider(self) -> str: ...

    @property
    def destination_url(self) -> str: ...

    @property
    def merchant_website(self) -> str | None: ...


Refusal = Callable[[str, str], None]


def usable_destination(
    link: FollowableLink, *, allow_sample: bool, on_refused: Refusal | None = None
) -> str | None:
    """The validated destination of ``link``, or None when ``/go/`` must skip it."""
    try:
        destination = validate_https_url(link.destination_url)
    except InvalidUrl:
        if on_refused:
            on_refused(link.id, REFUSED_NOT_HTTPS)
        return None
    host = urlsplit(destination).hostname or ""
    if not destination_allowed(
        link.provider,
        host,
        merchant_website=link.merchant_website,
        allow_sample=allow_sample,
    ):
        if on_refused:
            on_refused(link.id, REFUSED_HOST)
        return None
    return destination


def first_usable_link[L: FollowableLink](
    links: Iterable[L], *, allow_sample: bool, on_refused: Refusal | None = None
) -> tuple[L, str] | None:
    """The link ``/go/`` follows and its validated destination, or None when no link is usable."""
    for link in links:
        destination = usable_destination(
            link, allow_sample=allow_sample, on_refused=on_refused
        )
        if destination is not None:
            return link, destination
    return None


def provider_earns_commission(provider: str) -> bool:
    """True for an affiliate network; false for the merchant's own site and the local sample provider."""
    return provider not in (DIRECT_PROVIDER, SAMPLE_PROVIDER)


def offer_earns_commission(
    links: Iterable[FollowableLink], *, allow_sample: bool, basis_is_affiliate: bool
) -> bool:
    """Whether the offer earns Freejoy a commission: the one rule for the page flag and the click guard.

    True when the confirmed basis is AFFILIATE, or the link ``/go/`` would follow is on an affiliate
    network. Otherwise false, including when no link is usable.
    """
    if basis_is_affiliate:
        return True
    found = first_usable_link(links, allow_sample=allow_sample)
    return found is not None and provider_earns_commission(found[0].provider)
