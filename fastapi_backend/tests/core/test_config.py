import pytest
from pydantic import ValidationError

from app.core.config import Settings

PARTNER_URL = "postgresql+asyncpg://freejoy_partner:placeholder@localhost:5432/yourapp"


def test_production_requires_auth_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("VERCEL_ENV", raising=False)
    monkeypatch.setenv("UPSTASH_REDIS_REST_URL", "https://example.upstash.io")
    monkeypatch.setenv("UPSTASH_REDIS_REST_TOKEN", "test-token")
    monkeypatch.delenv("BACKEND_INTERNAL_KEY", raising=False)
    with pytest.raises(ValidationError, match="Production authentication requires"):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_short_internal_key_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BACKEND_INTERNAL_KEY", "short")
    with pytest.raises(ValidationError, match="at least 32 characters"):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_missing_database_url_fails_at_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(ValidationError, match="DATABASE_URL"):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_settings_load_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@localhost/db")

    assert Settings(_env_file=None).DATABASE_URL.endswith("/db")  # type: ignore[call-arg]


@pytest.mark.parametrize("environment", ["production", "preview", "staging"])
def test_deployed_environment_without_upstash_fails_startup(
    monkeypatch: pytest.MonkeyPatch, environment: str
) -> None:
    monkeypatch.setenv("ENVIRONMENT", environment)
    monkeypatch.delenv("UPSTASH_REDIS_REST_URL", raising=False)
    monkeypatch.delenv("UPSTASH_REDIS_REST_TOKEN", raising=False)

    with pytest.raises(ValidationError, match="UPSTASH_REDIS_REST_URL"):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_environment_defaults_to_production_and_requires_upstash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.delenv("UPSTASH_REDIS_REST_URL", raising=False)
    monkeypatch.delenv("UPSTASH_REDIS_REST_TOKEN", raising=False)

    with pytest.raises(ValidationError, match="UPSTASH_REDIS_REST_TOKEN"):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_deployed_environment_with_only_one_upstash_setting_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("UPSTASH_REDIS_REST_URL", "https://x.upstash.io")
    monkeypatch.delenv("UPSTASH_REDIS_REST_TOKEN", raising=False)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


@pytest.mark.parametrize("environment", ["local", "test"])
def test_local_and_test_start_without_upstash(
    monkeypatch: pytest.MonkeyPatch, environment: str
) -> None:
    monkeypatch.setenv("ENVIRONMENT", environment)
    monkeypatch.delenv("UPSTASH_REDIS_REST_URL", raising=False)
    monkeypatch.delenv("UPSTASH_REDIS_REST_TOKEN", raising=False)

    assert Settings(_env_file=None).UPSTASH_REDIS_REST_URL is None  # type: ignore[call-arg]


def test_production_starts_with_both_upstash_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("UPSTASH_REDIS_REST_URL", "https://x.upstash.io")
    monkeypatch.setenv("UPSTASH_REDIS_REST_TOKEN", "t")

    assert Settings(_env_file=None).ENVIRONMENT == "production"  # type: ignore[call-arg]


