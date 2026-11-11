"""The offer page flag and the outbound click read one sample-provider setting."""

import pytest

from app.api import deps
from app.api.v1.catalogue import get_offer_commission_service
from app.api.v1.catalogue_click import get_click_service
from tests.fakes import FakeSession


class _Settings:
    def __init__(self, relaxed: bool) -> None:
        self.relaxed_environment = relaxed


@pytest.mark.parametrize("relaxed", [True, False])
def test_both_services_get_the_same_sample_provider_value(
    monkeypatch: pytest.MonkeyPatch, relaxed: bool
) -> None:
    monkeypatch.setattr(deps, "settings", _Settings(relaxed))
    session = FakeSession()

    commission = get_offer_commission_service(session)  # type: ignore[arg-type]
    clicks = get_click_service(session)  # type: ignore[arg-type]

    assert commission._allow_sample is relaxed
    assert clicks._allow_sample is relaxed
