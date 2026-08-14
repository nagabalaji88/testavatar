"""Async SQLAlchemy engine, session factory and schema bootstrap."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

def _engine_kwargs() -> dict[str, object]:
    """Engine options differ by backend.

    SQLite has no server to pool connections to, and passing pool sizing to it
    raises. Keeping the single-file backend first-class is what allows the app
    to run with no database service at all.
    """
    if settings.database_url.startswith("sqlite"):
        Path(settings.sqlite_directory).mkdir(parents=True, exist_ok=True)
        return {"connect_args": {"check_same_thread": False, "timeout": 30}}

    return {
        "pool_pre_ping": True,
        "pool_size": settings.database_pool_size,
        "max_overflow": settings.database_max_overflow,
    }


engine = create_async_engine(settings.database_url, echo=False, **_engine_kwargs())


@event.listens_for(engine.sync_engine, "connect")
def _tune_sqlite(dbapi_connection: object, _record: object) -> None:
    """Make SQLite behave under concurrent async access.

    WAL lets the pipeline write while the API reads; without it the default
    rollback journal serialises them and readers hit 'database is locked'.
    """
    if not settings.database_url.startswith("sqlite"):
        return
    cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()

SessionFactory = async_sessionmaker(
    bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
)


async def init_database() -> None:
    """Create tables that do not exist yet.

    Alembic owns migrations in production; this keeps local and test
    environments usable without a migration step.
    """
    import app.models.domain  # noqa: F401  (register mappers before create_all)

    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    logger.info("database_schema_ready")


async def dispose_database() -> None:
    await engine.dispose()


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a transactional session."""
    async with SessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Session context manager for workers and background tasks."""
    async with SessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
