"""A key named by a registry entry must appear on the credentials page.

A hosted deployment of an open-weight model belongs to no provider family --
its `provider` still reads "Ollama" -- so it names its own variable through
api_key_env. Only the seven families were listed, so exporting
OLLAMA_CLOUD_API_KEY produced nothing on the page an operator goes to for keys,
while the Models page showed the same key as present. Two screens disagreeing
about one credential, and the one they consult first was the one that stayed
silent.
"""

from __future__ import annotations

import pytest

from app.api.v1.endpoints import _entry_credential_statuses
from app.core.provider_families import PROVIDER_FAMILIES
from app.models.schemas import ProviderConfig

VARIABLE = "OLLAMA_CLOUD_API_KEY"


def _entry(**overrides) -> ProviderConfig:
    base = {
        "id": "ollama-cloud-llama2",
        "name": "Llama 2 (Ollama Cloud)",
        "provider": "Ollama",
        "model_key": "openai/llama2",
        "max_tokens": 4096,
        "cost_per_1k_input": 0.0,
        "cost_per_1k_output": 0.0,
        "enabled": True,
        "api_base": "https://api.ollama.cloud/v1",
        "api_key_env": VARIABLE,
    }
    return ProviderConfig(**{**base, **overrides})


class TestEntryNamedVariablesAreListed:
    def test_the_variable_appears(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(VARIABLE, "sk-whatever")
        rows = _entry_credential_statuses([_entry()])

        assert [r.env_var for r in rows] == [VARIABLE]
        assert rows[0].configured is True
        assert rows[0].source == "environment"

    def test_it_is_listed_even_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Otherwise the one row telling you what to export is the row missing."""
        monkeypatch.delenv(VARIABLE, raising=False)
        rows = _entry_credential_statuses([_entry()])

        assert len(rows) == 1
        assert rows[0].configured is False
        assert rows[0].source is None

    def test_it_is_not_editable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """There is no family row to store a value against."""
        monkeypatch.setenv(VARIABLE, "sk-whatever")
        assert _entry_credential_statuses([_entry()])[0].editable is False

    def test_a_family_variable_is_not_duplicated(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An entry may point at a family's own variable; the family row owns it."""
        family = PROVIDER_FAMILIES[0]
        monkeypatch.setenv(family.env_var, "sk-whatever")
        rows = _entry_credential_statuses([_entry(api_key_env=family.env_var)])
        assert rows == []

    def test_entries_sharing_a_variable_collapse_to_one_row(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(VARIABLE, "sk-whatever")
        rows = _entry_credential_statuses(
            [_entry(), _entry(id="ollama-cloud-mistral", name="Mistral (Ollama Cloud)")]
        )
        assert len(rows) == 1
        assert rows[0].model_count == 2

    def test_the_count_covers_enabled_entries_only(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """It answers "would setting this achieve anything", not "how many exist"."""
        monkeypatch.setenv(VARIABLE, "sk-whatever")
        rows = _entry_credential_statuses(
            [_entry(), _entry(id="off", name="Disabled", enabled=False)]
        )
        assert rows[0].model_count == 1

    def test_an_inline_key_is_not_listed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Nothing to point an operator at: the value is in the registry itself."""
        rows = _entry_credential_statuses(
            [_entry(api_key_env=None, api_key="sk-inline")]
        )
        assert rows == []

    def test_a_keyless_local_entry_is_not_listed(self) -> None:
        rows = _entry_credential_statuses(
            [_entry(api_key_env=None, api_base=None, provider="Ollama")]
        )
        assert rows == []
