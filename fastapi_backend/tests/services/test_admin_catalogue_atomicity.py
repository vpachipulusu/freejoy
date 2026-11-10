"""Failure paths of the admin catalogue services: slug races and a failing audit write."""

from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.permissions import AdminContext
from app.models.platform.offer import OfferStatus, OfferType, PostageKind
from app.services.admin_catalogue_errors import SlugTaken
from app.services.admin_categories import AdminCategoryService
from app.services.admin_merchants import AdminMerchantService
from app.services.admin_offers import AdminOfferService
from app.services.offer_lifecycle import OfferAction
from tests.fakes_admin_catalogue import (
    EARLIER,
    NOW,
    FakeAuditor,
    FakeCategoryStore,
    FakeMerchantStore,
    FakeOfferStore,
    FakeSession,
    category,
    offer,
)

ACTOR = AdminContext("admin1", "admin", frozenset({"offers.write"}))
OFFER_DATA: dict[str, Any] = {
    "title": "Race",
    "summary": "s",
    "description": [],
    "category_id": "cat1",
    "merchant_id": "mer1",
    "offer_type": OfferType.FREE_SAMPLE,
    "featured": False,
    "image_tint": "sage",
    "image_label": "l",
    "image_alt": "a",
    "source_url": None,
    "postage_kind": PostageKind.FREE,
    "postage_amount_minor": None,
    "postage_currency": None,
    "requires_card": False,
    "subscription_terms": None,
    "eligibility": "e",
    "availability": "a",
    "steps": [],
    "start_at": None,
    "end_at": None,
    "freshness": None,
    "publisher_notes": None,
}
CATEGORY_DATA = {
    "name": "Race",
    "short_name": "R",
    "description": "d",
    "intro": "i",
    "tint": "sage",
    "sort_order": 0,
    "enabled": True,
}


def integrity_error(orig: object) -> IntegrityError:
    return IntegrityError("stmt", {}, orig)  # type: ignore[arg-type]


def direct_state(code: str) -> object:
    return SimpleNamespace(sqlstate=code)


def caused_state(code: str) -> object:
    return SimpleNamespace(sqlstate=None, __cause__=SimpleNamespace(sqlstate=code))


def racing(store: Any, error: IntegrityError) -> Any:
    async def insert(_values: dict[str, Any]) -> None:
        raise error

    store.insert = insert
    return store


def build_create(kind: str, error: IntegrityError):
    session, auditor = FakeSession(), FakeAuditor()
    if kind == "offer":
        store = racing(FakeOfferStore(), error)
        service, call = AdminOfferService(store, session, auditor), OFFER_DATA
        return session, auditor, lambda: service.create_offer(ACTOR, call)
    if kind == "category":
        store = racing(FakeCategoryStore(), error)
        service = AdminCategoryService(store, session, auditor)
        return session, auditor, lambda: service.create_category(ACTOR, CATEGORY_DATA)
    store = racing(FakeMerchantStore(), error)
    merchants = AdminMerchantService(store, session, auditor)
    return session, auditor, lambda: merchants.create_merchant(ACTOR, {"name": "Race"})


KINDS = ["offer", "category", "merchant"]


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("state", [direct_state, caused_state], ids=["direct", "cause"])
async def test_a_unique_violation_under_a_race_is_slug_taken_and_rolls_back(
    kind, state
):
    session, auditor, create = build_create(kind, integrity_error(state("23505")))

    with pytest.raises(SlugTaken):
        await create()

    assert session.rollbacks == 1 and session.commits == 0 and auditor.rows == []


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("state", [direct_state, caused_state], ids=["direct", "cause"])
async def test_any_other_integrity_error_propagates_unchanged(kind, state):
    error = integrity_error(state("23503"))
    session, auditor, create = build_create(kind, error)

    with pytest.raises(IntegrityError) as raised:
        await create()

    assert raised.value is error
    assert session.rollbacks == 1 and session.commits == 0 and auditor.rows == []


class FailingAuditor:
    async def record(self, admin: AdminContext, **fields: Any) -> None:
        raise RuntimeError("audit store down")


async def test_a_failing_audit_write_means_no_commit_for_create_transition_and_toggle():
    session = FakeSession()
    offers = FakeOfferStore()
    offers.offers["off1"] = offer(
        "off1", last_verified_at=EARLIER, source_url="https://shop.example.com"
    )
    offer_service = AdminOfferService(
        offers, session, FailingAuditor(), clock=lambda: NOW
    )
    categories = AdminCategoryService(
        FakeCategoryStore(category("cat1")), session, FailingAuditor()
    )

    with pytest.raises(RuntimeError):
        await offer_service.create_offer(ACTOR, OFFER_DATA)
    token = (await offer_service.get_offer("off1")).review_token
    with pytest.raises(RuntimeError):
        await offer_service.transition(
            ACTOR, "off1", OfferAction.PUBLISH, review_token=token
        )
    with pytest.raises(RuntimeError):
        await categories.set_enabled(ACTOR, "cat1", False)

    assert session.commits == 0
    assert offers.offers["off1"].status is not OfferStatus.ARCHIVED
