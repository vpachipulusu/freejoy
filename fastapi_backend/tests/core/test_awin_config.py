"""Startup validation of the Awin importer settings (FREEJ-46). Neither is required by the app."""

import pytest
from pydantic import SecretStr, ValidationError

from app.core.config import Settings

FAKE_TOKEN = "fake-test-token-0123456789"


def load(monkeypatch: pytest.MonkeyPatch, **env: str) -> Settings:
    for name in ("AWIN_PUBLISHER_ID", "AWIN_OAUTH_API_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return Settings(_env_file=None)  # type: ignore[call-arg]


def test_both_settings_are_optional(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load(monkeypatch)

    assert settings.AWIN_PUBLISHER_ID is None
    assert settings.AWIN_OAUTH_API_TOKEN is None


def test_blank_values_copied_from_the_example_file_mean_not_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = load(monkeypatch, AWIN_PUBLISHER_ID="", AWIN_OAUTH_API_TOKEN="")

    assert settings.AWIN_PUBLISHER_ID is None
    assert settings.AWIN_OAUTH_API_TOKEN is None


def test_the_publisher_id_is_a_positive_integer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert load(monkeypatch, AWIN_PUBLISHER_ID="12345").AWIN_PUBLISHER_ID == 12345


@pytest.mark.parametrize("value", ["0", "-5", "abc", "12.5", "1e3"])
def test_a_publisher_id_that_is_not_a_positive_integer_stops_startup(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    with pytest.raises(ValidationError):
        load(monkeypatch, AWIN_PUBLISHER_ID=value)


def test_the_token_is_a_secret_that_never_prints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = load(monkeypatch, AWIN_OAUTH_API_TOKEN=FAKE_TOKEN)

    token = settings.AWIN_OAUTH_API_TOKEN
    assert isinstance(token, SecretStr)
    assert token.get_secret_value() == FAKE_TOKEN
    assert FAKE_TOKEN not in repr(settings)
    assert FAKE_TOKEN not in str(token)


def test_surrounding_whitespace_is_trimmed(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load(monkeypatch, AWIN_OAUTH_API_TOKEN=f"  {FAKE_TOKEN}\n")

    assert settings.AWIN_OAUTH_API_TOKEN is not None
    assert settings.AWIN_OAUTH_API_TOKEN.get_secret_value() == FAKE_TOKEN


@pytest.mark.parametrize("value", ["two words", "tab\tinside", "line\nbreak"])
def test_a_token_that_could_not_be_a_header_value_is_refused_without_echoing_it(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    with pytest.raises(ValidationError) as caught:
        load(monkeypatch, AWIN_OAUTH_API_TOKEN=value)

    assert "AWIN_OAUTH_API_TOKEN" in str(caught.value)
    assert value not in str(caught.value)
