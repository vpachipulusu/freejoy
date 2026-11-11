"""/v1/catalogue: routes, headers, validation and privacy, over a fake repository.

The route, service, response models, ETag handling and rate limit are real.
"""

from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.deps import get_rate_limiter
from app.api.v1.catalogue import get_catalogue_service, get_offer_commission_service
from app.api.v1.catalogue_schemas import (
    CategoryData,
    CategoryRef,
    ExpiredOfferData,
    ExpiredOfferResponse,
    MerchantData,
    OfferData,
    OfferDetailData,
)
from app.core.rate_limit import PUBLIC_CATALOGUE_POLICY
from app.main import create_app
from app.models.platform.offer import (
    CommercialBasis,
    OfferFreshness,
    OfferStatus,
    OfferType,
    PostageKind,
)
from app.repositories.catalogue import OfferRecord, OfferSort
from app.repositories.outbound_clicks import LinkRecord
from app.services.catalogue import CatalogueService
from app.services.offer_commission import OfferCommissionService
from tests.fakes import FakeRateLimiter, RaisingRateLimiter, as_limiter
from tests.fakes_catalogue import (
    NOW,
    FakeCatalogueRepository,
    make_category,
    make_offer,
)
from tests.fakes_outbound import FakeClickStore, make_link

CATEGORY_FIELDS = {
    "slug",
    "name",
    "short_name",
    "description",
    "intro",
    "tint",
    "sort_order",
    "offer_count",
}
KEY = "test-only-internal-service-key-not-for-deployment"
CACHE = "private, max-age=60"
EDITORIAL_ONLY = {
    "publisher_notes",
    "commercial_basis",
    "status",
    "destination_url",
    "tracking_id",
    "source_url",
    "website",
    "created_at",
    "updated_at",
    "enabled",
    "active",
}


def build(
    repo: FakeCatalogueRepository,
    limiter: FakeRateLimiter | RaisingRateLimiter | None = None,
    links: list[LinkRecord] | None = None,
) -> tuple[AsyncClient, FakeRateLimiter | RaisingRateLimiter]:
    limiter = limiter or FakeRateLimiter()
    app = create_app()
    app.dependency_overrides[get_catalogue_service] = lambda: CatalogueService(
        repo, clock=lambda: NOW
    )
    app.dependency_overrides[get_offer_commission_service] = lambda: (
        OfferCommissionService(FakeClickStore(links or []), allow_sample_provider=False)
    )
    app.dependency_overrides[get_rate_limiter] = lambda: as_limiter(limiter)
    client = AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
        headers={"X-Internal-Key": KEY},
    )
    return client, limiter


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    repo = FakeCatalogueRepository(
        offers=[make_offer()],
        by_slug=make_offer(),
        categories=[make_category()],
    )
    http, _ = build(repo)
    async with http:
        yield http


# --- list -------------------------------------------------------------------


async def test_list_returns_the_envelope_with_total_and_paging() -> None:
    http, _ = build(FakeCatalogueRepository(offers=[make_offer()], total=30))

    async with http:
        response = await http.get("/v1/catalogue/offers?limit=5&offset=10")

    body = response.json()
    assert response.status_code == 200
    assert body["success"] is True
    assert (body["data"]["total"], body["data"]["limit"], body["data"]["offset"]) == (
        30,
        5,
        10,
    )
    assert body["data"]["items"][0]["slug"] == "free-shampoo"


