"""A single model, called safely.

This is the piece worth lifting out of an application: the behaviour around a
model call that everyone needs and nobody wants to write twice -- retry with
the right classification, a credential resolved from the environment rather
than baked into a config file, cost accounting, JSON that parses even when the
model wraps it in prose, and an outbound request that cannot be pointed at the
metadata service.

One client is bound to one model. Routing between models, scoring their output
and merging it are application concerns and deliberately absent.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional

import litellm

from agent_llm.errors import LLMError, classify
from agent_llm.models import LLMResult, ModelSpec
from agent_llm.net import DEFAULT_ALLOWLIST, build_pinned_client, validate_endpoint

logger = logging.getLogger("agent_llm")

litellm.drop_params = True
litellm.suppress_debug_info = True

_JSON_FENCE = re.compile(r"```(?:json)?\s*(?P<body>[\s\S]*?)```", re.IGNORECASE)


@dataclass
class RetryPolicy:
    """How hard to try before giving up.

    Jitter is not decoration: without it, every concurrent caller that hit the
    same rate limit retries at the same instant and reproduces the burst that
    caused it.
    """

    max_attempts: int = 3
    base_delay_seconds: float = 1.5
    jitter: float = 0.25

    def delay_for(self, attempt: int) -> float:
        delay = self.base_delay_seconds * (2 ** (attempt - 1))
        return delay + random.uniform(0, delay * self.jitter)


@dataclass
class ClientOptions:
    timeout_seconds: int = 180
    max_parallel: int = 8
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    # Hosts permitted to be private. Local inference needs this; leaving it at
    # the default keeps localhost and the usual Compose service names working.
    endpoint_allowlist: Iterable[str] = DEFAULT_ALLOWLIST
    # Resolve and pin outbound connections. Off only makes sense when the
    # endpoint is a constant in your own code rather than configuration.
    pin_connections: bool = True
    # Called after every completed request. The place to attach metrics without
    # this package depending on a metrics library.
    on_result: Optional[Callable[[LLMResult], None]] = None


def estimate_tokens(text: str) -> int:
    """Cheap deterministic estimate, for budgets and for usage-less providers.

    Not a tokenizer and not trying to be: it exists so a provider that returns
    no usage block still produces a non-zero number, and so budget arithmetic
    never has to call out to one.
    """
    if not text:
        return 0
    return max(1, int(len(text.split()) * 1.35) + text.count("\n"))


def extract_json_object(text: str) -> dict[str, Any]:
    """Recover a JSON object from a reply that may not be only JSON.

    Handles fenced blocks, leading prose and trailing commentary by scanning
    for the outermost balanced brace pair, tracking string state so a brace
    inside a string value does not end the scan early.
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


