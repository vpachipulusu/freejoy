"""The partner offer routes over HTTP, with a fake service: status codes, the fixed error set, the shape checks (FREEJ-58).

The service itself is ``test_partner_submission_service.py``; the whole stack on Postgres is the ``*_e2e.py`` suites.
"""

import json
from datetime import UTC, datetime
from typing import Any, get_args

import pytest
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.core.config import settings
from app.main import app
from app.partner import offer_routes
from app.partner.errors import (
    REFUSAL_CODES,
    SubmissionBusy,
    SubmissionNotFound,
    SubmissionRefused,
)
from app.partner.schemas import (
    EndResult,
    SubmissionResult,
    SubmissionsPage,
    SubmissionView,
)
from tests.api.partner.support import SURFACE_HEADERS, RawResponse, call_raw
from tests.api.partner.test_partner_errors import FIXED_MESSAGES
from tests.partner_world import PartnerWorld

NOW = datetime(2026, 6, 1, tzinfo=UTC)
BODY: dict[str, Any] = {
    "title": "Free sample",
    "summary": "A sample",
    "offer_type": "FREE_SAMPLE",
    "category": "home",
    "url": "https://shop.example.com/x",
    "postage_kind": "FREE",
    "requires_card": False,
    "subscription_terms": None,
    "eligibility": "UK",
    "availability": "Stocks",
    "start_at": None,
    "end_at": None,
}
MARKER = "zz-marker-77"


def view(ref: str = "sku-1", status: str = "pending_review") -> SubmissionView:
    return SubmissionView(
        ref=ref,
        status=status,  # type: ignore[arg-type]
        title="Free sample",
        public_url=None,
        update_pending=False,
        submitted_at=NOW,
        ends_at=None,
    )


class FakeService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.raises: Exception | None = None
        self.outcome = "created"

    def _maybe_raise(self) -> None:
        if self.raises is not None:
            raise self.raises

    async def submit(self, ref: str, body: Any) -> SubmissionResult:
        self.calls.append(("submit", (ref, body.title)))
        self._maybe_raise()
        return SubmissionResult(**view(ref).model_dump(), outcome=self.outcome)  # type: ignore[arg-type]

    async def get(self, ref: str) -> SubmissionView:
        self.calls.append(("get", (ref,)))
        self._maybe_raise()
        return view(ref)

    async def list_submissions(self, limit: int, offset: int) -> SubmissionsPage:
        self.calls.append(("list", (limit, offset)))
        self._maybe_raise()
        return SubmissionsPage(
            offers=[view()], total_count=1, limit=limit, offset=offset
        )

    async def end(self, ref: str) -> EndResult:
        self.calls.append(("end", (ref,)))
        self._maybe_raise()
        return EndResult(**view(ref, "ended").model_dump(), changed=True)


@pytest.fixture
def service(monkeypatch: pytest.MonkeyPatch, world: PartnerWorld) -> FakeService:
    fake = FakeService()
    monkeypatch.setattr(settings, "PARTNER_API_ENABLED", True)
    monkeypatch.setattr(
        offer_routes, "build_submission_service", lambda session, context: fake
    )
    return fake


async def call(
    world: PartnerWorld, method: str, path: str, body: Any = None
) -> RawResponse:
    headers = world.headers()
    chunks: tuple[bytes, ...] = (b"",)
    query = ""
    path, _, query = path.partition("?")
    if body is not None:
        headers.append(("content-type", "application/json"))
        chunks = (json.dumps(body).encode(),)
    return await call_raw(
        app,
        method,
        f"/partner/v1{path}",
        headers=headers,
        chunks=chunks,
        query_string=query,
    )


async def test_a_created_submission_is_a_201_with_the_envelope(
    world: PartnerWorld, service: FakeService
) -> None:
    response = await call(world, "PUT", "/offers/sku-1", BODY)

    assert response.status == 201
    assert response.json()["success"] is True
    assert response.json()["data"]["outcome"] == "created"
    assert service.calls == [("submit", ("sku-1", "Free sample"))]


@pytest.mark.parametrize("outcome", ["refreshed", "pending_update", "unchanged"])
async def test_any_other_outcome_is_a_200(
    world: PartnerWorld, service: FakeService, outcome: str
) -> None:
    service.outcome = outcome

    response = await call(world, "PUT", "/offers/sku-1", BODY)

    assert (response.status, response.json()["data"]["outcome"]) == (200, outcome)


async def test_every_response_carries_the_surface_headers_and_no_cookie(
    world: PartnerWorld, service: FakeService
) -> None:
    for method, path, body in (
        ("PUT", "/offers/sku-1", BODY),
        ("GET", "/offers", None),
        ("GET", "/offers/sku-1", None),
        ("POST", "/offers/sku-1/end", None),
    ):
        response = await call(world, method, path, body)

        assert {
            k: v for k, v in response.headers if k in SURFACE_HEADERS
        } == SURFACE_HEADERS
        assert "set-cookie" not in response.header_names()
        assert not any(
            name.startswith("access-control") for name in response.header_names()
        )


@pytest.mark.parametrize("code", get_args(REFUSAL_CODES))
async def test_a_refusal_is_the_fixed_message_and_status_for_its_code_and_echoes_nothing(
    world: PartnerWorld, service: FakeService, code: str
) -> None:
    service.raises = SubmissionRefused(code)

    response = await call(world, "PUT", "/offers/sku-1", {**BODY, "title": MARKER})

    body = response.json()
    assert set(body) == {"success", "error", "code"}
    assert (body["success"], body["code"]) == (False, code)
    assert body["error"] in FIXED_MESSAGES
    assert response.status == SubmissionRefused(code).status_code
    assert MARKER not in response.body.decode() + repr(response.headers)


