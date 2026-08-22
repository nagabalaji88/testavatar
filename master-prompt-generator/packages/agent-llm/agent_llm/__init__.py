"""agent-llm — one model, called safely.

    from agent_llm import LLMClient, ModelSpec, from_env

    llm = from_env()                       # reads AGENT_LLM_MODEL et al.
    result = await llm.complete(
        system="You are a careful assistant.",
        user="Summarise this ticket.",
    )
    print(result.content, result.cost_usd)

What this gives you over calling a provider SDK directly: retries that
distinguish transient failures from permanent ones, credentials resolved from
the environment at call time, per-call cost accounting, JSON that parses
through prose, and outbound requests that cannot be redirected to an internal
address.
"""

from __future__ import annotations

import os
from typing import Optional

from agent_llm.client import (
    ClientOptions,
    LLMClient,
    RetryPolicy,
    estimate_tokens,
    extract_json_object,
)
from agent_llm.errors import LLMError
from agent_llm.models import LLMResult, ModelSpec
from agent_llm.net import (
    DEFAULT_ALLOWLIST,
    PinnedResolutionTransport,
    UnsafeEndpointError,
    build_pinned_client,
    validate_endpoint,
    validate_endpoint_async,
)

__version__ = "0.1.0"

__all__ = [
    "ClientOptions",
    "DEFAULT_ALLOWLIST",
    "LLMClient",
    "LLMError",
    "LLMResult",
    "ModelSpec",
    "PinnedResolutionTransport",
    "RetryPolicy",
    "UnsafeEndpointError",
    "build_pinned_client",
    "estimate_tokens",
    "extract_json_object",
    "from_env",
    "validate_endpoint",
    "validate_endpoint_async",
]


def from_env(prefix: str = "AGENT_LLM_") -> LLMClient:
    """Build a client from environment variables.

        AGENT_LLM_MODEL          anthropic/claude-sonnet-5   (required)
        AGENT_LLM_API_KEY_ENV    ANTHROPIC_API_KEY           (name, not value)
        AGENT_LLM_API_BASE       https://...                 (optional)
        AGENT_LLM_MAX_TOKENS     4096
        AGENT_LLM_TEMPERATURE    0.4
        AGENT_LLM_COST_IN        per 1k input tokens
        AGENT_LLM_COST_OUT       per 1k output tokens

    API_KEY_ENV names the variable holding the credential rather than the
    credential itself, so nothing here ever holds a secret and the whole
    configuration can be logged or committed.
    """
    key = os.environ.get(f"{prefix}MODEL", "").strip()
    if not key:
        raise ValueError(
            f"{prefix}MODEL is required, e.g. 'anthropic/claude-sonnet-5'. "
            "The provider prefix is not optional: without it LiteLLM cannot "
            "route the call."
        )

    def _float(name: str, default: float) -> float:
        raw = os.environ.get(f"{prefix}{name}", "").strip()
        return float(raw) if raw else default

    def _int(name: str, default: int) -> int:
        raw = os.environ.get(f"{prefix}{name}", "").strip()
        return int(raw) if raw else default

    api_key_env: Optional[str] = (
        os.environ.get(f"{prefix}API_KEY_ENV", "").strip() or None
    )
    api_base: Optional[str] = os.environ.get(f"{prefix}API_BASE", "").strip() or None

    return LLMClient(
        ModelSpec(
            key=key,
            name=key,
            api_key_env=api_key_env,
            api_base=api_base,
            max_tokens=_int("MAX_TOKENS", 4096),
            temperature=_float("TEMPERATURE", 0.4),
            cost_per_1k_input=_float("COST_IN", 0.0),
            cost_per_1k_output=_float("COST_OUT", 0.0),
        )
    )