async def test_list_item_shape_uses_the_lower_case_frontend_values() -> None:
    http, _ = build(
        FakeCatalogueRepository(
            offers=[
                make_offer(
                    offer_type=OfferType.FREE_TRIAL,
                    postage_kind=PostageKind.COST,
                    postage_amount_minor=199,
                    postage_currency="GBP",
                    freshness=OfferFreshness.ENDS_SOON,
                )
            ]
        )
    )

    async with http:
        item = (await http.get("/v1/catalogue/offers")).json()["data"]["items"][0]

    assert item["offer_type"] == "free_trial"
    assert item["freshness"] == "ends_soon"
    assert item["postage"] == {"kind": "cost", "amount_minor": 199, "currency": "GBP"}
    assert item["category"] == {"slug": "beauty", "name": "Beauty"}
    assert item["merchant"]["slug"] == "acme"
    assert item["steps"] == [{"title": "Sign up", "detail": "Fill in the form."}]


async def test_list_with_no_matches_is_an_empty_page_not_an_error() -> None:
    http, _ = build(FakeCatalogueRepository())

    async with http:
        response = await http.get("/v1/catalogue/offers?category=no-such-category")

    assert response.status_code == 200
    assert response.json()["data"] == {
        "items": [],
        "total": 0,
        "limit": 12,
        "offset": 0,
    }


async def test_list_query_parameters_reach_the_repository_as_typed_filters() -> None:
    repo = FakeCatalogueRepository()
    http, _ = build(repo)

    async with http:
        await http.get(
            "/v1/catalogue/offers?q=%20shampoo%20&category=beauty&type=cashback"
            "&type=free_trial&postage=free&sort=a_z&featured=true&limit=7&offset=3"
        )

    filters, now, limit, offset = repo.list_calls[0]
    assert filters.query == "shampoo"
    assert filters.category_slug == "beauty"
    assert filters.offer_types == (OfferType.CASHBACK, OfferType.FREE_TRIAL)
    assert filters.free_postage_only is True
    assert filters.featured is True
    assert filters.sort is OfferSort.A_Z
    assert (now, limit, offset) == (NOW, 7, 3)


async def test_list_defaults_are_newest_first_twelve_with_no_filters() -> None:
    repo = FakeCatalogueRepository()
    http, _ = build(repo)

    async with http:
        await http.get("/v1/catalogue/offers?q=%20%20")

    filters, _, limit, offset = repo.list_calls[0]
    assert filters.query is None
    assert filters.sort is OfferSort.NEWEST
    assert filters.free_postage_only is False
    assert filters.featured is None
    assert (limit, offset) == (12, 0)


@pytest.mark.parametrize(
    "query",
    [
        "limit=0",
        "limit=51",
        "limit=abc",
        "offset=-1",
        "offset=10001",
        "sort=price",
        "type=free_lunch",
        "type=FREE_SAMPLE",
        "postage=cost",
        "featured=maybe",
        "type=cashback&" * 5 + "type=cashback",
        "q=" + "x" * 101,
        "category=" + "x" * 101,
    ],
)
async def test_list_rejects_invalid_parameters_with_422(query: str) -> None:
    repo = FakeCatalogueRepository()
    http, _ = build(repo)

    async with http:
        response = await http.get(f"/v1/catalogue/offers?{query}")

    assert response.status_code == 422
    assert response.json() == {"success": False, "error": "Invalid request"}
    assert repo.list_calls == []


async def test_list_accepts_the_largest_allowed_page() -> None:
    repo = FakeCatalogueRepository()
    http, _ = build(repo)

    async with http:
        response = await http.get("/v1/catalogue/offers?limit=50&offset=10000")

    assert response.status_code == 200
    assert repo.list_calls[0][2:] == (50, 10000)


# --- detail -----------------------------------------------------------------


async def test_detail_returns_the_offer() -> None:
    http, _ = build(FakeCatalogueRepository(by_slug=make_offer()))

    async with http:
        response = await http.get("/v1/catalogue/offers/free-shampoo")

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["data"]["title"] == "Free shampoo sample"
    assert response.json()["data"]["description"] == [
        "First paragraph.",
        "Second paragraph.",
    ]


