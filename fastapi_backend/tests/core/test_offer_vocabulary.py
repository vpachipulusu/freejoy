"""The plain-string vocabulary in ``app.core`` is the ORM enums by name (``app/partner`` cannot import the models)."""

from app.core import offer_vocabulary as vocabulary
from app.models.platform.offer import (
    OfferFreshness,
    OfferStatus,
    OfferType,
    PostageKind,
)


def test_each_tuple_is_the_enum_it_stands_for() -> None:
    assert vocabulary.OFFER_TYPES == tuple(member.name for member in OfferType)
    assert vocabulary.OFFER_STATUSES == tuple(member.name for member in OfferStatus)
    assert vocabulary.POSTAGE_KINDS == tuple(member.name for member in PostageKind)
    assert vocabulary.OFFER_FRESHNESS == tuple(member.name for member in OfferFreshness)


def test_the_named_constants_are_members_of_the_enums() -> None:
    assert vocabulary.STATUS_DRAFT == OfferStatus.DRAFT.value
    assert vocabulary.STATUS_PUBLISHED == OfferStatus.PUBLISHED.value
    assert vocabulary.STATUS_EXPIRED == OfferStatus.EXPIRED.value
    assert vocabulary.STATUS_ARCHIVED == OfferStatus.ARCHIVED.value
    assert vocabulary.POSTAGE_COST == PostageKind.COST.value


def test_a_retailer_may_submit_only_a_sample_or_a_trial() -> None:
    assert vocabulary.PARTNER_OFFER_TYPES == ("FREE_SAMPLE", "FREE_TRIAL")
    assert set(vocabulary.PARTNER_OFFER_TYPES) <= set(vocabulary.OFFER_TYPES)
