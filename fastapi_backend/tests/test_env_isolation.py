"""``tests/_env_isolation.py``: what the suite does to the inherited environment before importing the app."""

from app.core.config import Settings
from tests._env_isolation import (
    TEST_ENVIRONMENT,
    TEST_SETTINGS,
    isolate_settings_environment,
    settings_field_names,
)


def test_the_field_names_match_the_settings_class() -> None:
    # The names are read from the source because the class cannot be imported before the
    # environment is clean; this keeps that reading honest.
    assert settings_field_names() == frozenset(Settings.model_fields)


def test_an_exported_setting_is_removed_and_replaced_by_the_test_value() -> None:
    environment = {"APP_NAME": "Shell Brand", "SENTRY_DSN": "https://real@sentry.io/1"}

    isolate_settings_environment(environment)

    assert "APP_NAME" not in environment
    assert "SENTRY_DSN" not in environment


def test_the_test_settings_are_pinned_over_anything_exported() -> None:
    environment = {"ENVIRONMENT": "production", "DATABASE_URL": "postgresql://real/db"}

    isolate_settings_environment(environment)

    assert environment["ENVIRONMENT"] == TEST_ENVIRONMENT
    assert environment["DATABASE_URL"] == TEST_SETTINGS["DATABASE_URL"]


def test_variables_that_are_not_settings_are_kept() -> None:
    environment = {
        "TEST_DATABASE_URL": "postgresql://localhost/x_test",
        "PYTEST_XDIST_WORKER": "gw3",
        "PATH": "/bin",
    }

    isolate_settings_environment(environment)

    assert environment["TEST_DATABASE_URL"] == "postgresql://localhost/x_test"
    assert environment["PYTEST_XDIST_WORKER"] == "gw3"
    assert environment["PATH"] == "/bin"


def test_the_direct_migration_url_is_removed() -> None:
    environment = {"DATABASE_URL_DIRECT": "postgresql://real/db"}

    isolate_settings_environment(environment)

    assert "DATABASE_URL_DIRECT" not in environment


def test_the_env_file_switch_is_turned_on() -> None:
    environment: dict[str, str] = {}

    isolate_settings_environment(environment)

    assert environment["IGNORE_ENV_FILE"] == "1"