async def test_detail_says_whether_the_followed_link_earns_commission() -> None:
    offer = make_offer()
    network, direct = make_link(), make_link(provider="direct")
    http_network, _ = build(FakeCatalogueRepository(by_slug=offer), links=[network])
    http_direct, _ = build(FakeCatalogueRepository(by_slug=offer), links=[direct])
    http_none, _ = build(FakeCatalogueRepository(by_slug=offer))

    async with http_network, http_direct, http_none:
        paid = (await http_network.get("/v1/catalogue/offers/free-shampoo")).json()
        free = (await http_direct.get("/v1/catalogue/offers/free-shampoo")).json()
        bare = (await http_none.get("/v1/catalogue/offers/free-shampoo")).json()

    assert paid["data"]["earns_commission"] is True
    assert free["data"]["earns_commission"] is False
    assert bare["data"]["earns_commission"] is False


@pytest.mark.parametrize(
    ("basis", "earns"),
    [
        (CommercialBasis.AFFILIATE, True),
        (CommercialBasis.EDITORIAL, False),
        (CommercialBasis.SPONSORED, False),
    ],
)
async def test_the_confirmed_basis_decides_a_direct_only_offer(
    basis: CommercialBasis, earns: bool
) -> None:
    offer = make_offer(commercial_basis=basis)
    http, _ = build(
        FakeCatalogueRepository(by_slug=offer), links=[make_link(provider="direct")]
    )

    async with http:
        body = (await http.get("/v1/catalogue/offers/free-shampoo")).json()

    assert body["data"]["earns_commission"] is earns
    assert "commercial_basis" not in str(body)


async def test_the_flag_is_the_only_trace_of_the_link_in_the_detail_body() -> None:
    link = make_link(id="link_secret_77")
    http, _ = build(FakeCatalogueRepository(by_slug=make_offer()), links=[link])

    async with http:
        text = (await http.get("/v1/catalogue/offers/free-shampoo")).text

    for hidden in (link.id, link.provider, link.destination_url, "awin", "provider"):
        assert hidden not in text


async def test_list_items_carry_no_commission_flag() -> None:
    http, _ = build(FakeCatalogueRepository(offers=[make_offer()]), links=[make_link()])

    async with http:
        item = (await http.get("/v1/catalogue/offers")).json()["data"]["items"][0]

    assert "earns_commission" not in item


async def test_detail_of_an_unknown_slug_is_404() -> None:
    http, _ = build(FakeCatalogueRepository())

    async with http:
        response = await http.get("/v1/catalogue/offers/nope")

    assert response.status_code == 404
    assert response.json() == {
        "success": False,
        "error": "Offer not found",
        "code": "offer_not_found",
    }
    assert "Cache-Control" not in response.headers


async def test_a_route_level_404_carries_no_offer_code() -> None:
    """The Next.js app prunes a saved offer only on ``offer_not_found``; a wrong path must not look like it."""
    http, _ = build(FakeCatalogueRepository())

    async with http:
        response = await http.get("/v1/catalogue/no-such-route")

    assert response.status_code == 404
    assert response.json() == {"success": False, "error": "Not Found"}


@pytest.mark.parametrize("status", [OfferStatus.DRAFT, OfferStatus.ARCHIVED])
async def test_detail_of_an_unpublished_offer_is_the_same_404(
    status: OfferStatus,
) -> None:
    unknown, _ = build(FakeCatalogueRepository())
    hidden, _ = build(FakeCatalogueRepository(by_slug=make_offer(status=status)))

    async with unknown, hidden:
        missing = await unknown.get("/v1/catalogue/offers/x")
        draft = await hidden.get("/v1/catalogue/offers/x")

    assert draft.status_code == 404
    assert draft.content == missing.content


