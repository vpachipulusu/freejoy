"""Keep the developer's machine out of the settings the tests see (FREEJ-45).

``app.core.config`` builds its settings singleton when it is first imported, and it
reads the process environment and a git-ignored ``.env``. Both belong to whoever runs the
tests, so ``tests/conftest.py`` calls :func:`isolate_settings_environment` before
importing anything from ``app``. It is plain Python on a mapping so it can be tested.

The test settings are the placeholders in ``TEST_SETTINGS``; the real values for the
Postgres-backed tests come from ``TEST_DATABASE_URL``, which is not a setting.
"""

import ast
from collections.abc import MutableMapping
from pathlib import Path

from app.core.env_file import IGNORE_ENV_FILE

CONFIG_FILE = Path(__file__).resolve().parent.parent / "app" / "core" / "config.py"
TEST_ENVIRONMENT = "test"

# Placeholders: the unit tests never open a database connection, sign a token or call a provider.
TEST_SETTINGS = {
    "ENVIRONMENT": TEST_ENVIRONMENT,
    "BACKEND_INTERNAL_KEY": "test-only-internal-service-key-not-for-deployment",
    "FIREBASE_ADMIN_PROJECT_ID": "test-project",
    "FIREBASE_ADMIN_CLIENT_EMAIL": "test@example.invalid",
    "FIREBASE_ADMIN_PRIVATE_KEY": "test-only-placeholder-not-a-real-key",
    "RATE_LIMIT_EMAIL_KEY": "test-only-email-hmac-key-not-for-deployment",
    "SUBSCRIBER_TOKEN_SECRET": "test-only-subscriber-token-secret-0123456789",
    "DATABASE_URL": "postgresql+asyncpg://test:test@localhost:5432/test",
}

# Not settings fields, but they point Alembic at a real database, so they are removed too.
OTHER_INHERITED_NAMES = frozenset({"DATABASE_URL_DIRECT"})


def settings_field_names() -> frozenset[str]:
    """The field names of ``Settings``, read from the source (the class cannot be imported yet)."""
    tree = ast.parse(CONFIG_FILE.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "Settings":
            return frozenset(
                statement.target.id
                for statement in node.body
                if isinstance(statement, ast.AnnAssign)
                and isinstance(statement.target, ast.Name)
            )
    raise RuntimeError(f"class Settings not found in {CONFIG_FILE}")


def isolate_settings_environment(environment: MutableMapping[str, str]) -> None:
    """Turn the ``.env`` file off, drop every inherited setting and pin the test settings."""
    environment[IGNORE_ENV_FILE] = "1"
    for name in settings_field_names() | OTHER_INHERITED_NAMES:
        environment.pop(name, None)
    environment.update(TEST_SETTINGS)
