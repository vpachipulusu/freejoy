"""The switch that stops the local ``.env`` file from being read.

``Settings`` (``app/core/config.py``) and the scripts that call ``load_dotenv`` read a
git-ignored ``.env`` in the working directory. That is right for ``uvicorn`` and for
the management commands, but the test suite and the OpenAPI schema commands must give the
same result on every machine, so they turn the file off before anything imports the
settings by setting ``IGNORE_ENV_FILE=1`` (see ``tests/_env_isolation.py`` and
``commands/generate_openapi_schema.py``). The variable is read from the process
environment, so child processes inherit it.

This module must stay free of imports from ``app`` so that Alembic can use it without
building the settings.
"""

import os
from pathlib import Path

IGNORE_ENV_FILE = "IGNORE_ENV_FILE"
ENV_FILE = ".env"
TRUTHY = frozenset({"1", "true", "yes", "on"})


def env_file_ignored() -> bool:
    return os.environ.get(IGNORE_ENV_FILE, "").strip().lower() in TRUTHY


def settings_env_file() -> str | None:
    """The ``env_file`` for ``Settings``: ``None`` while the switch is on."""
    return None if env_file_ignored() else ENV_FILE


def load_env_file(path: Path | None = None) -> bool:
    """Load ``.env`` into ``os.environ`` (existing variables win) unless the switch is on.

    Without ``path`` the file is looked up from this module's directory upwards, which is
    where ``fastapi_backend/.env`` lives. Returns whether a file was read.
    """
    if env_file_ignored():
        return False
    from dotenv import (
        load_dotenv,
    )  # only the scripts need it; keep it off the settings import path

    return load_dotenv(dotenv_path=path)
