"""Credentials stored through the admin UI, and the registry edits around them.

Two properties matter more than the rest here and are asserted from several
angles: a stored key is never readable back over the API, and a key entered in
the UI actually becomes the effective one. The second is the whole point of the
feature -- if a stale environment variable outranked the database, the edit
would appear to do nothing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from app.core.crypto import (
    CredentialDecryptionError,
    decrypt_secret,
    encrypt_secret,
    last4,
    reset_cipher_cache,
)
from app.core.provider_families import PROVIDER_FAMILIES, family_for, is_local_provider
from app.models.schemas import ProviderConfig
from app.services.credential_store import CredentialStore
from app.services.model_registry import ModelRegistry


def _provider(**overrides: object) -> ProviderConfig:
    base = {
        "id": "gpt",
        "name": "GPT-4o",
        "provider": "OpenAI",
        "model_key": "gpt-4o",
        "max_tokens": 4096,
        "cost_per_1k_input": 0.0,
        "cost_per_1k_output": 0.0,
    }
    base.update(overrides)
    return ProviderConfig.model_validate(base)


@pytest_asyncio.fixture
async def session() -> AsyncSession:
    """An in-memory database with the real schema.

    A StaticPool-backed SQLite memory URL, so every connection in the session
    sees the same database -- the default would give each one its own empty
    one and the credential row would vanish between statements.
    """
    from sqlalchemy.pool import StaticPool

    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    import app.models.domain  # noqa: F401  (register mappers)

    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        yield db
    await engine.dispose()


class TestEncryption:
    def test_a_secret_round_trips(self) -> None:
        assert decrypt_secret(encrypt_secret("sk-live-abcd1234")) == "sk-live-abcd1234"

    def test_the_ciphertext_does_not_contain_the_plaintext(self) -> None:
        """Encoding is not encryption; a base64 of the key would pass a
        naive round-trip test while still disclosing it in a database dump."""
        secret = "sk-proj-verydistinctivevalue"
        assert secret not in encrypt_secret(secret)

    def test_the_same_secret_encrypts_differently_each_time(self) -> None:
        """Fernet is randomised. Equal ciphertexts would let anyone with read
        access to the table tell that two families share a key."""
        assert encrypt_secret("sk-same") != encrypt_secret("sk-same")

    def test_a_secret_from_another_key_is_reported_not_raised(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Rotating JWT_SECRET_KEY must not turn the models page into a 500.

        The realistic cause of undecryptable ciphertext is exactly that, and
        the useful response is "re-enter this key".
        """
        monkeypatch.setattr(
            "app.core.crypto.settings.credential_encryption_key",
            "first-encryption-key",
            raising=False,
        )
        reset_cipher_cache()
        ciphertext = encrypt_secret("sk-written-under-the-old-key")

        monkeypatch.setattr(
            "app.core.crypto.settings.credential_encryption_key",
            "rotated-encryption-key",
            raising=False,
        )
        reset_cipher_cache()
        with pytest.raises(CredentialDecryptionError):
            decrypt_secret(ciphertext)

    def test_last4_does_not_overshare_a_short_value(self) -> None:
        assert last4("sk-abcdefgh") == "efgh"
        assert last4("ab") == "**"


class TestTheStore:
    @pytest.mark.asyncio
    async def test_a_stored_key_is_readable_synchronously(
        self, session: AsyncSession
    ) -> None:
        """The LLM path is sync, so the whole design rests on this."""
        store = CredentialStore()
        await store.set(session, "openai", "sk-stored-value")
        assert store.get("openai") == "sk-stored-value"

    @pytest.mark.asyncio
    async def test_a_fresh_process_recovers_the_key_by_refreshing(
        self, session: AsyncSession
    ) -> None:
        """This is the worker case: a different process, its own empty cache."""
        writer = CredentialStore()
        await writer.set(session, "groq", "gsk-written-by-the-api")
        await session.commit()

        worker = CredentialStore()
        assert worker.get("groq") is None
        await worker.refresh(session)
        assert worker.get("groq") == "gsk-written-by-the-api"

    @pytest.mark.asyncio
    async def test_the_stored_value_is_encrypted_at_rest(
        self, session: AsyncSession
    ) -> None:
        from app.models.domain import ProviderCredential

        store = CredentialStore()
        await store.set(session, "anthropic", "sk-ant-plaintext-canary")
        row = await session.get(ProviderCredential, "anthropic")
        assert row is not None
        assert "sk-ant-plaintext-canary" not in row.encrypted_key
        assert row.last4 == "nary"

    @pytest.mark.asyncio
    async def test_clearing_removes_it_from_the_cache_and_the_table(
        self, session: AsyncSession
    ) -> None:
        store = CredentialStore()
        await store.set(session, "openai", "sk-to-be-removed")
        assert await store.clear(session, "openai") is True
        assert store.get("openai") is None
        await store.refresh(session)
        assert store.get("openai") is None

    @pytest.mark.asyncio
    async def test_an_unknown_family_is_rejected(self, session: AsyncSession) -> None:
        """The family is a path parameter; an unknown one must not create a row
        that nothing will ever read."""
        with pytest.raises(KeyError):
            await CredentialStore().set(session, "not-a-provider", "sk-x")

    @pytest.mark.asyncio
    async def test_an_undecryptable_row_is_surfaced_not_fatal(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "app.core.crypto.settings.credential_encryption_key", "key-one", raising=False
        )
        reset_cipher_cache()
        store = CredentialStore()
        await store.set(session, "openai", "sk-under-key-one")
        await session.commit()

        monkeypatch.setattr(
            "app.core.crypto.settings.credential_encryption_key", "key-two", raising=False
        )
        reset_cipher_cache()

        fresh = CredentialStore()
        await fresh.refresh(session)  # must not raise
        assert fresh.get("openai") is None
        assert "openai" in fresh.undecryptable_families()