@pytest.mark.parametrize(
    ("status", "ended_at"),
    [
        (OfferStatus.PUBLISHED, NOW - timedelta(days=1)),
        (OfferStatus.EXPIRED, None),
    ],
)
async def test_detail_of_an_ended_offer_is_410_with_expired_true(
    status: OfferStatus, ended_at: object
) -> None:
    offer = make_offer(status=status, end_at=ended_at)
    http, _ = build(FakeCatalogueRepository(by_slug=offer))

    async with http:
        response = await http.get("/v1/catalogue/offers/free-shampoo")

    body = response.json()
    assert response.status_code == 410
    assert body["success"] is False
    assert body["expired"] is True
    assert body["data"]["slug"] == "free-shampoo"
    assert body["data"]["title"] == "Free shampoo sample"
    assert body["data"]["category"] == {"slug": "beauty", "name": "Beauty"}
    assert response.headers["Cache-Control"] == CACHE


async def test_expired_body_carries_only_the_stub_not_the_offer_details() -> None:
    offer = make_offer(end_at=NOW - timedelta(days=1), eligibility="secret terms")
    http, _ = build(FakeCatalogueRepository(by_slug=offer))

    async with http:
        response = await http.get("/v1/catalogue/offers/free-shampoo")

    assert set(response.json()["data"]) == {"slug", "title", "category", "ended_at"}
    assert "secret terms" not in response.text


async def test_detail_path_slug_is_bounded() -> None:
    http, _ = build(FakeCatalogueRepository())

    async with http:
        response = await http.get("/v1/catalogue/offers/" + "x" * 101)

    assert response.status_code == 422


# --- categories -------------------------------------------------------------


async def test_categories_return_counts_in_the_envelope() -> None:
    http, _ = build(
        FakeCatalogueRepository(
            categories=[make_category(), make_category(slug="food", offer_count=0)]
        )
    )

    async with http:
        response = await http.get("/v1/catalogue/categories")

    data = response.json()["data"]
    assert response.status_code == 200
    assert [(c["slug"], c["offer_count"]) for c in data] == [("beauty", 3), ("food", 0)]
    assert set(data[0]) == CATEGORY_FIELDS


async def test_categories_empty_is_an_empty_list() -> None:
    http, _ = build(FakeCatalogueRepository())

    async with http:
        response = await http.get("/v1/catalogue/categories")

    assert response.json() == {"success": True, "data": []}


# --- caching ----------------------------------------------------------------

ROUTES = [
    "/v1/catalogue/offers",
    "/v1/catalogue/offers/free-shampoo",
    "/v1/catalogue/categories",
]


@pytest.mark.parametrize("path", ROUTES)
async def test_success_responses_are_cacheable_with_an_etag(
    client: AsyncClient, path: str
) -> None:
    response = await client.get(path)

    assert response.headers["Cache-Control"] == CACHE
    assert response.headers["ETag"].startswith('"')


@pytest.mark.parametrize("path", ROUTES)
async def test_matching_if_none_match_returns_304(
    client: AsyncClient, path: str
) -> None:
    first = await client.get(path)

    second = await client.get(path, headers={"If-None-Match": first.headers["ETag"]})

    assert second.status_code == 304
    assert second.content == b""
    assert second.headers["Cache-Control"] == CACHE


async def test_stale_if_none_match_returns_the_full_response(
    client: AsyncClient,
) -> None:
    response = await client.get(ROUTES[0], headers={"If-None-Match": '"stale"'})

    assert response.status_code == 200


async def test_etag_changes_when_the_content_changes() -> None:
    first_client, _ = build(FakeCatalogueRepository(offers=[make_offer()]))
    second_client, _ = build(FakeCatalogueRepository(offers=[make_offer(title="New")]))

    async with first_client, second_client:
        first = await first_client.get(ROUTES[0])
        second = await second_client.get(ROUTES[0])

    assert first.headers["ETag"] != second.headers["ETag"]


# --- auth, rate limit, failure ----------------------------------------------