async def test_an_unknown_ref_is_the_same_404_as_an_unknown_path(
    world: PartnerWorld, service: FakeService
) -> None:
    service.raises = SubmissionNotFound()

    for method, path in (("GET", "/offers/nope"), ("POST", "/offers/nope/end")):
        response = await call(world, method, path)
        unknown_path = await call(world, method, "/nothing-here")

        assert response.status == 404
        assert response.body == unknown_path.body
        assert response.header_names() == unknown_path.header_names() - {
            "x-request-id"
        } | (response.header_names() & {"x-request-id"})


async def test_a_busy_database_is_a_503_with_a_retry_after(
    world: PartnerWorld, service: FakeService
) -> None:
    service.raises = SubmissionBusy()

    response = await call(world, "PUT", "/offers/sku-1", BODY)

    assert response.status == 503
    assert response.json() == {
        "success": False,
        "error": "Service temporarily unavailable",
    }
    assert response.header("retry-after") is not None


class FakeDriverError(Exception):
    def __init__(self, sqlstate: str) -> None:
        super().__init__("driver")
        self.sqlstate = sqlstate


@pytest.mark.parametrize("sqlstate", ["55P03", "57014", "40P01", "08006"])
async def test_a_lock_timeout_or_any_other_database_failure_is_a_fixed_503(
    world: PartnerWorld, service: FakeService, sqlstate: str
) -> None:
    service.raises = DBAPIError("stmt", {}, FakeDriverError(sqlstate))

    response = await call(world, "PUT", "/offers/sku-1", BODY)

    assert response.status == 503
    assert response.json() == {
        "success": False,
        "error": "Service temporarily unavailable",
    }
    assert response.header("retry-after") is not None
    assert "stmt" not in response.body.decode()


async def test_a_constraint_the_service_did_not_handle_is_a_500_not_a_misleading_503(
    world: PartnerWorld, service: FakeService, caplog: pytest.LogCaptureFixture
) -> None:
    service.raises = IntegrityError(
        "INSERT", {"title": MARKER}, FakeDriverError("23514")
    )

    response = await call(world, "PUT", "/offers/sku-1", BODY)

    assert response.status == 500
    assert response.json() == {"success": False, "error": "Internal server error"}
    assert any("hit a constraint" in r.getMessage() for r in caplog.records)


async def test_a_body_that_is_not_the_documented_shape_is_a_plain_422_and_the_service_is_not_called(
    world: PartnerWorld, service: FakeService
) -> None:
    for bad in (
        {**BODY, "extra": 1},
        {**BODY, "offer_type": "CASHBACK"},
        {**BODY, "requires_card": "true"},
        {k: v for k, v in BODY.items() if k != "eligibility"},
        {**BODY, "title": MARKER * 100},
    ):
        response = await call(world, "PUT", "/offers/sku-1", bad)

        assert response.status == 422
        assert response.json() == {"success": False, "error": "Invalid request"}
        assert MARKER not in response.body.decode()
    assert service.calls == []


@pytest.mark.parametrize("ref", ["a b", "a" * 65, "a;b", "a%2Fb", "é"])
async def test_a_ref_outside_the_pattern_is_a_422_before_the_service(
    world: PartnerWorld, service: FakeService, ref: str
) -> None:
    response = await call(world, "GET", f"/offers/{ref}")

    assert response.status == 422
    assert service.calls == []


@pytest.mark.parametrize(
    ("query", "expected"), [("", (20, 0)), ("?limit=5&offset=10", (5, 10))]
)
async def test_list_passes_the_page_to_the_service(
    world: PartnerWorld, service: FakeService, query: str, expected: tuple[int, int]
) -> None:
    response = await call(world, "GET", f"/offers{query}")

    assert response.status == 200
    assert service.calls == [("list", expected)]
    assert response.json()["data"]["total_count"] == 1


@pytest.mark.parametrize(
    "query", ["?limit=0", "?limit=101", "?offset=-1", "?limit=x", "?offset=1000001"]
)
async def test_list_refuses_a_page_outside_the_bounds(
    world: PartnerWorld, service: FakeService, query: str
) -> None:
    response = await call(world, "GET", f"/offers{query}")

    assert response.status == 422
    assert service.calls == []


async def test_end_answers_with_what_changed(
    world: PartnerWorld, service: FakeService
) -> None:
    response = await call(world, "POST", "/offers/sku-1/end")

    assert response.status == 200
    assert response.json()["data"]["changed"] is True
    assert response.json()["data"]["status"] == "ended"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("DELETE", "/offers/sku-1"),
        ("PATCH", "/offers/sku-1"),
        ("POST", "/offers/sku-1"),
        ("PUT", "/offers"),
        ("GET", "/offers/sku-1/end"),
    ],
)
async def test_other_methods_are_a_405(
    world: PartnerWorld, service: FakeService, method: str, path: str
) -> None:
    response = await call(
        world, method, path, {} if method in ("PUT", "POST", "PATCH") else None
    )

    assert response.status == 405
    assert response.json() == {"success": False, "error": "Method Not Allowed"}


async def test_without_a_key_every_offer_route_is_the_same_401(
    service: FakeService,
) -> None:
    for method, path in (
        ("PUT", "/offers/a"),
        ("GET", "/offers"),
        ("GET", "/offers/a"),
        ("POST", "/offers/a/end"),
    ):
        response = await call_raw(app, method, f"/partner/v1{path}")

        assert response.status == 401
        assert response.json() == {"success": False, "error": "Unauthorized"}
    assert service.calls == []
