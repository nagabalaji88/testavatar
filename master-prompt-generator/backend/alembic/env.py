"""Alembic environment, wired to the application's own configuration.

Two things are deliberate here:

* The URL comes from DATABASE_URL through `settings`, never from alembic.ini.
  A URL in the ini file drifts from the one the app uses, and the failure mode
  is migrating a database nobody is running against.

* The async engine is driven through `connection.run_sync`. The app's URL uses
  an async driver (asyncpg, aiosqlite), and Alembic's migration context is
  synchronous, so the two have to be bridged rather than the URL rewritten --
  rewriting it would mean migrations and the app connect through different
  drivers, with different type handling.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel

# Importing the models is what populates SQLModel.metadata; without it
# autogenerate sees an empty schema and helpfully proposes dropping every
# table. noqa because the import exists purely for that side effect.
import app.models.domain  # noqa: F401
from app.core.config import settings

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = SQLModel.metadata


def _url() -> str:
    return settings.database_url


def run_migrations_offline() -> None:
    """Emit SQL to stdout without connecting -- `alembic upgrade head --sql`."""
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _run(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # Off by default, and the reason a column widened from VARCHAR(50) to
        # VARCHAR(200) silently produces an empty revision.
        compare_type=True,
        # SQLite cannot ALTER most columns; batch mode rebuilds the table
        # instead, which keeps the single-file backend migratable.
        render_as_batch=_url().startswith("sqlite"),
    )
    with context.begin_transaction():
        context.run_migrations()


async def _run_async() -> None:
    engine = create_async_engine(_url(), poolclass=None)
    async with engine.connect() as connection:
        await connection.run_sync(_run)
    await engine.dispose()


def run_migrations_online() -> None:
    asyncio.run(_run_async())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
