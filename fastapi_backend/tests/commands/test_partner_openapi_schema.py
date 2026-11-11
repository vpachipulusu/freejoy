"""The partner OpenAPI document and its freshness check (ADR-013).

The partner contract is its own committed file, ``docs/partner/openapi.json``. Every path is under
``/partner/v1/``; the generator is also exercised on a throwaway sub-app with one more route, to prove
the check stays meaningful as endpoints are added.
"""

import json
from pathlib import Path

import pytest

from app.main import app
from app.partner.app import create_partner_api
from app.partner.schema import partner_openapi_schema
from app.partner.surface import PARTNER_PREFIX
from commands import check_partner_openapi_schema as checker
from commands import generate_partner_openapi_schema as generator

REPO_ROOT = Path(__file__).resolve().parents[3]
PARTNER_SCHEMA = REPO_ROOT / "docs" / "partner" / "openapi.json"
INTERNAL_SCHEMA = REPO_ROOT / "nextjs-frontend" / "openapi.json"


def _api_with_a_route():
    api = create_partner_api()

    @api.get("/things/{thing_id}", tags=["things"])
    async def read_thing(thing_id: str) -> dict[str, str]:
        return {"id": thing_id}

    return api


def test_every_path_of_the_committed_partner_schema_is_under_the_prefix() -> None:
    schema = json.loads(PARTNER_SCHEMA.read_text(encoding="utf-8"))

    assert all(path.startswith(f"{PARTNER_PREFIX}/") for path in schema["paths"])


def test_the_partner_schema_has_its_own_title_and_version() -> None:
    schema = json.loads(PARTNER_SCHEMA.read_text(encoding="utf-8"))

    assert schema["info"] == {"title": "Freejoy Partner API", "version": "1.0.0"}


def test_paths_are_written_as_a_retailer_calls_them() -> None:
    api = _api_with_a_route()

    schema = partner_openapi_schema(api)

    real = list(partner_openapi_schema(create_partner_api())["paths"])
    assert "/partner/v1/me" in real
    assert list(schema["paths"]) == [*real, "/partner/v1/things/{thing_id}"]
    assert all(path.startswith(f"{PARTNER_PREFIX}/") for path in schema["paths"])


def test_asking_twice_does_not_prefix_twice_or_change_the_cached_schema() -> None:
    api = _api_with_a_route()

    first = partner_openapi_schema(api)
    second = partner_openapi_schema(api)

    assert first == second
    assert list(api.openapi()["paths"])[-1] == "/things/{thing_id}"
    assert not any(path.startswith("/partner") for path in api.openapi()["paths"])


def test_the_internal_schema_has_no_partner_path() -> None:
    committed = json.loads(INTERNAL_SCHEMA.read_text(encoding="utf-8"))["paths"]
    live = app.openapi()["paths"]

    assert [p for p in committed if p.lower().startswith("/partner")] == []
    assert [p for p in live if p.lower().startswith("/partner")] == []


def test_the_internal_schema_file_does_not_mention_the_partner_surface() -> None:
    """The partner surface (its prefix, its header, its sub-app) is not in the internal schema. The only
    "partner" paths are the admin ones that issue keys (FREEJ-57): they live under the merchants."""
    text = INTERNAL_SCHEMA.read_text(encoding="utf-8")
    paths = json.loads(text)["paths"]

    assert "/partner/v1" not in text.lower()
    components = {
        name
        for name in json.loads(text)["components"]["schemas"]
        if "partner" in name.lower()
    }
    assert components == {
        "CreatePartnerKeyRequest",
        "IssuedPartnerKey",
        "IssuedPartnerKeyResponse",
        "PartnerKeyOut",
        "PartnerKeyResponse",
        "PartnerKeysResponse",
    }
    assert "x-freejoy-surface" not in text.lower()
    assert "Freejoy Partner API" not in text
    partner_paths = [p for p in paths if "partner" in p.lower()]
    assert partner_paths
    assert all(
        p.startswith("/v1/admin/merchants/{merchant_id}/partner-keys")
        for p in partner_paths
    )


def test_the_generator_writes_lf_json_that_ends_with_a_newline(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "openapi.json"

    generator.generate_partner_openapi_schema(target)

    raw = target.read_bytes()
    assert b"\r" not in raw
    assert raw.endswith(b"\n")
    assert list(json.loads(raw)["paths"]) == list(
        partner_openapi_schema(create_partner_api())["paths"]
    )


def test_the_committed_partner_schema_is_what_the_generator_writes(
    tmp_path: Path,
) -> None:
    target = tmp_path / "openapi.json"

    generator.generate_partner_openapi_schema(target)

    assert target.read_bytes() == PARTNER_SCHEMA.read_bytes()


def test_the_check_passes_on_the_committed_schema(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert checker.main() == 0
    assert "up to date" in capsys.readouterr().out


def test_the_check_fails_on_a_stale_schema(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stale = tmp_path / "openapi.json"
    stale.write_text(
        json.dumps({"openapi": "3.1.0", "info": {}, "paths": {"/partner/v1/old": {}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(checker, "SCHEMA_FILE", stale)

    assert checker.main() == 1
    assert "out of date" in capsys.readouterr().out


def test_the_check_fails_when_the_schema_file_is_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(checker, "SCHEMA_FILE", tmp_path / "missing.json")

    assert checker.main() == 1
    assert "missing" in capsys.readouterr().out


def test_the_generator_writes_identical_copies_when_given_them(tmp_path: Path) -> None:
    target = tmp_path / "openapi.json"
    copy = tmp_path / "public" / "partner-openapi.json"

    generator.generate_partner_openapi_schema(target, copy)

    assert copy.read_bytes() == target.read_bytes()


def test_the_committed_frontend_copy_is_identical_to_the_committed_schema() -> None:
    copy = REPO_ROOT / "nextjs-frontend" / "public" / "partner-openapi.json"

    assert copy.read_bytes() == PARTNER_SCHEMA.read_bytes()


def test_the_check_fails_when_the_frontend_copy_differs_or_is_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    differing = tmp_path / "copy.json"
    differing.write_text("{}", encoding="utf-8")

    for copy in (differing, tmp_path / "missing.json"):
        monkeypatch.setattr(checker, "PUBLIC_COPY_FILE", copy)

        assert checker.main() == 1
        assert "partner-openapi.json is missing or differs" in capsys.readouterr().out
