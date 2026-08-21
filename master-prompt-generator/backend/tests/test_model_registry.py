"""Provider registry and endpoint-resolution tests.

These cover the open-source path specifically: local runtimes must resolve an
endpoint without a credential, and the shipped local registry must be valid and
internally consistent with the agent ids the pipeline references.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.config import BACKEND_ROOT
from app.models.schemas import ProviderConfig, ProviderRegistryConfig
from app.services.llm_service import (
    _api_base_for,
    _api_key_for,
    requires_credential,
)
from app.services.model_registry import ModelRegistry, UnknownProviderError

LOCAL_CONFIG = BACKEND_ROOT / "config" / "models.local.json"
CLOUD_CONFIG = BACKEND_ROOT / "config" / "models.json"


def _provider(**overrides: object) -> ProviderConfig:
    base = {
        "id": "ollama-test",
        "name": "Test",
        "provider": "Ollama",
        "model_key": "ollama_chat/qwen2.5:7b-instruct",
        "max_tokens": 4096,
        "cost_per_1k_input": 0.0,
        "cost_per_1k_output": 0.0,
    }
    base.update(overrides)
    return ProviderConfig.model_validate(base)


class TestEndpointResolution:
    def test_local_runtime_needs_no_credential(self) -> None:
        provider = _provider()
        assert _api_key_for(provider) is None
        assert requires_credential(provider) is False

    def test_local_runtime_inherits_the_configured_base_url(self) -> None:
        assert _api_base_for(_provider()) == "http://ollama:11434"

    def test_explicit_api_base_wins(self) -> None:
        provider = _provider(api_base="http://gpu-box.lan:11434")
        assert _api_base_for(provider) == "http://gpu-box.lan:11434"

    def test_hosted_provider_without_a_key_is_flagged(self) -> None:
        provider = _provider(provider="OpenAI", model_key="gpt-4o")
        # No key is configured in the test environment.
        assert requires_credential(provider) is True

    def test_hosted_provider_gets_no_local_base_url(self) -> None:
        assert _api_base_for(_provider(provider="Anthropic")) is None


class TestLocalRegistry:
    def test_shipped_local_registry_is_valid(self) -> None:
        raw = json.loads(LOCAL_CONFIG.read_text(encoding="utf-8"))
        registry = ProviderRegistryConfig.model_validate(raw)
        assert registry.providers

    def test_every_local_model_is_free(self) -> None:
        registry = ModelRegistry(LOCAL_CONFIG).load()
        for provider in registry.providers:
            assert provider.cost_per_1k_input == 0.0
            assert provider.cost_per_1k_output == 0.0
            assert provider.estimate_cost(10_000, 10_000) == 0.0

    def test_enabled_local_models_run_without_credentials(self) -> None:
        registry = ModelRegistry(LOCAL_CONFIG)
        enabled = registry.enabled()
        assert len(enabled) >= 2, "consensus needs at least two models to merge"
        for provider in enabled:
            assert requires_credential(provider) is False
            assert _api_base_for(provider) is not None

    def test_agent_model_ids_referenced_by_the_local_env_exist(self) -> None:
        """.env.local.example must not point an agent at a missing provider."""
        env_path = BACKEND_ROOT.parent / ".env.local.example"
        if not env_path.exists():  # pragma: no cover - repo layout guard
            pytest.skip(".env.local.example not present")

        settings_map = {}
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            settings_map[key.strip()] = value.strip()

        registry = ModelRegistry(LOCAL_CONFIG)
        known = {provider.id for provider in registry.all()}
        for key in ("ANALYZER_MODEL_ID", "JUDGE_MODEL_ID", "CONSENSUS_MODEL_ID"):
            model_id = settings_map.get(key)
            assert model_id, f"{key} must be set in .env.local.example"
            assert model_id in known, f"{key}={model_id} is not in models.local.json"

    def test_unknown_provider_is_rejected(self) -> None:
        registry = ModelRegistry(LOCAL_CONFIG)
        with pytest.raises(UnknownProviderError):
            registry.resolve(["does-not-exist"])


class TestCloudRegistry:
    def test_shipped_cloud_registry_is_still_valid(self) -> None:
        raw = json.loads(CLOUD_CONFIG.read_text(encoding="utf-8"))
        assert ProviderRegistryConfig.model_validate(raw).providers

    def test_registry_files_do_not_share_ids(self) -> None:
        """Distinct ids keep the two registries safely swappable."""
        cloud = {p.id for p in ModelRegistry(CLOUD_CONFIG).all()}
        local = {p.id for p in ModelRegistry(LOCAL_CONFIG).all()}
        assert not cloud & local


def test_registry_falls_back_to_defaults_for_a_missing_file(tmp_path: Path) -> None:
    registry = ModelRegistry(tmp_path / "absent.json")
    assert registry.enabled(), "a missing config must not leave zero providers"


class TestPerEntryCredentials:
    """A hosted deployment of an open-weight model needs its own key.

    `provider` still reads "Ollama" for such an entry, so nothing about the
    provider family distinguishes it from a keyless localhost runtime -- only
    the declared credential source does.
    """

    def test_api_key_env_is_read_from_the_environment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MPG_TEST_CLOUD_KEY", "sk-from-environment")
        provider = _provider(api_key_env="MPG_TEST_CLOUD_KEY")
        assert _api_key_for(provider) == "sk-from-environment"
        assert requires_credential(provider) is False

    def test_api_key_env_beats_an_inline_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MPG_TEST_CLOUD_KEY", "sk-from-environment")
        provider = _provider(
            api_key_env="MPG_TEST_CLOUD_KEY", api_key="sk-inline-loses"
        )
        assert _api_key_for(provider) == "sk-from-environment"

    def test_unset_api_key_env_reports_a_missing_credential(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("MPG_TEST_CLOUD_KEY", raising=False)
        provider = _provider(api_key_env="MPG_TEST_CLOUD_KEY")
        assert _api_key_for(provider) is None
        # The Ollama provider family would otherwise take the key-free path and
        # let a run start against an endpoint that answers 401 on every call.
        assert requires_credential(provider) is True

    def test_blank_api_key_env_is_a_missing_credential_not_an_empty_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MPG_TEST_CLOUD_KEY", "   ")
        provider = _provider(api_key_env="MPG_TEST_CLOUD_KEY")
        assert _api_key_for(provider) is None

    def test_keyless_local_runtime_is_unaffected(self) -> None:
        assert requires_credential(_provider()) is False


class TestDefaultSelectionSkipsUncredentialedProviders:
    def _registry(self, tmp_path: Path, providers: list[dict]) -> ModelRegistry:
        path = tmp_path / "models.json"
        path.write_text(
            json.dumps({"version": "1.0", "providers": providers}), encoding="utf-8"
        )
        return ModelRegistry(path)

    def test_uncredentialed_entries_are_dropped_from_the_default_fan_out(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setattr(
            "app.services.llm_service.settings.openai_api_key", None, raising=False
        )
        registry = self._registry(
            tmp_path,
            [
                _provider(id="local-ok").model_dump(mode="json"),
                _provider(
                    id="cloud-no-key",
                    provider="OpenAI",
                    model_key="gpt-4o",
                ).model_dump(mode="json"),
            ],
        )
        assert [p.id for p in registry.resolve(None)] == ["local-ok"]

    def test_an_explicit_selection_still_honours_an_uncredentialed_entry(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Forcing a model must surface its real provider error, not a filter."""
        monkeypatch.setattr(
            "app.services.llm_service.settings.openai_api_key", None, raising=False
        )
        registry = self._registry(
            tmp_path,
            [
                _provider(
                    id="cloud-no-key", provider="OpenAI", model_key="gpt-4o"
                ).model_dump(mode="json")
            ],
        )
        assert [p.id for p in registry.resolve(["cloud-no-key"])] == ["cloud-no-key"]

    def test_no_credentialed_provider_names_the_missing_variable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("MPG_TEST_CLOUD_KEY", raising=False)
        registry = self._registry(
            tmp_path,
            [
                _provider(
                    id="cloud-no-key", api_key_env="MPG_TEST_CLOUD_KEY"
                ).model_dump(mode="json")
            ],
        )
        with pytest.raises(UnknownProviderError, match="MPG_TEST_CLOUD_KEY"):
            registry.resolve(None)


