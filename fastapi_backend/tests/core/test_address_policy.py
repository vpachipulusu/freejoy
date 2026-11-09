"""``app.core.address_policy``: which resolved addresses an outbound probe may connect to."""

import pytest

from app.core.address_policy import is_public_address


@pytest.mark.parametrize(
    "address",
    [
        "93.184.216.34",
        "8.8.8.8",
        "1.1.1.1",
        "2606:2800:220:1:248:1893:25c8:1946",
        "2001:4860:4860::8888",
    ],
)
def test_public_addresses_are_allowed(address: str) -> None:
    assert is_public_address(address) is True


@pytest.mark.parametrize(
    "address",
    [
        "0.0.0.0",  # unspecified
        "0.1.2.3",  # "this network"
        "10.0.0.1",  # private
        "172.16.0.1",
        "172.31.255.255",
        "192.168.1.1",
        "127.0.0.1",  # loopback
        "127.255.255.254",
        "169.254.169.254",  # link-local, cloud metadata
        "100.64.0.1",  # carrier-grade NAT
        "100.127.255.255",
        "192.0.0.1",  # IETF protocol assignments
        "192.0.2.1",  # documentation
        "198.18.0.1",  # benchmarking
        "198.51.100.1",
        "203.0.113.1",
        "224.0.0.1",  # multicast
        "239.255.255.255",
        "240.0.0.1",  # reserved
        "255.255.255.255",  # broadcast
    ],
)
def test_private_and_special_ipv4_addresses_are_refused(address: str) -> None:
    assert is_public_address(address) is False


@pytest.mark.parametrize(
    "address",
    [
        "::",  # unspecified
        "::1",  # loopback
        "fe80::1",  # link-local
        "fe80::1%eth0",  # scoped link-local
        "fc00::1",  # unique local
        "fd12:3456:789a::1",
        "fec0::1",  # deprecated site-local
        "ff02::1",  # multicast
        "ff00::",
        "2001:db8::1",  # documentation
        "2001::1",  # Teredo
        "64:ff9b::7f00:1",  # NAT64 embedding 127.0.0.1
        "64:ff9b::808:808",  # NAT64 embedding a public address: still refused
        "2002:7f00:1::1",  # 6to4 embedding 127.0.0.1
        "2002:808:808::1",  # 6to4 embedding a public address: still refused
        "::ffff:127.0.0.1",  # IPv4-mapped loopback
        "::ffff:10.0.0.1",
        "::ffff:169.254.169.254",
        "::127.0.0.1",  # deprecated IPv4-compatible
    ],
)
def test_private_and_special_ipv6_addresses_are_refused(address: str) -> None:
    assert is_public_address(address) is False


def test_an_ipv4_mapped_public_address_is_judged_as_its_ipv4_address() -> None:
    assert is_public_address("::ffff:8.8.8.8") is True


@pytest.mark.parametrize(
    "address", ["", "not-an-ip", "1.2.3", "999.1.1.1", "1.2.3.4.5"]
)
def test_anything_that_is_not_an_address_is_refused(address: str) -> None:
    assert is_public_address(address) is False