class TestPrecedence:
    """A key set in the UI has to win, or the edit silently does nothing."""

    @pytest.mark.asyncio
    async def test_the_database_outranks_the_environment(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.services import llm_service

        monkeypatch.setattr(
            llm_service.settings, "openai_api_key", "sk-from-the-environment"
        )
        assert llm_service._api_key_for(_provider()) == "sk-from-the-environment"

        await llm_service.credential_store.set(session, "openai", "sk-from-the-ui")
        assert llm_service._api_key_for(_provider()) == "sk-from-the-ui"
        assert llm_service.credential_source(_provider()) == "database"

    @pytest.mark.asyncio
    async def test_a_per_entry_variable_still_outranks_both(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """One entry pinned to its own variable must not be hijacked by a
        family-wide key set in the UI."""
        from app.services import llm_service

        monkeypatch.setenv("MPG_TEST_DEDICATED_KEY", "sk-dedicated")
        await llm_service.credential_store.set(session, "openai", "sk-family-wide")

        entry = _provider(api_key_env="MPG_TEST_DEDICATED_KEY")
        assert llm_service._api_key_for(entry) == "sk-dedicated"
        assert llm_service.credential_source(entry) == "entry_env"

    @pytest.mark.asyncio
    async def test_storing_a_key_makes_the_model_callable(
        self, session: AsyncSession
    ) -> None:
        """The user-visible promise: set a key, the model becomes selectable."""
        from app.services import llm_service

        assert llm_service.requires_credential(_provider()) is True
        await llm_service.credential_store.set(session, "openai", "sk-now-present")
        assert llm_service.requires_credential(_provider()) is False

    def test_reporting_no_source_when_nothing_supplies_a_key(self) -> None:
        from app.services import llm_service

        assert llm_service.credential_source(_provider()) is None

    def test_a_local_runtime_reports_no_family_to_configure(self) -> None:
        """Offering to set a key for an Ollama model would read as a problem
        to fix on a model that needs nothing."""
        from app.services import llm_service

        local = _provider(id="ollama", provider="Ollama", model_key="ollama_chat/x")
        assert llm_service.credential_family(local) is None
        assert llm_service.is_local_runtime(local) is True


class TestTheApiNeverServesTheValue:
    @pytest.mark.asyncio
    async def test_the_credential_listing_withholds_every_stored_key(
        self, session: AsyncSession
    ) -> None:
        from app.api.v1.endpoints import _credential_rows, _credential_statuses
        from app.services.credential_store import credential_store

        await credential_store.set(session, "openai", "sk-must-never-be-served")
        statuses = _credential_statuses(await _credential_rows(session))
        assert "sk-must-never-be-served" not in json.dumps(
            [s.model_dump(mode="json") for s in statuses]
        )

    def test_the_write_schema_has_no_readable_counterpart(self) -> None:
        """CredentialStatus is the only shape returned, and it has no field
        that could carry a value -- checked structurally so a later addition
        of one fails here rather than in production."""
        from app.models.schemas import CredentialStatus

        assert "api_key" not in CredentialStatus.model_fields
        assert not [
            name
            for name in CredentialStatus.model_fields
            if "key" in name and name not in {"api_key_env"}
        ] or set(CredentialStatus.model_fields) >= {"last4"}

    @pytest.mark.asyncio
    async def test_a_stored_key_does_not_leak_through_the_model_listing(
        self, session: AsyncSession
    ) -> None:
        from app.api.v1.endpoints import _to_public
        from app.services.credential_store import credential_store

        await credential_store.set(session, "openai", "sk-leak-canary-value")
        served = _to_public(_provider()).model_dump(mode="json")
        assert "sk-leak-canary-value" not in json.dumps(served)
        # It should still report that the model is now usable.
        assert served["credential_available"] is True
        assert served["credential_source"] == "database"


class TestRegistryStalenessAcrossProcesses:
    """The API and the worker are separate processes sharing one file.

    Caching on first read alone meant a model added through the API was offered
    by the UI and then rejected by the worker as unknown.
    """

    def _write(self, path: Path, ids: list[str]) -> None:
        path.write_text(
            json.dumps(
                {
                    "version": "1.0",
                    "providers": [
                        {
                            "id": pid,
                            "name": pid,
                            "provider": "Ollama",
                            "model_key": f"ollama_chat/{pid}",
                            "max_tokens": 4096,
                            "cost_per_1k_input": 0.0,
                            "cost_per_1k_output": 0.0,
                        }
                        for pid in ids
                    ],
                }
            ),
            encoding="utf-8",
        )

    def test_a_second_process_sees_a_model_added_by_the_first(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "models.json"
        self._write(path, ["one"])

        worker = ModelRegistry(path)
        assert [p.id for p in worker.all()] == ["one"]

        # The API process adds a model.
        api = ModelRegistry(path)
        api.upsert(
            ProviderConfig.model_validate(
                {
                    "id": "two",
                    "name": "Two",
                    "provider": "Ollama",
                    "model_key": "ollama_chat/two",
                    "max_tokens": 4096,
                    "cost_per_1k_input": 0.0,
                    "cost_per_1k_output": 0.0,
                }
            )
        )

        # Without the mtime check this raised UnknownProviderError.
        assert worker.get("two").id == "two"
        assert worker.resolve(["two"])[0].id == "two"

    def test_the_writer_does_not_reread_its_own_write(self, tmp_path: Path) -> None:
        """The stamp is recorded after _persist, so the next load is a cache
        hit rather than a needless re-parse of the file just written."""
        path = tmp_path / "models.json"
        self._write(path, ["one"])
        registry = ModelRegistry(path)
        registry.load()
        registry.set_enabled("one", False)
        assert registry._stamp == registry._file_stamp()
        assert registry.get("one").enabled is False


class TestBulkImport:
    def _registry(self, tmp_path: Path, ids: list[str]) -> ModelRegistry:
        path = tmp_path / "models.json"
        path.write_text(
            json.dumps(
                {
                    "version": "1.0",
                    "providers": [
                        {
                            "id": pid,
                            "name": pid,
                            "provider": "Ollama",
                            "model_key": f"ollama_chat/{pid}",
                            "max_tokens": 4096,
                            "cost_per_1k_input": 0.0,
                            "cost_per_1k_output": 0.0,
                        }
                        for pid in ids
                    ],
                }
            ),
            encoding="utf-8",
        )
        return ModelRegistry(path)

    def _incoming(self, ids: list[str]) -> list[ProviderConfig]:
        return [
            ProviderConfig.model_validate(
                {
                    "id": pid,
                    "name": f"{pid} imported",
                    "provider": "Groq",
                    "model_key": f"groq/{pid}",
                    "max_tokens": 8192,
                    "cost_per_1k_input": 0.0,
                    "cost_per_1k_output": 0.0,
                }
            )
            for pid in ids
        ]

    def test_merge_keeps_models_the_upload_does_not_mention(
        self, tmp_path: Path
    ) -> None:
        """Merge is the default because replace is destructive for a file
        picker: an upload listing one model must not delete the other nine."""
        registry = self._registry(tmp_path, ["keep", "change"])
        added, updated = registry.import_providers(
            self._incoming(["change", "new"]), replace=False
        )

        assert added == ["new"]
        assert updated == ["change"]
        assert [p.id for p in registry.all()] == ["keep", "change", "new"]
        assert registry.get("change").provider == "Groq"
        assert registry.get("keep").provider == "Ollama"

    def test_replace_swaps_the_catalogue_wholesale(self, tmp_path: Path) -> None:
        registry = self._registry(tmp_path, ["old-one", "old-two"])
        registry.import_providers(self._incoming(["fresh"]), replace=True)
        assert [p.id for p in registry.all()] == ["fresh"]

    def test_an_import_survives_a_reload_from_disk(self, tmp_path: Path) -> None:
        """It has to be persisted, not just cached, or a restart loses it."""
        registry = self._registry(tmp_path, ["one"])
        registry.import_providers(self._incoming(["two"]), replace=False)
        assert [p.id for p in ModelRegistry(registry._path).all()] == ["one", "two"]


class TestValidationFailuresAreServableAsJson:
    """A rejected api_base has to come back as a 422 that says why.

    Every custom check in schemas.py rejects input by raising ValueError, and
    pydantic puts that exception *object* into the error's `ctx`. json.dumps
    cannot encode it, so building the 422 body raised TypeError inside the
    handler and the caller got a bare 500 -- making the SSRF rejection
    indistinguishable from a server fault, on the one field where knowing the
    difference matters.
    """

    def _errors_for(self, body: dict) -> list[dict]:
        from fastapi.exceptions import RequestValidationError
        from pydantic import ValidationError

        from app.main import _serialisable_errors
        from app.models.schemas import RegistryImportRequest

        try:
            RegistryImportRequest.model_validate(body)
        except ValidationError as exc:
            return _serialisable_errors(RequestValidationError(exc.errors()))
        raise AssertionError("expected the payload to be rejected")

    def _payload(self, api_base: str) -> dict:
        return {
            "providers": [
                {
                    "id": "evil",
                    "name": "Evil",
                    "provider": "OpenAI",
                    "model_key": "gpt-4o",
                    "max_tokens": 1024,
                    "cost_per_1k_input": 0.0,
                    "cost_per_1k_output": 0.0,
                    "api_base": api_base,
                }
            ],
            "mode": "merge",
        }

    def test_an_ssrf_rejection_serialises(self) -> None:
        errors = self._errors_for(self._payload("http://169.254.169.254/latest"))
        # The whole bug: this call used to raise TypeError.
        encoded = json.dumps(errors)
        assert "169.254.169.254" in encoded

    def test_the_message_survives_the_sanitising(self) -> None:
        """Stripping ctx must not strip the reason with it."""
        errors = self._errors_for(self._payload("http://10.0.0.5/admin"))
        assert any("private address" in error["msg"] for error in errors)

    def test_the_location_points_at_the_offending_entry(self) -> None:
        """In a fifty-model upload, "some api_base is bad" is not actionable."""
        # Not 127.0.0.1: that is allowlisted on purpose, because local
        # inference needs it.
        body = self._payload("http://192.168.31.7/x")
        body["providers"].insert(
            0,
            {
                "id": "fine",
                "name": "Fine",
                "provider": "Groq",
                "model_key": "groq/x",
                "max_tokens": 1024,
                "cost_per_1k_input": 0.0,
                "cost_per_1k_output": 0.0,
            },
        )
        errors = self._errors_for(body)
        # Validating the model directly gives ("providers", 1, "api_base");
        # over HTTP FastAPI prefixes "body". The index is the part that has to
        # survive either way.
        assert any(
            "providers" in error["loc"] and 1 in error["loc"] for error in errors
        ), errors


class TestTheFamilyTableIsInternallyConsistent:
    """Three things have to agree about every family; they used to live in two
    modules that only a comment kept in sync."""

    def test_every_family_resolves_from_its_own_name(self) -> None:
        for family in PROVIDER_FAMILIES:
            assert family_for(family.name) is family

    def test_every_family_resolves_from_each_alias(self) -> None:
        for family in PROVIDER_FAMILIES:
            for alias in family.aliases:
                assert family_for(alias) is family

    def test_matching_is_case_and_whitespace_insensitive(self) -> None:
        """`provider` is free text in models.json; " OpenAI " occurs."""
        assert family_for("  OpenAI  ") is family_for("openai")

    def test_every_family_names_a_settings_field_that_exists(self) -> None:
        """A typo here would silently mean "this key is never configured"."""
        from app.core.config import Settings

        for family in PROVIDER_FAMILIES:
            assert family.settings_attr in Settings.model_fields, family.name

    def test_no_family_collides_with_a_local_runtime(self) -> None:
        """A name in both would make a keyless local model demand a key."""
        for family in PROVIDER_FAMILIES:
            assert not is_local_provider(family.name)
            for alias in family.aliases:
                assert not is_local_provider(alias)

    def test_aliases_are_unique_across_families(self) -> None:
        seen: dict[str, str] = {}
        for family in PROVIDER_FAMILIES:
            for token in (family.name, *family.aliases):
                assert token not in seen, f"{token} claimed by {seen.get(token)}"
                seen[token] = family.name
