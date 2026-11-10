"""The admin partner key service with fake stores (no database)."""

from dataclasses import replace
from datetime import datetime
from typing import Any

import pytest

from app.core.partner_keys import key_digest, parse_key
from app.core.permissions import AdminContext
from app.repositories.admin_partner_keys import PartnerKeyRecord
from app.services.admin_catalogue_errors import (
    CatalogueNotFound,
    InvalidInput,
    KeyNotAllowed,
)
from app.services.admin_partner_keys import AdminPartnerKeyService
from tests.fakes_admin_catalogue import NOW, FakeAuditor, FakeSession, merchant

ACTOR = AdminContext("admin1", "admin", frozenset({"merchants.write"}))
ENVIRONMENT = "test"


class FakeKeyStore:
    def __init__(self) -> None:
        self.rows: dict[str, PartnerKeyRecord] = {}
        self.hashes: dict[str, str] = {}

    async def list_for_merchant(self, merchant_id: str) -> list[PartnerKeyRecord]:
        return [r for r in self.rows.values() if r.merchant_id == merchant_id]

    async def get(
        self, merchant_id: str, key_id: str, *, lock: bool = False
    ) -> PartnerKeyRecord | None:
        found = self.rows.get(key_id)
        return found if found and found.merchant_id == merchant_id else None

    async def insert(self, values: dict[str, Any]) -> PartnerKeyRecord:
        record = PartnerKeyRecord(
            id=values["id"],
            merchant_id=values["merchant_id"],
            label=values["label"],
            created_by=values["created_by"],
            created_at=NOW,
            revoked_at=None,
            last_used_at=None,
        )
        self.rows[record.id] = record
        self.hashes[record.id] = values["key_hash"]
        return record

    async def revoke(self, merchant_id: str, key_id: str) -> PartnerKeyRecord:
        self.rows[key_id] = replace(self.rows[key_id], revoked_at=NOW)
        return self.rows[key_id]


class FakeMerchants:
    def __init__(self, *items: Any) -> None:
        self.items = {item.id: item for item in items}

    async def get(self, merchant_id: str, *, lock: bool = False) -> Any:
        return self.items.get(merchant_id)


class Setup:
    def __init__(
        self,
        merchants: FakeMerchants | None = None,
        environment: str | None = ENVIRONMENT,
    ) -> None:
        self.keys, self.session, self.auditor = (
            FakeKeyStore(),
            FakeSession(),
            FakeAuditor(),
        )
        self.merchants = merchants or FakeMerchants(
            replace(merchant("mer1"), website="https://shop.example.com")
        )
        self.service = AdminPartnerKeyService(
            self.keys, self.merchants, self.session, self.auditor, environment
        )


async def test_creating_a_key_returns_it_once_and_stores_only_its_hash() -> None:
    setup = Setup()

    issued = await setup.service.create_key(ACTOR, "mer1", "Production feed")

    parsed = parse_key(issued.value, ENVIRONMENT)
    assert parsed is not None and parsed.key_id == issued.record.id
    assert setup.keys.hashes[issued.record.id] == key_digest(issued.value)
    assert issued.value not in repr(setup.keys.rows) + repr(setup.auditor.rows)
    assert issued.value not in repr(issued)
    assert setup.session.commits == 1


async def test_creating_a_key_is_audited_without_the_key() -> None:
    setup = Setup()

    issued = await setup.service.create_key(ACTOR, "mer1", "Production feed")

    (row,) = setup.auditor.rows
    assert row["action"] == "admin.partner_keys.create"
    assert (row["resource"], row["resource_id"]) == (
        "partner_api_keys",
        issued.record.id,
    )
    assert row["after"] == {"merchant_id": "mer1", "label": "Production feed"}
    assert issued.value not in repr(row)
    assert key_digest(issued.value) not in repr(row)


async def test_a_merchant_without_a_website_cannot_be_given_a_key() -> None:
    setup = Setup(FakeMerchants(merchant("mer1")))

    with pytest.raises(KeyNotAllowed) as caught:
        await setup.service.create_key(ACTOR, "mer1", "x")

    assert caught.value.code == "merchant_no_website"
    assert setup.keys.rows == {} and setup.session.commits == 0


async def test_an_inactive_merchant_cannot_be_given_a_key() -> None:
    inactive = replace(
        merchant("mer1", active=False), website="https://shop.example.com"
    )
    setup = Setup(FakeMerchants(inactive))

    with pytest.raises(KeyNotAllowed) as caught:
        await setup.service.create_key(ACTOR, "mer1", "x")

    assert caught.value.code == "merchant_inactive"


async def test_an_unknown_merchant_is_not_found() -> None:
    with pytest.raises(CatalogueNotFound):
        await Setup().service.create_key(ACTOR, "nope", "x")
    with pytest.raises(CatalogueNotFound):
        await Setup().service.list_keys("nope")


async def test_no_key_is_issued_where_the_environment_cannot_be_written_into_one() -> (
    None
):
    setup = Setup(environment=None)

    with pytest.raises(KeyNotAllowed) as caught:
        await setup.service.create_key(ACTOR, "mer1", "x")

    assert caught.value.code == "keys_unavailable"


@pytest.mark.parametrize(
    "label", ["fj_test_abcdefghijklmnop", "my key fj_production_a1b2c3d4e5f6a7b8c9d0"]
)
async def test_a_label_that_carries_a_key_is_refused(label: str) -> None:
    setup = Setup()

    with pytest.raises(InvalidInput) as caught:
        await setup.service.create_key(ACTOR, "mer1", label)

    assert caught.value.code == "invalid_label"
    assert setup.keys.rows == {}


async def test_revoking_sets_the_time_and_audits_the_change() -> None:
    setup = Setup()
    issued = await setup.service.create_key(ACTOR, "mer1", "Feed")

    revoked = await setup.service.revoke_key(ACTOR, "mer1", issued.record.id)

    assert isinstance(revoked.revoked_at, datetime)
    assert setup.auditor.rows[-1]["action"] == "admin.partner_keys.revoke"
    assert setup.auditor.rows[-1]["details"] == {"merchant_id": "mer1", "label": "Feed"}


async def test_revoking_twice_changes_nothing_and_audits_once() -> None:
    setup = Setup()
    issued = await setup.service.create_key(ACTOR, "mer1", "Feed")
    await setup.service.revoke_key(ACTOR, "mer1", issued.record.id)

    again = await setup.service.revoke_key(ACTOR, "mer1", issued.record.id)

    assert again.revoked_at is not None
    assert len(setup.auditor.rows) == 2
    assert setup.session.rollbacks == 1


async def test_revoking_a_key_of_another_merchant_is_not_found() -> None:
    setup = Setup(
        FakeMerchants(
            replace(merchant("mer1"), website="https://a.example.com"),
            replace(merchant("mer2"), website="https://b.example.com"),
        )
    )
    issued = await setup.service.create_key(ACTOR, "mer1", "Feed")

    with pytest.raises(CatalogueNotFound):
        await setup.service.revoke_key(ACTOR, "mer2", issued.record.id)


async def test_listing_returns_the_merchants_keys_only() -> None:
    setup = Setup()
    await setup.service.create_key(ACTOR, "mer1", "One")

    listed = await setup.service.list_keys("mer1")

    assert [k.label for k in listed] == ["One"]
