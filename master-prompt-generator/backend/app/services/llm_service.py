"""Unified async LLM access layer built on LiteLLM.

Responsibilities:
  * provider-agnostic completion calls with per-provider parameters,
  * bounded retries with exponential backoff and jitter,
  * concurrency limiting for fan-out,
  * token/cost accounting and telemetry emission,
  * strict JSON extraction for agents that require structured output.
"""

from __future__ import annotations

import asyncio
import json
import random
import re
import time
from dataclasses import dataclass, field
from typing import (
    Any,
    Awaitable,
    Callable,
    Iterable,
    Optional,
    Sequence,
    TypeVar,
    Union,
)

import litellm
from litellm.exceptions import (
    APIConnectionError,
    APIError,
    AuthenticationError,
    BadRequestError,
    ContextWindowExceededError,
    InternalServerError,
    RateLimitError,
    ServiceUnavailableError,
    Timeout,
)

from app.core.config import settings
from app.core.logging import get_logger
from app.core.telemetry import LLM_CALLS, LLM_COST, LLM_LATENCY, LLM_TOKENS, span
from app.models.schemas import ProviderConfig

logger = get_logger(__name__)

litellm.drop_params = True
litellm.set_verbose = False
litellm.suppress_debug_info = True

RETRYABLE: tuple[type[Exception], ...] = (
    RateLimitError,
    ServiceUnavailableError,
    APIConnectionError,
    Timeout,
    # litellm.exceptions.InternalServerError subclasses openai.APIError, not
    # litellm's own APIError below -- a completely separate hierarchy, so it
    # was previously caught by nothing here and crashed straight through any
    # bare call. A provider's transient 500 is as retryable as a 503; this is
    # what a fan-out-isolated candidate failure turned into an unhandled crash
    # of the whole run when it happened during analysis, judging, or the
    # consensus polish pass, none of which go through fan_out()'s per-provider
    # try/except.
    InternalServerError,
)
FATAL: tuple[type[Exception], ...] = (
    AuthenticationError,
    BadRequestError,
    ContextWindowExceededError,
)

T = TypeVar("T")

_JSON_FENCE = re.compile(r"```(?:json)?\s*(?P<body>[\s\S]*?)```", re.IGNORECASE)


class LLMError(RuntimeError):
    """Raised when a provider call cannot be completed."""

    def __init__(self, message: str, *, model_id: str, retryable: bool) -> None:
        super().__init__(message)
        self.model_id = model_id
        self.retryable = retryable


@dataclass
class LLMResult:
    model_id: str
    model_name: str
    provider: str
    content: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int
    attempts: int
    finish_reason: Optional[str] = None
    raw_metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class LLMFailure:
    model_id: str
    model_name: str
    provider: str
    error: str
    attempts: int
    latency_ms: int


# Runtimes that serve open-weight models from your own hardware. They take no
# credential, so a missing key must not be treated as a misconfiguration.
LOCAL_PROVIDERS = frozenset({"ollama", "vllm", "llamacpp", "llama.cpp", "local", "lmstudio"})


def _api_key_for(provider: ProviderConfig) -> Optional[str]:
    mapping = {
        "openai": settings.openai_api_key,
        "anthropic": settings.anthropic_api_key,
        "google": settings.gemini_api_key,
        "gemini": settings.gemini_api_key,
        "groq": settings.groq_api_key,
        "openrouter": settings.openrouter_api_key,
        "together": settings.together_api_key,
        "togetherai": settings.together_api_key,
        "huggingface": settings.huggingface_api_key,
    }
    return mapping.get(provider.provider.strip().lower())


def _api_base_for(provider: ProviderConfig) -> Optional[str]:
    """Resolve the endpoint, defaulting local runtimes to their configured host.

    An explicit api_base in the registry always wins, so a single models.json
    can be pointed at a remote GPU box without touching code.
    """
    if provider.api_base:
        return provider.api_base

    name = provider.provider.strip().lower()
    if name == "ollama":
        return settings.ollama_base_url
    if name in {"vllm", "llamacpp", "llama.cpp", "lmstudio", "local"}:
        return settings.vllm_base_url
    return None


def requires_credential(provider: ProviderConfig) -> bool:
    """True when the provider needs an API key that is not currently set."""
    if provider.provider.strip().lower() in LOCAL_PROVIDERS:
        return False
    return _api_key_for(provider) is None


