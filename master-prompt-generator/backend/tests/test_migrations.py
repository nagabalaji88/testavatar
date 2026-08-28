"""The migration environment must stay in step with the models.

create_all creates absent tables and touches existing ones not at all -- no
added column, no widened type, no dropped index. On a fresh database that is
indistinguishable from a migration; on the second deploy it applies nothing and
the application runs against a schema that no longer matches its models,
failing at query time rather than at startup. Alembic owns the schema outside
local, and these tests exist so that ownership cannot silently lapse.
"""

from __future__ import annotations

import os
import subprocess
import sqlite3
from pathlib import Path

import pytest

from app.core.config import BACKEND_ROOT

VERSIONS = BACKEND_ROOT / "alembic" / "versions"


def _alembic(*args: str, url: str) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "ENVIRONMENT": "local",
        "DATABASE_URL": url,
        "PYTHONPATH": str(BACKEND_ROOT),
    }
    return subprocess.run(
        ["python", "-m", "alembic", *args],
        cwd=BACKEND_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )


def _schema(path: Path) -> dict[tuple[str, str], str]:
    """Every table and index, normalised, excluding Alembic's own bookkeeping."""
    connection = sqlite3.connect(path)
    try:
        rows = connection.execute(
            "SELECT type, name, sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' AND name != 'alembic_version' "
            "ORDER BY type, name"
        ).fetchall()
    finally:
        connection.close()
    return {(kind, name): " ".join((sql or "").split()) for kind, name, sql in rows}


class TestMigrationEnvironment:
    def test_a_migration_environment_exists(self) -> None:
        """alembic is a pinned dependency; for a long time nothing used it."""
        assert (BACKEND_ROOT / "alembic.ini").is_file()
        assert (BACKEND_ROOT / "alembic" / "env.py").is_file()
        assert VERSIONS.is_dir()

    def test_at_least_one_revision_is_committed(self) -> None:
        revisions = [p for p in VERSIONS.glob("*.py") if p.name != "__init__.py"]
        assert revisions, "no revision would leave every deployment with no schema"

    def test_the_url_is_not_hardcoded_in_the_ini(self) -> None:
        """A URL here drifts from the one the app uses, and carries a password."""
        ini = (BACKEND_ROOT / "alembic.ini").read_text(encoding="utf-8")
        for line in ini.splitlines():
            stripped = line.strip()
            if stripped.startswith("sqlalchemy.url") and "=" in stripped:
                assert not stripped.split("=", 1)[1].strip(), (
                    "sqlalchemy.url must stay empty; env.py reads DATABASE_URL"
                )

    def test_the_revision_template_imports_what_autogenerate_emits(self) -> None:
        """The models' JSONB columns render as postgresql.JSONB(astext_type=Text()).

        Without both imports a generated revision raises NameError partway
        through, having already created some of the tables.
        """
        template = (BACKEND_ROOT / "alembic" / "script.py.mako").read_text(
            encoding="utf-8"
        )
        assert "from sqlalchemy.dialects import postgresql" in template
        assert "from sqlalchemy import Text" in template


class TestMigrationsReproduceTheModels:
    """The point of the whole exercise: `upgrade head` == the models."""

    @pytest.mark.asyncio
    async def test_upgrade_head_builds_the_same_schema_as_create_all(
        self, tmp_path: Path
    ) -> None:
        migrated = tmp_path / "migrated.db"
        direct = tmp_path / "direct.db"

        result = _alembic("upgrade", "head", url=f"sqlite+aiosqlite:///{migrated}")
        assert result.returncode == 0, result.stderr

        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlmodel import SQLModel

        import app.models.domain  # noqa: F401  (registers the mappers)

        engine = create_async_engine(f"sqlite+aiosqlite:///{direct}")
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        await engine.dispose()

        from_migration, from_models = _schema(migrated), _schema(direct)

        missing = set(from_models) - set(from_migration)
        assert not missing, f"the migration never creates: {sorted(missing)}"
        extra = set(from_migration) - set(from_models)
        assert not extra, f"the migration creates what the models lack: {sorted(extra)}"
        differing = [k for k in from_models if from_migration[k] != from_models[k]]
        assert not differing, f"DDL differs for: {sorted(differing)}"

    def test_the_models_have_no_unmigrated_changes(self, tmp_path: Path) -> None:
        """Autogenerate against a migrated database must find nothing to do.

        This is the test that catches the real mistake -- adding a column and
        forgetting the revision. It fails the moment the models move ahead of
        the migrations.
        """
        database = tmp_path / "check.db"
        url = f"sqlite+aiosqlite:///{database}"

        upgrade = _alembic("upgrade", "head", url=url)
        assert upgrade.returncode == 0, upgrade.stderr

        check = _alembic("check", url=url)
        if check.returncode != 0:
            pytest.fail(
                "the models have changed without a migration; run\n"
                "  alembic revision --autogenerate -m 'describe the change'\n\n"
                f"{check.stdout}\n{check.stderr}"
            )


class TestCreateAllIsLocalOnly:
    """Outside local, create_all must not run at all.

    It cannot alter an existing table, so letting it run alongside migrations
    means a deployment that half-applies: new tables appear, changed ones do
    not, and nothing reports a problem.
    """

    @pytest.mark.asyncio
    async def test_it_is_skipped_outside_local(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from app.db import session as session_module

        monkeypatch.setattr(session_module.settings, "environment", "production")

        created: list[str] = []
        monkeypatch.setattr(
            session_module.SQLModel.metadata,
            "create_all",
            lambda *a, **k: created.append("called"),
        )
        await session_module.init_database()
        assert not created, "create_all ran outside local"

    @pytest.mark.asyncio
    async def test_it_still_runs_in_local(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Local development must keep working with no migration step."""
        from app.db import session as session_module

        monkeypatch.setattr(session_module.settings, "environment", "local")
        await session_module.init_database()  # must not raise


class TestDeploymentWiring:
    def test_the_entrypoint_gates_migrations_behind_a_flag(self) -> None:
        script = (BACKEND_ROOT / "entrypoint.sh").read_text(encoding="utf-8")
        assert "alembic upgrade head" in script
        assert "RUN_MIGRATIONS" in script
        assert 'exec "$@"' in script, "the entrypoint must hand off to the CMD"

    def test_only_one_service_migrates(self) -> None:
        """Two containers running upgrade at once race on the version table."""
        import yaml

        compose = yaml.safe_load(
            (BACKEND_ROOT.parent / "docker-compose.yml").read_text(encoding="utf-8")
        )
        migrating = [
            name
            for name, service in compose["services"].items()
            if str(service.get("environment", {}).get("RUN_MIGRATIONS", "")).lower()
            not in ("", "false")
        ]
        assert len(migrating) <= 1, f"more than one service migrates: {migrating}"
