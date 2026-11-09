"""Which addresses a server-side fetch of an admin-written URL may connect to.

``catalogue_urls`` refuses IP literals and single-label names, but a public-looking DNS name can
still resolve to a private address (``127.0.0.1.nip.io``, a rebinding record). Anything that
connects to such a name must resolve it first and refuse the connection unless every address is a
globally routable unicast one. ``is_public_address`` is that test.

It refuses, for IPv4 and IPv6: unspecified, loopback, private, link-local (including the
169.254.169.254 metadata address), carrier-grade NAT (100.64.0.0/10), documentation and
benchmarking ranges, multicast and reserved space. An IPv4-mapped IPv6 address is judged as the
IPv4 address it wraps. Translation prefixes that can embed an IPv4 address (NAT64, 6to4) and the
deprecated IPv4-compatible and site-local ranges are refused outright, whatever they embed.
"""

import ipaddress

_REFUSED_IPV6_NETWORKS = tuple(
    ipaddress.IPv6Network(network)
    for network in (
        "::/96",  # deprecated IPv4-compatible addresses
        "64:ff9b::/96",  # NAT64 (RFC 6052)
        "64:ff9b:1::/48",  # local-use NAT64 (RFC 8215)
        "2002::/16",  # 6to4
        "2001::/32",  # Teredo
        "fec0::/10",  # deprecated site-local
    )
)


def _is_public_ipv4(address: ipaddress.IPv4Address) -> bool:
    return address.is_global and not (
        address.is_multicast or address.is_reserved or address.is_unspecified
    )


def _is_public_ipv6(address: ipaddress.IPv6Address) -> bool:
    if address.ipv4_mapped is not None:
        return _is_public_ipv4(address.ipv4_mapped)
    if any(address in network for network in _REFUSED_IPV6_NETWORKS):
        return False
    return address.is_global and not (
        address.is_multicast or address.is_reserved or address.is_unspecified
    )


def is_public_address(raw: str) -> bool:
    """True only for a globally routable unicast IPv4 or IPv6 address; False for anything else."""
    try:
        address = ipaddress.ip_address(raw)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv4Address):
        return _is_public_ipv4(address)
    return _is_public_ipv6(address)
