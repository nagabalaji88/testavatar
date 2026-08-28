"""Every shipped registry entry must be routable by LiteLLM.

`ollama-cloud-llama2` shipped as a bare `llama2`. LiteLLM cannot infer a
provider from that, so the model failed with "LLM Provider NOT provided" on
every run -- and because the failure needs a real dispatch to surface, no
amount of config review caught it. It looked exactly like a missing
credential in the UI, which is what kept it hidden.

get_llm_provider is the same resolution LiteLLM performs at call time, so
this asks the router the question the run would ask.
"""

from __future__ import annotations

import json
import pathlib

import pytest

CONFIG_DIR = pathlib.Path(__file__).resolve().parents[1] / "config"
REGISTRIES = sorted(CONFIG_DIR.glob("models*.json"))


def _entries() -> list[tuple[str, str, str]]:
    found: list[tuple[str, str, str]] = []
    for path in REGISTRIES:
        payload = json.loads(path.read_text())
        for provider in payload.get("providers", []):
            found.append((path.name, provider["id"], provider["model_key"]))
    return found


def test_the_registries_are_not_empty() -> None:
    """Guards against the globs silently matching nothing."""
    assert REGISTRIES, f"no models*.json under {CONFIG_DIR}"
    assert _entries()


@pytest.mark.parametrize(
    ("registry", "model_id", "model_key"),
    _entries(),
    ids=[f"{r}:{i}" for r, i, _ in _entries()],
)
def test_every_model_key_resolves_to_a_provider(
    registry: str, model_id: str, model_key: str
) -> None:
    import litellm

    try:
        _, provider, _, _ = litellm.get_llm_provider(model_key)
    except Exception as exc:  # noqa: BLE001 - LiteLLM raises its own types
        pytest.fail(
            f"{registry}: {model_id} has model_key {model_key!r}, which LiteLLM "
            f"cannot route ({type(exc).__name__}). Prefix it with the provider "
            f"-- e.g. 'openai/{model_key}' for an OpenAI-compatible endpoint."
        )
    assert provider, f"{registry}: {model_id} resolved to an empty provider"
