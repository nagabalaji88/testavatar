"""The two values everything else is expressed in terms of.

Plain dataclasses rather than pydantic models: this package is meant to be
dropped into somebody else's application, and inheriting a validation
framework it may not use -- or may use at a different major version -- is the
kind of dependency that makes a library not worth adopting.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass(frozen=True)
class ModelSpec:
    """One callable model: what to call, where, and what it costs.

    `key` is passed to LiteLLM verbatim and must carry the provider prefix it
    expects -- "anthropic/claude-sonnet-5", "openai/gpt-4o",
    "ollama_chat/qwen2.5:7b-instruct". A bare model name is the single most
    common way to configure this wrongly: LiteLLM cannot infer the provider and
    rejects the call before it leaves the process, which reads as a model
    outage rather than a configuration error.
    """

    key: str
    name: str = ""
    max_tokens: int = 4096
    temperature: float = 0.4
    supports_json_mode: bool = True

    # Where to send it. None means LiteLLM's default for the provider.
    api_base: Optional[str] = None
    # Name of the environment variable holding the credential -- the name, not
    # the value, so a spec can be logged, serialised and committed safely.
    api_key_env: Optional[str] = None

    # Per-1k-token prices, used only for the cost figure on each result. Zero
    # is correct for a model you host yourself.
    cost_per_1k_input: float = 0.0
    cost_per_1k_output: float = 0.0

    def __post_init__(self) -> None:
        if not self.key:
            raise ValueError("ModelSpec.key is required")
        if self.max_tokens <= 0:
            raise ValueError("ModelSpec.max_tokens must be positive")

    @property
    def label(self) -> str:
        return self.name or self.key

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        return round(
            (input_tokens / 1000) * self.cost_per_1k_input
            + (output_tokens / 1000) * self.cost_per_1k_output,
            6,
        )


@dataclass
class LLMResult:
    """One completed call, with what it produced and what it cost."""

    content: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int
    attempts: int
    finish_reason: Optional[str] = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def truncated(self) -> bool:
        """The reply hit the output ceiling and stops mid-thought.

        Worth checking explicitly: a truncated reply is a successful call with
        an unusable result, so nothing raises and the damage surfaces later as
        a parse failure or a silently incomplete answer.
        """
        return self.finish_reason in {"length", "max_tokens"}
