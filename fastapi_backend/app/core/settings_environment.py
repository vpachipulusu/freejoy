"""Make the process environment independent of the machine before the settings are built.

``app.core.config`` builds its settings singleton when it is first imported, from the process
environment and a git-ignored ``.env``. Both belong to whoever runs the code, so the test suite
(``tests/conftest.py``) and the OpenAPI schema commands (``commands/generate_openapi_schema.py``)
call :func:`isolate_settings_environment` before importing anything that imports the settings:
it turns the ``.env`` file off, removes every setting a shell exported and pins harmless
placeholders. Only these callers use it; no deployment or normal run does.

This module must not import ``app.core.config`` (or anything that does): the point is to run first.
"""

import ast
from collections.abc import MutableMapping
from pathlib import Path

from app.core.env_file import IGNORE_ENV_FILE

CONFIG_FILE = Path(__file__).with_name("config.py")

# Placeholders: nothing that uses them opens a database connection, signs a token or calls a
# provider. ENVIRONMENT=test also relaxes the production-only validators.
PLACEHOLDER_SETTINGS = {
    "ENVIRONMENT": "test",
    "BACKEND_INTERNAL_KEY": "test-only-internal-service-key-not-for-deployment",
    "FIREBASE_ADMIN_PROJECT_ID": "test-project",
    "FIREBASE_ADMIN_CLIENT_EMAIL": "test@example.invalid",
    "FIREBASE_ADMIN_PRIVATE_KEY": "test-only-placeholder-not-a-real-key",
    "RATE_LIMIT_EMAIL_KEY": "test-only-email-hmac-key-not-for-deployment",
    "SUBSCRIBER_TOKEN_SECRET": "test-only-subscriber-token-secret-0123456789",
    "DATABASE_URL": "postgresql+asyncpg://test:test@localhost:5432/test",
}

# Not settings fields, but Alembic prefers it over DATABASE_URL, so it can point at a real database.
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
    """Turn the ``.env`` file off, drop every inherited setting and pin the placeholders.

    Names are compared case-insensitively because pydantic-settings reads them that way.
    """
    environment[IGNORE_ENV_FILE] = "1"
    names = {name.lower() for name in settings_field_names() | OTHER_INHERITED_NAMES}
    for key in [key for key in environment if key.lower() in names]:
        del environment[key]
    environment.update(PLACEHOLDER_SETTINGS)
