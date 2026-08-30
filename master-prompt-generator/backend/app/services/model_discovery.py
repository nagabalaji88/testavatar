"""Ask each provider which models the configured key can actually reach.

A hand-maintained catalogue shipped in the repo has two failure modes that this
avoids: it goes stale, and it offers models the *account* has no access to. Both
were observed here -- a plausible-looking `groq/llama-3.3-70b-versatile` was in
fact not available on the key in use, while that account served an entirely
different family. The provider is the only authority on what it will answer for,
so the catalogue is fetched rather than written down.

What is written down is the shape of each provider's listing endpoint: the URL,
how it carries the key, and how to turn its rows into a litellm `model_key`.
That last part is the reason this cannot be one generic loop -- the prefix
litellm expects differs per family, and getting it wrong produces a registry
entry that resolves to nothing.

Results are cached briefly. The listing endpoints are rate-limited, the answer
changes on the order of weeks, and the models page would otherwise re-query
every provider on each render.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

import httpx

from app.core.logging import get_logger
from app.core.pinned_transport import build_pinned_client
from app.core.provider_families import PROVIDER_FAMILIES, ProviderFamily
from app.services.credential_store import credential_store
from app.core.config import settings

logger = get_logger(__name__)

CACHE_TTL_SECONDS = 300.0
REQUEST_TIMEOUT_SECONDS = 20.0

# Chat models only. These endpoints also return embedding, audio, moderation
# and guard models, none of which can answer a prompt-generation request -- and
# offering one produces a registry entry that fails on first use.
_EXCLUDE_SUBSTRINGS = (
    "whisper",
    "embed",
    "tts",
    "dall-e",
    "moderation",
    "guard",
    "rerank",
    "stable-diffusion",
    "flux",
    "sora",
    "davinci",
    "babbage",
    "audio",
    "realtime",
    "transcribe",
    "image",
)


@dataclass
class DiscoveredModel:
    """One model a provider says the current key can call."""

    family: str
    provider_label: str
    # Ready to paste into a registry entry's model_key: litellm-prefixed.
    model_key: str
    # The provider's own id, shown so the row is recognisable in their console.
    remote_id: str
    display_name: str
    cost_per_1k_input: Optional[float] = None
    cost_per_1k_output: Optional[float] = None
    max_tokens: Optional[int] = None
    supports_json_mode: bool = True


@dataclass
class FamilyDiscovery:
    """The outcome of asking one family for its model list."""

    family: str
    label: str
    configured: bool
    models: list[DiscoveredModel]
    error: Optional[str] = None


def _is_chat_model(model_id: str) -> bool:
    lowered = model_id.lower()
    return not any(token in lowered for token in _EXCLUDE_SUBSTRINGS)


def _pretty(remote_id: str) -> str:
    """A readable label from a provider's model id."""
    tail = remote_id.rsplit("/", 1)[-1]
    return tail.replace("-", " ").replace("_", " ").strip() or remote_id


# --- per-family listing -----------------------------------------------------


