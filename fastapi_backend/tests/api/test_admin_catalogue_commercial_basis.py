"""The commercial basis on the admin offers routes: request validation, read models and the publish rule."""

import pytest

from app.models.platform.offer import CommercialBasis, OfferStatus
from tests.api.test_admin_catalogue import (
    OFFER_BODY,
    Harness,
    harness,  # noqa: F401  (the fixture)
    token_body,
)
from tests.fakes_admin_catalogue import EARLIER, link, offer

BASES = [basis.value for basis in CommercialBasis]


def ready(**changes):
    return offer("off1", last_verified_at=EARLIER, **changes)


@pytest.mark.parametrize("basis", BASES)
async def test_create_stores_and_returns_the_basis(harness: Harness, basis: str):  # noqa: F811
    async with harness.client() as client:
        result = await client.post(
            "/v1/admin/offers", json={**OFFER_BODY, "commercial_basis": basis}
        )

    assert result.status_code == 201
    assert result.json()["data"]["commercial_basis"] == basis


async def test_create_without_a_basis_is_422_and_writes_nothing(harness: Harness):  # noqa: F811
    body = {k: v for k, v in OFFER_BODY.items() if k != "commercial_basis"}

    async with harness.client() as client:
        result = await client.post("/v1/admin/offers", json=body)

    assert result.status_code == 422
    assert result.json() == {"success": False, "error": "Invalid request"}
    assert harness.offers.offers == {} and harness.auditor.rows == []


@pytest.mark.parametrize("value", ["PAID", "sponsored", "", None, 3, True])
async def test_create_with_an_unknown_basis_is_422(harness: Harness, value):  # noqa: F811
    async with harness.client() as client:
        result = await client.post(
            "/v1/admin/offers", json={**OFFER_BODY, "commercial_basis": value}
        )

    assert result.status_code == 422
    assert harness.offers.offers == {}


async def test_update_changes_the_basis_and_the_write_response_carries_both_tokens(
    harness: Harness,  # noqa: F811
):
    harness.offers.offers["off1"] = offer("off1")

    async with harness.client() as client:
        before = (await token_body(client))["review_token"]
        result = await client.patch(
            "/v1/admin/offers/off1",
            json={"commercial_basis": "SPONSORED", "review_token": before},
        )

    data = result.json()["data"]
    assert result.status_code == 200
    assert data["commercial_basis"] == "SPONSORED"
    assert data["previous_review_token"] == before
    assert data["review_token"] != before


@pytest.mark.parametrize("value", [None, "PAID", ""])
async def test_update_refuses_a_null_or_unknown_basis(harness: Harness, value):  # noqa: F811
    harness.offers.offers["off1"] = offer("off1")

    async with harness.client() as client:
        body = {**await token_body(client), "commercial_basis": value}
        result = await client.patch("/v1/admin/offers/off1", json=body)

    assert result.status_code == 422
    assert harness.auditor.rows == []
    assert harness.offers.offers["off1"].commercial_basis is CommercialBasis.EDITORIAL


async def test_the_read_and_the_list_carry_the_basis(harness: Harness):  # noqa: F811
    harness.offers.offers["off1"] = offer(
        "off1", commercial_basis=CommercialBasis.AFFILIATE
    )

    async with harness.client() as client:
        detail = (await client.get("/v1/admin/offers/off1")).json()["data"]
        listed = (await client.get("/v1/admin/offers")).json()["data"]["offers"][0]

    assert detail["commercial_basis"] == "AFFILIATE"
    assert listed["commercial_basis"] == "AFFILIATE"


async def test_the_audit_row_of_a_create_and_an_update_names_the_basis(
    harness: Harness,  # noqa: F811
):
    async with harness.client() as client:
        created = await client.post(
            "/v1/admin/offers", json={**OFFER_BODY, "commercial_basis": "AFFILIATE"}
        )
        offer_id = created.json()["data"]["id"]
        await client.patch(
            f"/v1/admin/offers/{offer_id}",
            json={
                "commercial_basis": "SPONSORED",
                **await token_body(client, offer_id),
            },
        )

    create_row, update_row = harness.auditor.rows
    assert create_row["after"]["commercial_basis"] == "AFFILIATE"
    assert update_row["before"] == {"commercial_basis": "AFFILIATE"}
    assert update_row["after"] == {"commercial_basis": "SPONSORED"}


async def test_publishing_with_only_direct_links_without_a_basis_is_422_with_a_code(
    harness: Harness,  # noqa: F811
):
    harness.offers.offers["off1"] = ready()
    harness.offers.links["l1"] = link("l1", provider="direct")

    async with harness.client() as client:
        result = await client.post(
            "/v1/admin/offers/off1/publish", json=await token_body(client)
        )

    assert result.status_code == 422
    assert result.json()["code"] == "publish_blocked"
    assert "its commercial basis has not been confirmed" in result.json()["error"]
    assert harness.offers.offers["off1"].status is OfferStatus.DRAFT


async def test_publishing_with_only_direct_links_and_the_basis_passes(
    harness: Harness,  # noqa: F811
):
    harness.offers.offers["off1"] = ready(commercial_basis=CommercialBasis.SPONSORED)
    harness.offers.links["l1"] = link("l1", provider="direct")

    async with harness.client() as client:
        body = {**await token_body(client), "commercial_basis": "SPONSORED"}
        result = await client.post("/v1/admin/offers/off1/publish", json=body)

    assert result.status_code == 200
    assert result.json()["data"]["status"] == "PUBLISHED"
    assert result.json()["data"]["commercial_basis"] == "SPONSORED"


