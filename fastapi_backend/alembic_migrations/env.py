import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

import app.models.platform  # noqa: F401  (registers every table on Base.metadata)
from app.core.base import Base
from app.core.db_url import to_async_target
from app.core.env_file import load_env_file

load_env_file()

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# The platform metadata: every model under app/models/platform.
target_metadata = Base.metadata

# Migrations use the direct (unpooled) endpoint when DATABASE_URL_DIRECT is set,
# otherwise they fall back to DATABASE_URL (local and CI databases).
database_url = os.getenv("DATABASE_URL_DIRECT") or os.getenv("DATABASE_URL")

if not database_url:
    raise ValueError("DATABASE_URL (or DATABASE_URL_DIRECT) is not set!")

# Same connect args as the app engines (app.core.database.build_engine): TLS mode,
# statement cache off (PgBouncer-safe) and the bounded connect timeout.
db_target = to_async_target(database_url)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    context.configure(
        url=db_target.url.render_as_string(hide_password=False),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """In this scenario we need to create an Engine
    and associate a connection with the context.

    """

    connectable = create_async_engine(
        db_target.url,
        poolclass=pool.NullPool,
        connect_args=db_target.connect_args,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""

    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