@pytest.mark.parametrize("path", ROUTES)
@pytest.mark.parametrize("key", ["", "wrong-key"])
async def test_requires_the_internal_key(path: str, key: str) -> None:
    repo = FakeCatalogueRepository(by_slug=make_offer())
    http, _ = build(repo)

    async with http:
        response = await http.get(path, headers={"X-Internal-Key": key})

    assert response.status_code == 401
    assert repo.list_calls == repo.slugs == []


async def test_rate_limit_is_keyed_by_the_forwarded_client_ip() -> None:
    http, limiter = build(FakeCatalogueRepository())

    async with http:
        await http.get(ROUTES[0], headers={"X-Client-IP": "203.0.113.7"})

    assert isinstance(limiter, FakeRateLimiter)
    assert limiter.hits == [
        ("public_catalogue:ip:203.0.113.7", PUBLIC_CATALOGUE_POLICY)
    ]


async def test_requests_over_the_limit_get_429_and_no_cache_header() -> None:
    http, _ = build(FakeCatalogueRepository())

    async with http:
        responses = [
            await http.get(ROUTES[0]) for _ in range(PUBLIC_CATALOGUE_POLICY.limit + 1)
        ]

    assert responses[-1].status_code == 429
    assert "Cache-Control" not in responses[-1].headers
    assert "Retry-After" in responses[-1].headers
    assert responses[-2].status_code == 200


async def test_rate_limiter_outage_fails_open() -> None:
    http, _ = build(FakeCatalogueRepository(), RaisingRateLimiter())

    async with http:
        response = await http.get(ROUTES[0])

    assert response.status_code == 200


@pytest.mark.parametrize("path", ROUTES)
async def test_database_error_is_an_uncached_500(path: str) -> None:
    http, _ = build(FakeCatalogueRepository(error=RuntimeError("database is down")))

    async with http:
        response = await http.get(path)

    assert response.status_code == 500
    assert response.json() == {"success": False, "error": "Internal server error"}
    assert "Cache-Control" not in response.headers
    assert "ETag" not in response.headers


# --- privacy ----------------------------------------------------------------


def _field_names(model: Any) -> set[str]:
    names: set[str] = set()
    for field_name, field in model.model_fields.items():
        names.add(field_name)
        annotation = getattr(field.annotation, "__args__", (field.annotation,))
        for candidate in (field.annotation, *annotation):
            if hasattr(candidate, "model_fields"):
                names |= _field_names(candidate)
    return names


def test_public_offer_schema_exposes_no_editorial_only_field() -> None:
    names = (
        _field_names(OfferData)
        | _field_names(OfferDetailData)
        | _field_names(ExpiredOfferResponse)
    )

    assert names.isdisjoint(EDITORIAL_ONLY)
    assert _field_names(CategoryData).isdisjoint(EDITORIAL_ONLY)
    assert _field_names(MerchantData).isdisjoint(EDITORIAL_ONLY)
    assert "category_id" not in names
    assert "merchant_id" not in names


def test_public_offer_schema_is_an_explicit_allowlist() -> None:
    list_fields = set(OfferData.model_fields)
    assert "earns_commission" not in list_fields
    assert set(OfferDetailData.model_fields) == list_fields | {"earns_commission"}
    assert list_fields == {
        "id",
        "slug",
        "title",
        "summary",
        "description",
        "category",
        "merchant",
        "offer_type",
        "sponsored",
        "featured",
        "image",
        "postage",
        "requires_card",
        "subscription_terms",
        "eligibility",
        "availability",
        "start_at",
        "end_at",
        "last_verified_at",
        "freshness",
        "steps",
    }


def test_the_offer_record_the_routes_build_from_has_no_editorial_only_field() -> None:
    assert set(OfferRecord.__dataclass_fields__).isdisjoint(
        EDITORIAL_ONLY - {"status", "commercial_basis"}
    )


async def test_responses_never_contain_editorial_field_names_or_values() -> None:
    repo = FakeCatalogueRepository(offers=[make_offer()], by_slug=make_offer())
    http, _ = build(repo)

    async with http:
        bodies = [(await http.get(path)).text for path in ROUTES]

    for text in bodies:
        for name in EDITORIAL_ONLY:
            assert name not in text