@pytest.fixture
def no_upstash(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    monkeypatch.delenv("UPSTASH_REDIS_REST_URL", raising=False)
    monkeypatch.delenv("UPSTASH_REDIS_REST_TOKEN", raising=False)
    return monkeypatch


@pytest.mark.parametrize("vercel_env", ["preview", "development"])
def test_vercel_preview_and_development_start_without_upstash(
    no_upstash: pytest.MonkeyPatch, vercel_env: str
) -> None:
    no_upstash.setenv("VERCEL_ENV", vercel_env)
    no_upstash.delenv("ENVIRONMENT", raising=False)  # defaults to production

    assert Settings(_env_file=None).UPSTASH_REDIS_REST_URL is None  # type: ignore[call-arg]


def test_vercel_production_without_upstash_fails_even_if_environment_is_local(
    no_upstash: pytest.MonkeyPatch,
) -> None:
    no_upstash.setenv("VERCEL_ENV", "production")
    no_upstash.setenv("ENVIRONMENT", "local")

    with pytest.raises(ValidationError, match="UPSTASH_REDIS_REST_URL"):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_vercel_production_starts_with_upstash(no_upstash: pytest.MonkeyPatch) -> None:
    no_upstash.setenv("VERCEL_ENV", "production")
    no_upstash.setenv("UPSTASH_REDIS_REST_URL", "https://x.upstash.io")
    no_upstash.setenv("UPSTASH_REDIS_REST_TOKEN", "t")

    assert Settings(_env_file=None).VERCEL_ENV == "production"  # type: ignore[call-arg]


def test_outside_vercel_the_default_environment_is_strict(
    no_upstash: pytest.MonkeyPatch,
) -> None:
    no_upstash.delenv("VERCEL_ENV", raising=False)
    no_upstash.delenv("ENVIRONMENT", raising=False)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_clock_skew_defaults_to_ten_seconds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FIREBASE_CLOCK_SKEW_SECONDS", raising=False)
    assert Settings(_env_file=None).FIREBASE_CLOCK_SKEW_SECONDS == 10  # type: ignore[call-arg]


@pytest.mark.parametrize("value", ["0", "60"])
def test_clock_skew_accepts_both_range_limits(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("FIREBASE_CLOCK_SKEW_SECONDS", value)
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.FIREBASE_CLOCK_SKEW_SECONDS == int(value)


@pytest.mark.parametrize("value", ["-1", "61", "ten"])
def test_clock_skew_outside_range_stops_startup(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("FIREBASE_CLOCK_SKEW_SECONDS", value)
    with pytest.raises(ValidationError, match="FIREBASE_CLOCK_SKEW_SECONDS"):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_a_validation_error_does_not_echo_the_input_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # pydantic prints a truncated repr of the whole input (head and tail), which can hold a
    # password or the start and end of a key. hide_input_in_errors drops it altogether.
    sentinel = "sentinel-secret-9f3a"
    monkeypatch.setenv("BACKEND_INTERNAL_KEY", sentinel)

    with pytest.raises(ValidationError, match="at least 32 characters") as error:
        Settings(_env_file=None)  # type: ignore[call-arg]

    assert sentinel not in str(error.value)
    assert "input_value" not in str(error.value)


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_blank_partner_switch_means_off(
    monkeypatch: pytest.MonkeyPatch, blank: str
) -> None:
    monkeypatch.setenv("PARTNER_API_ENABLED", blank)

    assert Settings(_env_file=None).PARTNER_API_ENABLED is False  # type: ignore[call-arg]


@pytest.mark.parametrize(
    ("raw", "expected"), [("true", True), ("false", False), ("1", True)]
)
def test_the_partner_switch_reads_booleans(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: bool
) -> None:
    monkeypatch.setenv("PARTNER_API_ENABLED", raw)
    monkeypatch.setenv(
        "PARTNER_DATABASE_URL", PARTNER_URL
    )  # required once the switch is on

    assert Settings(_env_file=None).PARTNER_API_ENABLED is expected  # type: ignore[call-arg]


@pytest.mark.parametrize("bad", ["maybe", "enabled", "2"])
def test_a_partner_switch_that_is_not_a_boolean_stops_startup(
    monkeypatch: pytest.MonkeyPatch, bad: str
) -> None:
    monkeypatch.setenv("PARTNER_API_ENABLED", bad)

    with pytest.raises(ValidationError, match="PARTNER_API_ENABLED"):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_the_partner_switch_is_off_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PARTNER_API_ENABLED", raising=False)

    assert Settings(_env_file=None).PARTNER_API_ENABLED is False  # type: ignore[call-arg]


def test_the_partner_switch_on_without_a_partner_database_url_stops_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PARTNER_API_ENABLED", "true")
    monkeypatch.delenv("PARTNER_DATABASE_URL", raising=False)

    with pytest.raises(ValidationError, match="PARTNER_DATABASE_URL must be set"):
        Settings(_env_file=None)  # type: ignore[call-arg]


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_blank_partner_database_url_counts_as_missing(
    monkeypatch: pytest.MonkeyPatch, blank: str
) -> None:
    monkeypatch.setenv("PARTNER_API_ENABLED", "true")
    monkeypatch.setenv("PARTNER_DATABASE_URL", blank)

    with pytest.raises(ValidationError, match="PARTNER_DATABASE_URL"):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_the_partner_database_url_is_not_needed_while_the_switch_is_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PARTNER_API_ENABLED", "false")
    monkeypatch.delenv("PARTNER_DATABASE_URL", raising=False)

    assert Settings(_env_file=None).PARTNER_DATABASE_URL is None  # type: ignore[call-arg]


def test_the_partner_database_url_may_not_be_the_owner_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PARTNER_API_ENABLED", "true")
    monkeypatch.setenv("DATABASE_URL", PARTNER_URL)
    monkeypatch.setenv("PARTNER_DATABASE_URL", PARTNER_URL)

    with pytest.raises(ValidationError, match="not DATABASE_URL"):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_the_partner_database_url_is_a_secret_and_stays_out_of_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PARTNER_API_ENABLED", "true")
    monkeypatch.setenv("PARTNER_DATABASE_URL", PARTNER_URL)

    loaded = Settings(_env_file=None)  # type: ignore[call-arg]

    assert "placeholder" not in repr(loaded)
    assert loaded.PARTNER_DATABASE_URL is not None
    assert loaded.PARTNER_DATABASE_URL.get_secret_value() == PARTNER_URL
