"""A production partner surface needs a public https ``PUBLIC_SITE_URL`` (ADR-013, FREEJ-59).

Every offer in a click report carries a link built from ``PUBLIC_SITE_URL``, which defaults to
``http://localhost:3000``. With the partner surface on in production, startup must refuse that default, and must not
quote the value it refused.
"""

import pytest
from pydantic import ValidationError

from app.core.config import Settings, _is_public_https_origin

PARTNER_URL = (
    "postgresql+asyncpg://freejoy_partner:placeholder@db.example.com:5432/yourapp"
)
OWNER_URL = "postgresql+asyncpg://owner:placeholder@db.example.com:5432/yourapp"


def production_with_the_partner_surface(monkeypatch: pytest.MonkeyPatch) -> None:
    """Everything else a production start needs, so only ``PUBLIC_SITE_URL`` decides."""
    for name, value in {
        "ENVIRONMENT": "production",
        "DATABASE_URL": OWNER_URL,
        "PARTNER_API_ENABLED": "true",
        "PARTNER_DATABASE_URL": PARTNER_URL,
        "UPSTASH_REDIS_REST_URL": "https://example.upstash.io",
        "UPSTASH_REDIS_REST_TOKEN": "placeholder-token",
        "BACKEND_INTERNAL_KEY": "k" * 40,
        "RATE_LIMIT_EMAIL_KEY": "e" * 40,
        "SUBSCRIBER_TOKEN_SECRET": "s" * 40,
        "FIREBASE_ADMIN_PROJECT_ID": "placeholder",
        "FIREBASE_ADMIN_CLIENT_EMAIL": "placeholder@example.com",
        "FIREBASE_ADMIN_PRIVATE_KEY": "placeholder",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("VERCEL_ENV", raising=False)


@pytest.mark.parametrize(
    "origin",
    ["http://localhost:3000", "https://localhost", "http://www.freejoy.example"],
)
def test_production_with_the_surface_on_refuses_a_local_or_plain_http_site_url(
    monkeypatch: pytest.MonkeyPatch, origin: str
) -> None:
    production_with_the_partner_surface(monkeypatch)
    monkeypatch.setenv("PUBLIC_SITE_URL", origin)

    with pytest.raises(
        ValidationError, match="PUBLIC_SITE_URL must be an https origin"
    ) as caught:
        Settings(_env_file=None)  # type: ignore[call-arg]

    assert origin not in str(caught.value)


def test_production_with_the_default_site_url_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    production_with_the_partner_surface(monkeypatch)
    monkeypatch.delenv("PUBLIC_SITE_URL", raising=False)

    with pytest.raises(
        ValidationError, match="PUBLIC_SITE_URL must be an https origin"
    ):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_production_with_a_public_https_site_url_starts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    production_with_the_partner_surface(monkeypatch)
    monkeypatch.setenv("PUBLIC_SITE_URL", "https://www.freejoy.example")

    assert Settings(_env_file=None).PARTNER_API_ENABLED is True  # type: ignore[call-arg]


def test_the_default_site_url_is_fine_while_the_surface_is_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    production_with_the_partner_surface(monkeypatch)
    monkeypatch.setenv("PARTNER_API_ENABLED", "false")
    monkeypatch.delenv("PUBLIC_SITE_URL", raising=False)

    assert Settings(_env_file=None).PARTNER_API_ENABLED is False  # type: ignore[call-arg]


def test_a_local_environment_may_keep_the_default_site_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("PARTNER_API_ENABLED", "true")
    monkeypatch.setenv("PARTNER_DATABASE_URL", PARTNER_URL)
    monkeypatch.delenv("PUBLIC_SITE_URL", raising=False)

    assert Settings(_env_file=None).PARTNER_API_ENABLED is True  # type: ignore[call-arg]


@pytest.mark.parametrize(
    ("origin", "expected"),
    [
        ("https://www.freejoy.example", True),
        ("https://freejoy.example/", True),
        ("https://8.8.8.8", True),
        ("http://www.freejoy.example", False),
        ("https://localhost:3000", False),
        ("https://LOCALHOST", False),
        ("https://app.localhost", False),
        ("https://127.0.0.1", False),
        ("https://[::1]", False),
        ("https://0.0.0.0", False),
        ("", False),
        ("https://", False),
        ("not a url", False),
    ],
)
def test_what_counts_as_a_public_https_origin(origin: str, expected: bool) -> None:
    assert _is_public_https_origin(origin) is expected
