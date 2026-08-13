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