async def _list_openai_compatible(
    client: httpx.AsyncClient,
    *,
    url: str,
    api_key: str,
    family: ProviderFamily,
    key_prefix: str,
    header: str = "Authorization",
    header_template: str = "Bearer {key}",
) -> list[DiscoveredModel]:
    """The /v1/models shape shared by OpenAI, Groq, Together and friends."""
    response = await client.get(
        url,
        headers={header: header_template.format(key=api_key)},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    rows = response.json().get("data", []) or []

    models: list[DiscoveredModel] = []
    for row in rows:
        remote_id = str(row.get("id") or "").strip()
        if not remote_id or not _is_chat_model(remote_id):
            continue
        models.append(
            DiscoveredModel(
                family=family.name,
                provider_label=family.label,
                model_key=f"{key_prefix}{remote_id}" if key_prefix else remote_id,
                remote_id=remote_id,
                display_name=_pretty(remote_id),
                max_tokens=_as_int(row.get("context_window") or row.get("context_length")),
            )
        )
    return models


async def _list_anthropic(
    client: httpx.AsyncClient, *, api_key: str, family: ProviderFamily
) -> list[DiscoveredModel]:
    response = await client.get(
        "https://api.anthropic.com/v1/models?limit=100",
        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    rows = response.json().get("data", []) or []
    return [
        DiscoveredModel(
            family=family.name,
            provider_label=family.label,
            model_key=f"anthropic/{row['id']}",
            remote_id=str(row["id"]),
            display_name=str(row.get("display_name") or _pretty(str(row["id"]))),
            # Anthropic's Messages API has no JSON response_format switch; the
            # app's prompted-JSON path handles these, and claiming native
            # support would send a parameter the API rejects.
            supports_json_mode=False,
        )
        for row in rows
        if row.get("id")
    ]


async def _list_gemini(
    client: httpx.AsyncClient, *, api_key: str, family: ProviderFamily
) -> list[DiscoveredModel]:
    response = await client.get(
        "https://generativelanguage.googleapis.com/v1beta/models",
        params={"key": api_key, "pageSize": 200},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    rows = response.json().get("models", []) or []

    models: list[DiscoveredModel] = []
    for row in rows:
        # "models/gemini-flash-latest" -> "gemini-flash-latest"
        remote_id = str(row.get("name") or "").split("/", 1)[-1]
        methods = row.get("supportedGenerationMethods") or []
        # Without generateContent it is an embedding or token-counting model and
        # cannot answer a prompt.
        if not remote_id or "generateContent" not in methods:
            continue
        if not _is_chat_model(remote_id):
            continue
        models.append(
            DiscoveredModel(
                family=family.name,
                provider_label=family.label,
                model_key=f"gemini/{remote_id}",
                remote_id=remote_id,
                display_name=str(row.get("displayName") or _pretty(remote_id)),
                max_tokens=_as_int(row.get("outputTokenLimit")),
            )
        )
    return models


async def _list_openrouter(
    client: httpx.AsyncClient, *, api_key: str, family: ProviderFamily
) -> list[DiscoveredModel]:
    response = await client.get(
        "https://openrouter.ai/api/v1/models",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    rows = response.json().get("data", []) or []

    models: list[DiscoveredModel] = []
    for row in rows:
        remote_id = str(row.get("id") or "").strip()
        if not remote_id or not _is_chat_model(remote_id):
            continue
        # OpenRouter quotes per-token prices as strings; the registry is
        # per-1k, hence the factor of 1000.
        pricing = row.get("pricing") or {}
        models.append(
            DiscoveredModel(
                family=family.name,
                provider_label=family.label,
                model_key=f"openrouter/{remote_id}",
                remote_id=remote_id,
                display_name=str(row.get("name") or _pretty(remote_id)),
                cost_per_1k_input=_per_1k(pricing.get("prompt")),
                cost_per_1k_output=_per_1k(pricing.get("completion")),
                max_tokens=_as_int(row.get("context_length")),
            )
        )
    return models


def _as_int(value: Any) -> Optional[int]:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _per_1k(value: Any) -> Optional[float]:
    """Convert a per-token price string to per-1000-tokens."""
    try:
        return round(float(value) * 1000, 8)
    except (TypeError, ValueError):
        return None


# Bound per family so the caller does not need to know which shape each uses.
_LISTERS: dict[str, Callable[[httpx.AsyncClient, str, ProviderFamily], Any]] = {
    "openai": lambda c, k, f: _list_openai_compatible(
        c, url="https://api.openai.com/v1/models", api_key=k, family=f, key_prefix=""
    ),
    "anthropic": lambda c, k, f: _list_anthropic(c, api_key=k, family=f),
    "google": lambda c, k, f: _list_gemini(c, api_key=k, family=f),
    "groq": lambda c, k, f: _list_openai_compatible(
        c,
        url="https://api.groq.com/openai/v1/models",
        api_key=k,
        family=f,
        key_prefix="groq/",
    ),
    "openrouter": lambda c, k, f: _list_openrouter(c, api_key=k, family=f),
    "together": lambda c, k, f: _list_openai_compatible(
        c,
        url="https://api.together.xyz/v1/models",
        api_key=k,
        family=f,
        key_prefix="together_ai/",
    ),
}


def _resolved_key(family: ProviderFamily) -> Optional[str]:
    """The key discovery should authenticate with, matching llm_service order."""
    if stored := credential_store.get(family.name):
        return stored
    value = getattr(settings, family.settings_attr, None)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _describe_http_error(exc: httpx.HTTPStatusError) -> str:
    """A short, operator-facing reason, keeping the provider's own wording.

    The status alone is not enough to act on: 401 means the key is wrong while
    429 means the key is fine and the account is out of quota, and those have
    opposite fixes.
    """
    status = exc.response.status_code
    hint = {
        401: "key rejected",
        403: "key lacks access",
        404: "listing endpoint not found",
        429: "rate limited or out of quota",
    }.get(status, f"HTTP {status}")
    try:
        body = exc.response.json()
        message = (
            body.get("error", {}).get("message")
            if isinstance(body.get("error"), dict)
            else body.get("message") or body.get("detail")
        )
    except ValueError:
        message = None
    return f"{hint}: {message}" if message else hint


class ModelDiscovery:
    """Cached, per-family live model listings."""

    def __init__(self) -> None:
        self._cache: dict[str, tuple[float, FamilyDiscovery]] = {}

    def invalidate(self, family: Optional[str] = None) -> None:
        """Drop cached listings, so a key change is reflected immediately."""
        if family is None:
            self._cache.clear()
        else:
            self._cache.pop(family, None)

    async def discover_all(self, *, refresh: bool = False) -> list[FamilyDiscovery]:
        results: list[FamilyDiscovery] = []
        async with build_pinned_client() as client:
            for family in PROVIDER_FAMILIES:
                results.append(
                    await self._discover_family(client, family, refresh=refresh)
                )
        return results

    async def _discover_family(
        self, client: httpx.AsyncClient, family: ProviderFamily, *, refresh: bool
    ) -> FamilyDiscovery:
        cached = self._cache.get(family.name)
        if cached and not refresh and (time.monotonic() - cached[0]) < CACHE_TTL_SECONDS:
            return cached[1]

        api_key = _resolved_key(family)
        if not api_key:
            outcome = FamilyDiscovery(
                family=family.name,
                label=family.label,
                configured=False,
                models=[],
            )
        elif (lister := _LISTERS.get(family.name)) is None:
            # Hugging Face has no single chat-model listing worth surfacing;
            # the key still works for a manually added entry.
            outcome = FamilyDiscovery(
                family=family.name,
                label=family.label,
                configured=True,
                models=[],
                error="this provider has no model listing endpoint; add models manually",
            )
        else:
            try:
                models = await lister(client, api_key, family)
                models.sort(key=lambda m: m.remote_id)
                outcome = FamilyDiscovery(
                    family=family.name,
                    label=family.label,
                    configured=True,
                    models=models,
                )
            except httpx.HTTPStatusError as exc:
                outcome = FamilyDiscovery(
                    family=family.name,
                    label=family.label,
                    configured=True,
                    models=[],
                    error=_describe_http_error(exc),
                )
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                logger.warning(
                    "model_discovery_failed",
                    extra={"family": family.name, "error": str(exc)},
                )
                outcome = FamilyDiscovery(
                    family=family.name,
                    label=family.label,
                    configured=True,
                    models=[],
                    error=f"{type(exc).__name__}: {exc}",
                )

        self._cache[family.name] = (time.monotonic(), outcome)
        return outcome


model_discovery = ModelDiscovery()
