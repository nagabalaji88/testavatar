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

import sqlalchemy as sa
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


# The first revision. A database carrying this schema is at this revision
# whether or not anything ever recorded that.
BASELINE_REVISION = "4e794452fecc"

# Tables the pre-Alembic create_all path produced. Any one of them means the
# database already holds the baseline schema.
BASELINE_TABLES = frozenset(
    {
        "users",
        "provider_credentials",
        "prompt_runs",
        "prompt_candidates",
        "consensus_prompts",
        "execution_logs",
    }
)


def _adopt_pre_alembic_schema(connection: Connection) -> None:
    """Record the baseline for a database that predates this environment.

    Every schema created before the migration environment existed was built by
    create_all, so it has all six tables and no alembic_version. Alembic reads
    that as "no revision applied" and replays the baseline, whose first
    statement is CREATE TABLE provider_credentials -- which fails on a database
    that already has it, killing the container before the API ever starts.

    Stamping is correct rather than merely convenient here: the baseline was
    generated from these same models and verified to produce a schema identical
    to create_all's, so a database built either way genuinely is at this
    revision. Later revisions then apply on top normally.

    Deliberately narrow: it does nothing when alembic_version already exists,
    and nothing on an empty database, so the only case it touches is the one
    that would otherwise crash.
    """
    inspector = sa.inspect(connection)
    tables = set(inspector.get_table_names())

    if "alembic_version" in tables:
        return
    if not (tables & BASELINE_TABLES):
        return

    # Alembic's own version table definition, created here rather than through
    # the migration context so the stamp can be committed on its own before the
    # migration run begins.
    connection.execute(
        sa.text(
            "CREATE TABLE alembic_version ("
            "version_num VARCHAR(32) NOT NULL, "
            "CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num))"
        )
    )
    connection.execute(
        sa.text("INSERT INTO alembic_version (version_num) VALUES (:rev)"),
        {"rev": BASELINE_REVISION},
    )


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
        # Committed on its own, before the migration run: the stamp is a row,
        # not DDL, so it would otherwise be discarded when the connection
        # closes -- leaving the adoption to repeat on every start.
        await connection.run_sync(_adopt_pre_alembic_schema)
        await connection.commit()
        await connection.run_sync(_run)
    await engine.dispose()


def run_migrations_online() -> None:
    asyncio.run(_run_async())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