def extract_json_object(text: str) -> dict[str, Any]:
    """Recover a JSON object from a model response.

    Handles fenced blocks, leading prose and trailing commentary by scanning
    for the outermost balanced brace pair.
    """
    candidates: list[str] = []

    fenced = _JSON_FENCE.search(text)
    if fenced:
        candidates.append(fenced.group("body").strip())

    stripped = text.strip()
    candidates.append(stripped)

    start = stripped.find("{")
    if start != -1:
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(stripped)):
            char = stripped[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(stripped[start : index + 1])
                    break

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(parsed, dict):
            return parsed

    raise ValueError("Response did not contain a JSON object")


def estimate_tokens(text: str) -> int:
    """Cheap deterministic token estimate used for budgets and compression stats."""
    if not text:
        return 0
    words = len(text.split())
    return max(1, int(words * 1.35) + text.count("\n"))


class LLMService:
    """Async facade over LiteLLM with retries, metrics and concurrency control."""

    def __init__(self, max_parallel: Optional[int] = None) -> None:
        self._semaphore = asyncio.Semaphore(
            max_parallel or settings.max_parallel_generations
        )

    async def complete(
        self,
        provider: ProviderConfig,
        *,
        system_prompt: str,
        user_prompt: str,
        phase: str,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        json_mode: bool = False,
        timeout: Optional[int] = None,
    ) -> LLMResult:
        """Invoke a provider once, retrying transient failures."""
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        request: dict[str, Any] = {
            "model": provider.model_key,
            "messages": messages,
            "temperature": (
                provider.temperature if temperature is None else temperature
            ),
            "max_tokens": max_tokens or provider.max_tokens,
            "timeout": timeout or settings.generation_timeout_seconds,
        }
        if api_key := _api_key_for(provider):
            request["api_key"] = api_key
        if api_base := _api_base_for(provider):
            request["api_base"] = api_base
        if json_mode and provider.supports_json_mode:
            request["response_format"] = {"type": "json_object"}

        started = time.perf_counter()
        last_error: Optional[Exception] = None

        async with self._semaphore:
            with span(
                "llm.complete",
                **{"llm.model_id": provider.id, "llm.phase": phase},
            ) as current_span:
                for attempt in range(1, settings.llm_max_retries + 1):
                    try:
                        response = await litellm.acompletion(**request)
                    except FATAL as exc:
                        latency_ms = int((time.perf_counter() - started) * 1000)
                        LLM_CALLS.labels(provider.id, phase, "fatal").inc()
                        logger.error(
                            "llm_call_fatal",
                            extra={
                                "model_id": provider.id,
                                "phase": phase,
                                "attempt": attempt,
                                "error": str(exc),
                                "latency_ms": latency_ms,
                            },
                        )
                        raise LLMError(
                            str(exc), model_id=provider.id, retryable=False
                        ) from exc
                    except RETRYABLE as exc:
                        last_error = exc
                        LLM_CALLS.labels(provider.id, phase, "retry").inc()
                        if attempt >= settings.llm_max_retries:
                            break
                        delay = settings.llm_retry_base_delay * (2 ** (attempt - 1))
                        delay += random.uniform(0, delay * 0.25)
                        logger.warning(
                            "llm_call_retry",
                            extra={
                                "model_id": provider.id,
                                "phase": phase,
                                "attempt": attempt,
                                "delay_seconds": round(delay, 2),
                                "error": str(exc),
                            },
                        )
                        await asyncio.sleep(delay)
                        continue
                    except APIError as exc:
                        last_error = exc
                        LLM_CALLS.labels(provider.id, phase, "error").inc()
                        break

                    latency_ms = int((time.perf_counter() - started) * 1000)
                    result = self._build_result(
                        provider, response, latency_ms, attempt
                    )
                    current_span.set_attribute("llm.input_tokens", result.input_tokens)
                    current_span.set_attribute(
                        "llm.output_tokens", result.output_tokens
                    )
                    current_span.set_attribute("llm.cost_usd", result.cost_usd)

                    LLM_CALLS.labels(provider.id, phase, "success").inc()
                    LLM_LATENCY.labels(provider.id, phase).observe(latency_ms / 1000)
                    LLM_TOKENS.labels(provider.id, "input").inc(result.input_tokens)
                    LLM_TOKENS.labels(provider.id, "output").inc(result.output_tokens)
                    LLM_COST.labels(provider.id).inc(result.cost_usd)

                    logger.info(
                        "llm_call_succeeded",
                        extra={
                            "model_id": provider.id,
                            "phase": phase,
                            "attempts": attempt,
                            "latency_ms": latency_ms,
                            "cost_usd": result.cost_usd,
                        },
                    )
                    return result

        LLM_CALLS.labels(provider.id, phase, "failed").inc()
        message = str(last_error) if last_error else "unknown provider failure"
        raise LLMError(message, model_id=provider.id, retryable=True)

    def _build_result(
        self,
        provider: ProviderConfig,
        response: Any,
        latency_ms: int,
        attempts: int,
    ) -> LLMResult:
        choices = getattr(response, "choices", None) or []
        if not choices:
            raise LLMError(
                "Provider returned no choices", model_id=provider.id, retryable=True
            )

        message = choices[0].message
        content = (getattr(message, "content", None) or "").strip()
        if not content:
            raise LLMError(
                "Provider returned empty content",
                model_id=provider.id,
                retryable=True,
            )

        usage = getattr(response, "usage", None)
        input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        if not input_tokens and not output_tokens:
            output_tokens = estimate_tokens(content)

        return LLMResult(
            model_id=provider.id,
            model_name=provider.name,
            provider=provider.provider,
            content=content,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=provider.estimate_cost(input_tokens, output_tokens),
            latency_ms=latency_ms,
            attempts=attempts,
            finish_reason=getattr(choices[0], "finish_reason", None),
            raw_metadata={"id": getattr(response, "id", None)},
        )

    async def complete_json(
        self,
        provider: ProviderConfig,
        *,
        system_prompt: str,
        user_prompt: str,
        phase: str,
        max_tokens: Optional[int] = None,
        timeout: Optional[int] = None,
    ) -> tuple[dict[str, Any], LLMResult]:
        """Call a provider and parse a JSON object out of the response.

        A single corrective retry is issued when the first response is not
        parseable, because models occasionally wrap JSON in commentary.
        """
        result = await self.complete(
            provider,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            phase=phase,
            temperature=0.0,
            max_tokens=max_tokens,
            json_mode=True,
            timeout=timeout,
        )
        try:
            return extract_json_object(result.content), result
        except ValueError:
            logger.warning(
                "llm_json_parse_retry",
                extra={"model_id": provider.id, "phase": phase},
            )

        repair = await self.complete(
            provider,
            system_prompt=system_prompt,
            user_prompt=(
                f"{user_prompt}\n\n"
                "Your previous reply was not valid JSON. Reply with a single "
                "JSON object and nothing else — no prose, no code fences."
            ),
            phase=f"{phase}.repair",
            temperature=0.0,
            max_tokens=max_tokens,
            json_mode=True,
            timeout=timeout,
        )
        merged = LLMResult(
            model_id=repair.model_id,
            model_name=repair.model_name,
            provider=repair.provider,
            content=repair.content,
            input_tokens=result.input_tokens + repair.input_tokens,
            output_tokens=result.output_tokens + repair.output_tokens,
            cost_usd=round(result.cost_usd + repair.cost_usd, 6),
            latency_ms=result.latency_ms + repair.latency_ms,
            attempts=result.attempts + repair.attempts,
            finish_reason=repair.finish_reason,
        )
        return extract_json_object(repair.content), merged

    async def fan_out(
        self,
        providers: Sequence[ProviderConfig],
        *,
        build_messages: Callable[[ProviderConfig], tuple[str, str]],
        phase: str,
        on_start: Optional[Callable[[ProviderConfig], Awaitable[None]]] = None,
        on_success: Optional[Callable[[LLMResult], Awaitable[None]]] = None,
        on_failure: Optional[Callable[[LLMFailure], Awaitable[None]]] = None,
    ) -> tuple[list[LLMResult], list[LLMFailure]]:
        """Execute providers concurrently, isolating per-provider failures."""

        async def _run(provider: ProviderConfig) -> Union[LLMResult, LLMFailure]:
            if on_start is not None:
                await on_start(provider)
            system_prompt, user_prompt = build_messages(provider)
            started = time.perf_counter()
            try:
                result = await self.complete(
                    provider,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    phase=phase,
                )
            except LLMError as exc:
                failure = LLMFailure(
                    model_id=provider.id,
                    model_name=provider.name,
                    provider=provider.provider,
                    error=str(exc),
                    attempts=settings.llm_max_retries,
                    latency_ms=int((time.perf_counter() - started) * 1000),
                )
                if on_failure is not None:
                    await on_failure(failure)
                return failure
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # defensive: never lose the fan-out
                failure = LLMFailure(
                    model_id=provider.id,
                    model_name=provider.name,
                    provider=provider.provider,
                    error=f"{type(exc).__name__}: {exc}",
                    attempts=1,
                    latency_ms=int((time.perf_counter() - started) * 1000),
                )
                if on_failure is not None:
                    await on_failure(failure)
                return failure

            if on_success is not None:
                await on_success(result)
            return result

        outcomes = await asyncio.gather(*(_run(p) for p in providers))
        results = [o for o in outcomes if isinstance(o, LLMResult)]
        failures = [o for o in outcomes if isinstance(o, LLMFailure)]
        return results, failures


def total_usage(results: Iterable[LLMResult]) -> tuple[int, int, float]:
    input_tokens = output_tokens = 0
    cost = 0.0
    for result in results:
        input_tokens += result.input_tokens
        output_tokens += result.output_tokens
        cost += result.cost_usd
    return input_tokens, output_tokens, round(cost, 6)


llm_service = LLMService()