class LLMClient:
    """Calls one model, with retries, accounting and a safe transport."""

    def __init__(
        self, model: ModelSpec, options: Optional[ClientOptions] = None
    ) -> None:
        self.model = model
        self.options = options or ClientOptions()
        self._semaphore = asyncio.Semaphore(self.options.max_parallel)

        if model.api_base:
            # Fail at construction rather than on the first call: a bad
            # endpoint is a configuration error, and finding out at startup is
            # far cheaper than finding out mid-task.
            validate_endpoint(
                model.api_base,
                allowlist=self.options.endpoint_allowlist,
                resolve=False,
            )
        if self.options.pin_connections:
            litellm.aclient_session = build_pinned_client(
                allowlist=self.options.endpoint_allowlist
            )

    # -- credentials -------------------------------------------------------

    def api_key(self) -> Optional[str]:
        """The credential, read from the environment at call time.

        Read fresh rather than captured at construction so a process that
        rotates a key in place picks it up, and so a spec can be built,
        serialised and passed around without ever holding a secret.
        """
        if not self.model.api_key_env:
            return None
        return os.environ.get(self.model.api_key_env, "").strip() or None

    def is_ready(self) -> bool:
        """False when this model declares a credential that is not set.

        Worth calling before dispatching work: a model with no key fails every
        attempt in the retry ladder first, which looks like an outage rather
        than a missing variable.
        """
        return not self.model.api_key_env or self.api_key() is not None

    # -- calling -----------------------------------------------------------

    async def complete(
        self,
        *,
        system: str,
        user: str,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        json_mode: bool = False,
        timeout: Optional[int] = None,
    ) -> LLMResult:
        request: dict[str, Any] = {
            "model": self.model.key,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": (
                self.model.temperature if temperature is None else temperature
            ),
            "max_tokens": max_tokens or self.model.max_tokens,
            "timeout": timeout or self.options.timeout_seconds,
        }
        if key := self.api_key():
            request["api_key"] = key
        if self.model.api_base:
            request["api_base"] = self.model.api_base
        if json_mode and self.model.supports_json_mode:
            request["response_format"] = {"type": "json_object"}

        started = time.perf_counter()
        last_error: Optional[BaseException] = None
        policy = self.options.retry

        async with self._semaphore:
            for attempt in range(1, policy.max_attempts + 1):
                try:
                    response = await litellm.acompletion(**request)
                except BaseException as exc:  # noqa: BLE001 - re-raised below
                    retryable = classify(exc)
                    if retryable is False:
                        raise LLMError(
                            str(exc), model=self.model.key, retryable=False
                        ) from exc
                    if retryable is None:
                        raise
                    last_error = exc
                    if attempt >= policy.max_attempts:
                        break
                    delay = policy.delay_for(attempt)
                    logger.warning(
                        "llm retry %s/%s for %s in %.1fs: %s",
                        attempt,
                        policy.max_attempts,
                        self.model.label,
                        delay,
                        exc,
                    )
                    await asyncio.sleep(delay)
                    continue

                result = self._build_result(
                    response, int((time.perf_counter() - started) * 1000), attempt
                )
                if self.options.on_result:
                    self.options.on_result(result)
                return result

        raise LLMError(
            str(last_error) if last_error else "unknown provider failure",
            model=self.model.key,
            retryable=True,
        )

    async def complete_json(
        self,
        *,
        system: str,
        user: str,
        max_tokens: Optional[int] = None,
        timeout: Optional[int] = None,
    ) -> tuple[dict[str, Any], LLMResult]:
        """Call the model and parse a JSON object out of the reply.

        One corrective retry is issued when the first reply does not parse,
        because models wrap JSON in commentary often enough that failing
        immediately wastes a usable answer. The returned result carries the
        summed cost of both attempts, so accounting stays honest.
        """
        result = await self.complete(
            system=system,
            user=user,
            temperature=0.0,
            max_tokens=max_tokens,
            json_mode=True,
            timeout=timeout,
        )
        try:
            return extract_json_object(result.content), result
        except ValueError:
            logger.warning("reply was not JSON, issuing one repair call")

        repair = await self.complete(
            system=system,
            user=(
                f"{user}\n\nYour previous reply was not valid JSON. Reply with a "
                "single JSON object and nothing else - no prose, no code fences."
            ),
            temperature=0.0,
            max_tokens=max_tokens,
            json_mode=True,
            timeout=timeout,
        )
        combined = LLMResult(
            content=repair.content,
            model=repair.model,
            input_tokens=result.input_tokens + repair.input_tokens,
            output_tokens=result.output_tokens + repair.output_tokens,
            cost_usd=round(result.cost_usd + repair.cost_usd, 6),
            latency_ms=result.latency_ms + repair.latency_ms,
            attempts=result.attempts + repair.attempts,
            finish_reason=repair.finish_reason,
        )
        return extract_json_object(repair.content), combined

    # -- internals ---------------------------------------------------------

    def _build_result(
        self, response: Any, latency_ms: int, attempts: int
    ) -> LLMResult:
        choices = getattr(response, "choices", None) or []
        if not choices:
            raise LLMError(
                "provider returned no choices", model=self.model.key, retryable=True
            )
        message = choices[0].message
        content = (getattr(message, "content", None) or "").strip()
        if not content:
            # Empty is treated as retryable: it is nearly always a truncated
            # stream or a transient provider hiccup, not a considered reply.
            raise LLMError(
                "provider returned empty content",
                model=self.model.key,
                retryable=True,
            )

        usage = getattr(response, "usage", None)
        input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        if not input_tokens and not output_tokens:
            # Some self-hosted runtimes omit usage entirely; an estimate keeps
            # budget arithmetic working rather than silently reporting zero.
            output_tokens = estimate_tokens(content)

        return LLMResult(
            content=content,
            model=self.model.key,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=self.model.estimate_cost(input_tokens, output_tokens),
            latency_ms=latency_ms,
            attempts=attempts,
            finish_reason=getattr(choices[0], "finish_reason", None),
            raw={"id": getattr(response, "id", None)},
        )
