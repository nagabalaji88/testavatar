"""The provider families the app knows how to authenticate.

One table, because three things have to agree about every family and used to be
spread across two modules that a comment could only ask to stay in sync:

  * which settings field / environment variable supplies its key,
  * what a registry entry's free-text `provider` value may say to mean it,
  * whether it is a local runtime that takes no key at all.

A family here that is missing from any of those three would either tell an
operator to set a variable nothing reads, or silently fail to match the
`provider` string their models.json actually contains.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class ProviderFamily:
    """A credential domain: one key authenticates every model in it."""

    # Canonical name, and the id the credentials API is addressed by.
    name: str
    label: str
    # The environment variable an operator can set instead of using the UI.
    env_var: str
    # The Settings attribute that variable lands on.
    settings_attr: str
    # Alternative spellings a registry entry's `provider` field may use.
    aliases: frozenset[str] = field(default_factory=frozenset)
    # Where to send someone who has no key yet.
    console_url: Optional[str] = None

    def matches(self, provider: str) -> bool:
        normalised = provider.strip().lower()
        return normalised == self.name or normalised in self.aliases


PROVIDER_FAMILIES: tuple[ProviderFamily, ...] = (
    ProviderFamily(
        name="openai",
        label="OpenAI",
        env_var="OPENAI_API_KEY",
        settings_attr="openai_api_key",
        console_url="https://platform.openai.com/api-keys",
    ),
    ProviderFamily(
        name="anthropic",
        label="Anthropic",
        env_var="ANTHROPIC_API_KEY",
        settings_attr="anthropic_api_key",
        aliases=frozenset({"claude"}),
        console_url="https://console.anthropic.com/settings/keys",
    ),
    ProviderFamily(
        name="google",
        label="Google Gemini",
        env_var="GEMINI_API_KEY",
        settings_attr="gemini_api_key",
        aliases=frozenset({"gemini", "googleai", "google-ai"}),
        console_url="https://aistudio.google.com/apikey",
    ),
    ProviderFamily(
        name="groq",
        label="Groq",
        env_var="GROQ_API_KEY",
        settings_attr="groq_api_key",
        console_url="https://console.groq.com/keys",
    ),
    ProviderFamily(
        name="openrouter",
        label="OpenRouter",
        env_var="OPENROUTER_API_KEY",
        settings_attr="openrouter_api_key",
        console_url="https://openrouter.ai/keys",
    ),
    ProviderFamily(
        name="together",
        label="Together AI",
        env_var="TOGETHER_API_KEY",
        settings_attr="together_api_key",
        aliases=frozenset({"togetherai", "together-ai"}),
        console_url="https://api.together.xyz/settings/api-keys",
    ),
    ProviderFamily(
        name="huggingface",
        label="Hugging Face",
        env_var="HUGGINGFACE_API_KEY",
        settings_attr="huggingface_api_key",
        aliases=frozenset({"hf", "hugging-face"}),
        console_url="https://huggingface.co/settings/tokens",
    ),
)

FAMILIES_BY_NAME: dict[str, ProviderFamily] = {f.name: f for f in PROVIDER_FAMILIES}

# Runtimes that serve open-weight models from your own hardware. They take no
# credential, so a missing key must not be reported as a misconfiguration.
LOCAL_PROVIDERS = frozenset(
    {"ollama", "vllm", "llamacpp", "llama.cpp", "local", "lmstudio"}
)


def family_for(provider: str) -> Optional[ProviderFamily]:
    """Resolve a registry entry's free-text `provider` to a known family."""
    for candidate in PROVIDER_FAMILIES:
        if candidate.matches(provider):
            return candidate
    return None


def is_local_provider(provider: str) -> bool:
    return provider.strip().lower() in LOCAL_PROVIDERS


__all__ = [
    "FAMILIES_BY_NAME",
    "LOCAL_PROVIDERS",
    "PROVIDER_FAMILIES",
    "ProviderFamily",
    "family_for",
    "is_local_provider",
]