class TestCredentialsAreNotServedByTheApi:
    """GET /models is readable by any authenticated principal.

    With open registration on -- the default -- that is anyone who can reach
    the service, so the response shape must not carry the inline credential.
    """

    def test_public_shape_has_no_api_key_field(self) -> None:
        from app.models.schemas import ProviderPublic

        assert "api_key" not in ProviderPublic.model_fields
        # The variable *name* is not a secret and stays visible, so an operator
        # can see which variable a misconfigured entry reads.
        assert "api_key_env" in ProviderPublic.model_fields

    def test_an_inline_key_is_dropped_when_narrowed_to_the_public_shape(self) -> None:
        from app.models.schemas import ProviderPublic

        secret = "sk-inline-must-not-be-served"
        entry = _provider(api_key=secret, api_key_env="SOME_VAR")
        assert entry.api_key == secret, "the registry itself still needs the key"

        served = ProviderPublic.model_validate(
            entry.model_dump(mode="json")
        ).model_dump(mode="json")
        assert secret not in json.dumps(served)
        assert served["api_key_env"] == "SOME_VAR"

    def test_no_models_route_serves_the_credential_bearing_shape(self) -> None:
        """The leak was a response_model, so assert on the wiring itself.

        Written as a sweep rather than a check of one route: the first fix
        missed POST /models/reload, which returned the same shape.
        """
        from app.api.v1.endpoints import models_router

        leaking = [
            f"{sorted(r.methods)} {r.path}"
            for r in models_router.routes
            if hasattr(r, "methods")
            and r.response_model in (ProviderConfig, list[ProviderConfig])
        ]
        assert not leaking, f"these serve the inline api_key: {leaking}"