async def test_publishing_with_a_basis_other_than_the_stored_one_is_409_offer_changed(
    harness: Harness,  # noqa: F811
):
    harness.offers.offers["off1"] = ready(commercial_basis=CommercialBasis.EDITORIAL)
    harness.offers.links["l1"] = link("l1", provider="direct")

    async with harness.client() as client:
        body = {**await token_body(client), "commercial_basis": "SPONSORED"}
        result = await client.post("/v1/admin/offers/off1/publish", json=body)

    assert result.status_code == 409
    assert result.json()["code"] == "offer_changed"
    assert harness.offers.offers["off1"].status is OfferStatus.DRAFT


@pytest.mark.parametrize("value", ["PAID", "", 7])
async def test_publish_refuses_an_unknown_basis(harness: Harness, value):  # noqa: F811
    harness.offers.offers["off1"] = ready()
    harness.offers.links["l1"] = link("l1", provider="direct")

    async with harness.client() as client:
        body = {**await token_body(client), "commercial_basis": value}
        result = await client.post("/v1/admin/offers/off1/publish", json=body)

    assert result.status_code == 422
    assert harness.auditor.rows == []


async def test_changing_the_basis_without_the_loaded_token_is_409_and_writes_nothing(
    harness: Harness,  # noqa: F811
):
    harness.offers.offers["off1"] = offer("off1")

    async with harness.client() as client:
        result = await client.patch(
            "/v1/admin/offers/off1", json={"commercial_basis": "SPONSORED"}
        )

    assert (result.status_code, result.json()["code"]) == (409, "offer_changed")
    assert harness.offers.offers["off1"].commercial_basis is CommercialBasis.EDITORIAL
    assert harness.auditor.rows == []


async def test_changing_the_basis_with_a_stale_token_is_409(harness: Harness):  # noqa: F811
    harness.offers.offers["off1"] = offer("off1")

    async with harness.client() as client:
        stale = (await token_body(client))["review_token"]
        await client.patch("/v1/admin/offers/off1", json={"summary": "Moved on"})
        result = await client.patch(
            "/v1/admin/offers/off1",
            json={"commercial_basis": "SPONSORED", "review_token": stale},
        )

    assert (result.status_code, result.json()["code"]) == (409, "offer_changed")
    assert harness.offers.offers["off1"].commercial_basis is CommercialBasis.EDITORIAL


async def test_an_edit_that_leaves_the_basis_alone_needs_no_token(harness: Harness):  # noqa: F811
    harness.offers.offers["off1"] = offer("off1")

    async with harness.client() as client:
        result = await client.patch("/v1/admin/offers/off1", json={"summary": "New"})

    assert result.status_code == 200


async def test_a_token_alone_is_not_an_edit(harness: Harness):  # noqa: F811
    harness.offers.offers["off1"] = offer("off1")

    async with harness.client() as client:
        result = await client.patch(
            "/v1/admin/offers/off1", json=await token_body(client)
        )

    assert result.status_code == 422
    assert harness.auditor.rows == []


async def test_a_writer_without_publish_cannot_change_the_basis_of_a_published_offer(
    harness: Harness,  # noqa: F811
):
    harness.grants["writer"] = ["offers.read", "offers.write"]
    harness.offers.offers["off1"] = ready(
        status=OfferStatus.PUBLISHED,
        source_url="https://shop.example.com",
        commercial_basis=CommercialBasis.SPONSORED,
    )

    async with harness.client("writer") as client:
        body = {**await token_body(client), "commercial_basis": "EDITORIAL"}
        result = await client.patch("/v1/admin/offers/off1", json=body)

    assert result.status_code == 403
    assert result.json()["code"] == "permission_denied"
    assert harness.offers.offers["off1"].commercial_basis is CommercialBasis.SPONSORED
    assert harness.auditor.rows == []


async def test_the_same_writer_can_change_the_basis_of_a_draft(harness: Harness):  # noqa: F811
    harness.grants["writer"] = ["offers.read", "offers.write"]
    harness.offers.offers["off1"] = offer("off1")

    async with harness.client("writer") as client:
        body = {**await token_body(client), "commercial_basis": "SPONSORED"}
        result = await client.patch("/v1/admin/offers/off1", json=body)

    assert result.status_code == 200


async def test_taking_the_last_network_link_off_a_published_offer_is_422_with_a_code(
    harness: Harness,  # noqa: F811
):
    harness.offers.offers["off1"] = ready(
        status=OfferStatus.PUBLISHED, commercial_basis=CommercialBasis.AFFILIATE
    )
    harness.offers.links["l1"] = link("l1", provider="awin")
    harness.offers.links["l2"] = link("l2", provider="direct")

    async with harness.client() as client:
        result = await client.patch(
            "/v1/admin/offers/off1/links/l1", json={"active": False}
        )

    assert result.status_code == 422
    assert result.json()["code"] == "publish_blocked"
    assert "its commercial basis has not been confirmed" in result.json()["error"]
    assert harness.offers.links["l1"].active is True


@pytest.mark.parametrize(
    ("providers", "expected"),
    [
        ([], False),
        (["direct"], True),
        (["direct", "sample"], True),
        (["awin"], False),
        (["direct", "awin"], False),
    ],
)
async def test_the_offer_read_says_whether_publishing_needs_the_confirmation(
    harness: Harness,
    providers,
    expected,  # noqa: F811
):
    harness.offers.offers["off1"] = offer("off1")
    for index, provider in enumerate(providers):
        harness.offers.links[f"l{index}"] = link(f"l{index}", provider=provider)

    async with harness.client() as client:
        data = (await client.get("/v1/admin/offers/off1")).json()["data"]

    assert data["needs_basis_confirmation"] is expected
