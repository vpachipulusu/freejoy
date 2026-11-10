"""The one definition of when publishing needs the editor's confirmed commercial basis."""

import pytest

from app.core.affiliate_hosts import PROVIDER_HOSTS
from app.services.offer_rules import needs_basis_confirmation


@pytest.mark.parametrize("provider", sorted(PROVIDER_HOSTS))
def test_an_active_link_on_any_registered_network_means_no_confirmation(provider):
    assert needs_basis_confirmation([(provider, True)]) is False
    assert needs_basis_confirmation([("direct", True), (provider, True)]) is False


@pytest.mark.parametrize("provider", ["direct", "sample", "somenewnet"])
def test_active_links_on_no_network_need_the_confirmation(provider):
    assert needs_basis_confirmation([(provider, True)]) is True
    assert needs_basis_confirmation([("direct", True), (provider, True)]) is True


def test_a_network_link_that_is_switched_off_does_not_count():
    assert needs_basis_confirmation([("awin", False), ("direct", True)]) is True


@pytest.mark.parametrize("links", [[], [("direct", False)], [("awin", False)]])
def test_no_active_link_means_nothing_to_confirm(links):
    assert needs_basis_confirmation(links) is False