def test_category_merchant_and_expired_schemas_are_literal_allowlists() -> None:
    assert set(CategoryData.model_fields) == CATEGORY_FIELDS
    assert set(MerchantData.model_fields) == {"slug", "name", "logo_url"}
    assert set(CategoryRef.model_fields) == {"slug", "name"}
    assert set(ExpiredOfferData.model_fields) == {
        "slug",
        "title",
        "category",
        "ended_at",
    }
    assert set(ExpiredOfferResponse.model_fields) == {
        "success",
        "error",
        "expired",
        "data",
    }


def test_the_expired_stub_never_carries_the_verification_date() -> None:
    assert "last_verified_at" not in _field_names(ExpiredOfferResponse)


async def test_unverified_expired_offer_answers_the_same_404_as_an_unknown_slug() -> (
    None
):
    unknown, _ = build(FakeCatalogueRepository())
    unverified, _ = build(
        FakeCatalogueRepository(
            by_slug=make_offer(status=OfferStatus.EXPIRED, last_verified_at=None)
        )
    )

    async with unknown, unverified:
        missing = await unknown.get("/v1/catalogue/offers/x")
        hidden = await unverified.get("/v1/catalogue/offers/x")

    assert hidden.status_code == 404
    assert hidden.content == missing.content


async def test_a_410_is_not_turned_into_a_304_by_a_matching_if_none_match() -> None:
    offer = make_offer(end_at=NOW - timedelta(days=1))
    http, _ = build(FakeCatalogueRepository(by_slug=offer))

    async with http:
        first = await http.get("/v1/catalogue/offers/free-shampoo")
        second = await http.get(
            "/v1/catalogue/offers/free-shampoo",
            headers={"If-None-Match": first.headers["ETag"]},
        )

    assert second.status_code == 410
    assert second.json() == first.json()
    assert second.json()["expired"] is True


@pytest.mark.parametrize(
    ("basis", "sponsored"),
    [
        (CommercialBasis.EDITORIAL, False),
        (CommercialBasis.AFFILIATE, False),
        (CommercialBasis.SPONSORED, True),
    ],
)
async def test_an_offer_is_sponsored_only_when_its_basis_is_sponsored(
    basis: CommercialBasis, sponsored: bool
) -> None:
    offer = make_offer(commercial_basis=basis)
    http, _ = build(FakeCatalogueRepository(offers=[offer], by_slug=offer))

    async with http:
        listed = (await http.get("/v1/catalogue/offers")).json()["data"]["items"][0]
        detail = (await http.get("/v1/catalogue/offers/free-shampoo")).json()["data"]

    assert listed["sponsored"] is sponsored
    assert detail["sponsored"] is sponsored


async def test_a_featured_sponsored_offer_is_both_on_the_card() -> None:
    offer = make_offer(commercial_basis=CommercialBasis.SPONSORED, featured=True)
    http, _ = build(FakeCatalogueRepository(offers=[offer]))

    async with http:
        item = (await http.get("/v1/catalogue/offers")).json()["data"]["items"][0]

    assert (item["featured"], item["sponsored"]) == (True, True)


@pytest.mark.parametrize("basis", list(CommercialBasis))
async def test_the_three_way_basis_never_reaches_the_public_response(
    basis: CommercialBasis,
) -> None:
    offer = make_offer(commercial_basis=basis)
    http, _ = build(FakeCatalogueRepository(offers=[offer], by_slug=offer))

    async with http:
        bodies = [(await http.get(path)).text for path in ROUTES]

    for text in bodies:
        assert "commercial_basis" not in text
        assert not {"EDITORIAL", "AFFILIATE", "SPONSORED"} & set(
            text.replace('"', " ").replace(",", " ").split()
        )
