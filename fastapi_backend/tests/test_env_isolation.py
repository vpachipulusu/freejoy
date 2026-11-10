"""``app/core/settings_environment.py``: what runs before the app is imported by the tests and the schema commands."""

from app.core.config import Settings
from app.core.settings_environment import (
    PLACEHOLDER_SETTINGS,
    isolate_settings_environment,
    settings_field_names,
)


def test_the_field_names_match_the_settings_class() -> None:
    # The names are read from the source because the class cannot be imported before the
    # environment is clean; this keeps that reading honest.
    assert settings_field_names() == frozenset(Settings.model_fields)


def test_settings_are_read_by_field_name_only() -> None:
    # The scrub removes names, so an alias or a prefix would let a variable slip through it.
    assert Settings.model_config.get("env_prefix", "") == ""
    for name, field in Settings.model_fields.items():
        assert field.alias is None, name
        assert field.validation_alias is None, name


def test_an_exported_setting_is_removed() -> None:
    environment = {"APP_NAME": "Shell Brand", "SENTRY_DSN": "https://real@sentry.io/1"}

    isolate_settings_environment(environment)

    assert "APP_NAME" not in environment
    assert "SENTRY_DSN" not in environment


def test_names_are_matched_case_insensitively() -> None:
    environment = {"sentry_dsn": "x", "Environment": "production", "app_name": "y"}

    isolate_settings_environment(environment)

    assert not {"sentry_dsn", "Environment", "app_name"} & set(environment)
    assert environment["ENVIRONMENT"] == "test"


def test_the_placeholders_are_pinned_over_anything_exported() -> None:
    environment = {"ENVIRONMENT": "production", "DATABASE_URL": "postgresql://real/db"}

    isolate_settings_environment(environment)

    for name, value in PLACEHOLDER_SETTINGS.items():
        assert environment[name] == value
    assert environment["ENVIRONMENT"] == "test"


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
    environment = {"database_url_direct": "postgresql://real/db"}

    isolate_settings_environment(environment)

    assert "database_url_direct" not in environment


def test_the_env_file_switch_is_turned_on() -> None:
    environment: dict[str, str] = {}

    isolate_settings_environment(environment)

    assert environment["IGNORE_ENV_FILE"] == "1"


def test_the_docs_switch_is_removed_so_a_shell_cannot_turn_the_docs_on() -> None:
    environment = {"API_DOCS_ENABLED": "true", "api_docs_enabled": "1"}

    isolate_settings_environment(environment)

    assert not {"API_DOCS_ENABLED", "api_docs_enabled"} & set(environment)
    assert "API_DOCS_ENABLED" in settings_field_names()
