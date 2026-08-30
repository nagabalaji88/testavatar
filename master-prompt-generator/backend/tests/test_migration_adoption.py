"""A database built before the migration environment existed must still boot.

Every schema created by the old create_all path has all six tables and no
alembic_version. Alembic reads that as "nothing applied" and replays the
baseline, whose first statement is CREATE TABLE provider_credentials -- which
fails on a database that already has it. The entrypoint runs migrations under
`set -e`, so the container exited before uvicorn ever started and Docker
restarted it forever: a crash loop whose only visible symptom was an API that
never came up.

It was missed because the migration environment was only ever verified against
a fresh database, where the baseline has nothing to collide with.
"""

from __future__ import annotations

import os
import pathlib
import sqlite3
import subprocess
import sys

import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
BASELINE = "4e794452fecc"


def _env(db: pathlib.Path) -> dict[str, str]:
    return {
        **os.environ,
        "DATABASE_URL": f"sqlite+aiosqlite:///{db}",
        "ENVIRONMENT": "local",
        "JWT_SECRET_KEY": "a-sufficiently-long-random-secret-0123456789abcd",
    }


def _upgrade(db: pathlib.Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        env=_env(db),
        cwd=BACKEND,
        capture_output=True,
        text=True,
    )


def _build_with_create_all(db: pathlib.Path) -> None:
    """Reproduce the pre-Alembic schema exactly as the old code produced it."""
    script = (
        "import asyncio\n"
        "from sqlalchemy.ext.asyncio import create_async_engine\n"
        "from sqlmodel import SQLModel\n"
        "import app.models.domain\n"
        f"e = create_async_engine('sqlite+aiosqlite:///{db}')\n"
        "async def go():\n"
        "    async with e.begin() as c:\n"
        "        await c.run_sync(SQLModel.metadata.create_all)\n"
        "    await e.dispose()\n"
        "asyncio.run(go())\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", script], env=_env(db), cwd=BACKEND,
        capture_output=True, text=True,
    )
    assert done.returncode == 0, done.stderr


def _version(db: pathlib.Path) -> list[str] | None:
    conn = sqlite3.connect(db)
    try:
        return [r[0] for r in conn.execute("select version_num from alembic_version")]
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()


def _schema(db: pathlib.Path) -> list[str]:
    """Every object's DDL, less the bookkeeping table itself."""
    conn = sqlite3.connect(db)
    try:
        return sorted(
            row[0]
            for row in conn.execute(
                "select sql from sqlite_master where sql is not null "
                "and name not like 'sqlite_%' and name != 'alembic_version'"
            )
        )
    finally:
        conn.close()


@pytest.fixture
def legacy_db(tmp_path: pathlib.Path) -> pathlib.Path:
    db = tmp_path / "legacy.db"
    _build_with_create_all(db)
    assert _version(db) is None, "the fixture must reproduce the un-stamped state"
    return db


class TestAdoptingAPreAlembicDatabase:
    def test_it_migrates_instead_of_crash_looping(self, legacy_db: pathlib.Path) -> None:
        done = _upgrade(legacy_db)
        assert done.returncode == 0, (
            "upgrade head failed on a create_all database -- this is the "
            f"container crash loop:\n{done.stderr[-1500:]}"
        )
        assert _version(legacy_db) == [BASELINE]

    def test_the_stamp_survives_the_connection_closing(
        self, legacy_db: pathlib.Path
    ) -> None:
        """The stamp is a row, not DDL; uncommitted it vanishes and re-runs forever."""
        _upgrade(legacy_db)
        assert _version(legacy_db) == [BASELINE]
        again = _upgrade(legacy_db)
        assert again.returncode == 0
        assert _version(legacy_db) == [BASELINE]

    def test_adopting_does_not_hide_a_different_schema(
        self, legacy_db: pathlib.Path, tmp_path: pathlib.Path
    ) -> None:
        """Stamping is only honest if the two routes really do agree."""
        _upgrade(legacy_db)

        migrated = tmp_path / "migrated.db"
        migrated.touch()
        assert _upgrade(migrated).returncode == 0

        assert _schema(legacy_db) == _schema(migrated)

    def test_an_empty_database_still_runs_the_baseline(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Adoption must not swallow the case it looks superficially like."""
        fresh = tmp_path / "fresh.db"
        fresh.touch()
        assert _upgrade(fresh).returncode == 0
        assert _version(fresh) == [BASELINE]
        assert _schema(fresh), "the baseline created nothing"
