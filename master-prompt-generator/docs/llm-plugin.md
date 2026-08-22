# LLM Layer — portable specification

Everything needed to call an LLM correctly, as one document. Copy the code
blocks into any Python project; nothing here refers to another file, another
repository, or a framework.

**Who this is for:** an engineer or an AI assistant standing up the LLM layer
of a new agent application, who does not want to rediscover the same eight
failures.

**Dependencies:** `litellm` and `httpx`. Nothing else — no web framework, no
ORM, no settings library, no pydantic.

```bash
pip install litellm httpx
```

**Target layout** (names are yours to change):

```
your_app/llm/
    __init__.py     § 9
    models.py       § 2
    errors.py       § 3
    net.py          § 7
    client.py       § 4, 5, 6
```

---

## 0. What this solves

Calling a provider SDK is four lines. The next six months are spent adding the
things around it, and they are the same things every time.

| Failure | Without | With |
|---|---|---|
| Transient 500 | unhandled exception kills the task | retried with backoff |
| Bad API key | retried 3×, fails anyway | fails at once, marked fatal |
| Missing API key | surfaces mid-task as a provider outage | caught before any work |
| Bare model name | "LLM Provider NOT provided" | prefix enforced at config time |
| Model wraps JSON in prose | `json.loads` raises | recovered, or one repair call |
| Reply hits `max_tokens` | silent truncation, parsed as complete | `result.truncated` |
| Self-hosted model omits `usage` | cost reported as 0 | estimated, budgets survive |
| Configurable endpoint | SSRF into your metadata service | validated and pinned |

None is hard. Each is discovered in production.

---

## 1. Design rules

Five decisions everything else follows from. Deviating is fine, but know which
one you are deviating from.

1. **Catalogue and secrets are separate.** Model definitions are committed
   config; keys live in the environment. A model definition holds the *name* of
   the variable holding its key, never the key.
2. **Credentials are read at call time.** A key rotated in place is picked up
   without a restart, and a model definition can be logged and serialised
   freely.
3. **Config is injected, never a global singleton.** A module-level `settings`
   import is the single thing that makes an LLM layer unliftable.
4. **Enabled ≠ callable.** Whether an operator wants a model and whether it can
   run are different questions with different answers.
5. **Fail before spending.** Check credentials and budget before the call, not
   after.

---

## 2. Data model

```python
# llm/models.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass(frozen=True)
class ModelSpec:
    """One callable model: what to call, where, and what it costs."""

    key: str                      # passed to LiteLLM verbatim — see § 2.1
    name: str = ""
    max_tokens: int = 4096
    temperature: float = 0.4
    supports_json_mode: bool = True

    api_base: Optional[str] = None       # None = provider default
    api_key_env: Optional[str] = None    # variable NAME, never the value

    cost_per_1k_input: float = 0.0       # 0 is correct for self-hosted
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
        """Hit the output ceiling; the reply stops mid-thought.

        A truncated reply is a *successful* call with an unusable result, so
        nothing raises and the damage surfaces later as a parse failure or a
        silently incomplete answer. Check it explicitly.
        """
        return self.finish_reason in {"length", "max_tokens"}
```

Dataclasses rather than pydantic deliberately: a library that drags in a
validation framework — possibly at a conflicting major version — is one you
will not adopt.

### 2.1 `key` must carry the provider prefix

```
claude-3-5-sonnet-20240620   →  BadRequestError: LLM Provider NOT provided
anthropic/claude-sonnet-5    →  routes correctly
```

LiteLLM cannot infer the provider from a bare model name and rejects the call
**before it leaves the process**. The error reads like a provider outage, so it
gets misdiagnosed as one.

| Provider | Prefix |
|---|---|
| Anthropic | `anthropic/claude-sonnet-5` |
| OpenAI | `openai/gpt-4o` |
| Google | `gemini/gemini-2.0-flash` |
| Groq | `groq/llama-3.3-70b-versatile` |
| Ollama (local) | `ollama_chat/qwen2.5:7b-instruct` |
| Any OpenAI-compatible gateway | `openai/<model>` + `api_base` |

Use `openai/<model>` for a hosted gateway even when it serves an open-weight
model: that path sends the key as a bearer token, which `ollama_chat/` does not.

---

## 3. Retry classification

Retrying a 401 burns the full ladder and fails anyway. *Not* retrying a
transient 500 turns a blip into a dead run. Both mistakes come from catching
one broad exception class.

```python
# llm/errors.py
from __future__ import annotations

from typing import Optional

from litellm.exceptions import (
    APIConnectionError, APIError, AuthenticationError, BadRequestError,
    ContextWindowExceededError, InternalServerError, RateLimitError,
    ServiceUnavailableError, Timeout,
)


class LLMError(RuntimeError):
    def __init__(self, message: str, *, model: str, retryable: bool) -> None:
        super().__init__(message)
        self.model = model
        self.retryable = retryable


RETRYABLE: tuple[type[Exception], ...] = (
    RateLimitError,
    ServiceUnavailableError,
    APIConnectionError,
    Timeout,
    InternalServerError,   # see the warning below
)

FATAL: tuple[type[Exception], ...] = (
    AuthenticationError,
    BadRequestError,
    ContextWindowExceededError,
)


def classify(exc: BaseException) -> Optional[bool]:
    """True = retry, False = hopeless, None = unrecognised.

    None is distinct from False on purpose: an exception this module has never
    seen should not be silently treated as permanent.
    """
    if isinstance(exc, FATAL):
        return False
    if isinstance(exc, RETRYABLE):
        return True
    if isinstance(exc, APIError):
        return False
    return None
```

> **`InternalServerError` subclasses `openai.APIError`, not litellm's own
> `APIError`.** They are separate hierarchies, so `except APIError` looks
> exhaustive and catches nothing — a transient 500 escapes as an unhandled
> exception. It must be listed explicitly. This shipped as a real bug in the
> source application and crashed entire runs.

**Backoff must have jitter.** Without it, every caller that hit the same rate
limit retries at the same instant and reproduces the burst that caused it.

```python
delay = base_delay * (2 ** (attempt - 1))
delay += random.uniform(0, delay * 0.25)
```

---

## 4. Credentials

Three sources, most specific first:

1. `spec.api_key_env` — the entry names its own variable
2. A literal in the entry — only safe if the config file is mounted from
   outside your repository
3. A provider-family default — `{"anthropic": "ANTHROPIC_API_KEY", ...}`

```python
def api_key(self) -> Optional[str]:
    if not self.model.api_key_env:
        return None
    # Blank is a MISSING credential, not an empty one: passing "" reads as
    # "no auth" on some providers and as a malformed header on others, both
    # of which fail downstream with a far worse error than "variable unset".
    return os.environ.get(self.model.api_key_env, "").strip() or None


def is_ready(self) -> bool:
    """False when this model declares a credential that is not set."""
    return not self.model.api_key_env or self.api_key() is not None
```

Call `is_ready()` **before dispatching work**. Without it a missing key fails
every attempt in the retry ladder first, which looks like an outage.

### 4.1 Enabled vs callable

If your app has a model catalogue with an on/off switch, report both:

| Field | Meaning |
|---|---|
| `enabled` | an operator wants this model |
| `credential_available` | it can actually be called right now |
| `credential_env_var` | which variable to set — the **name** |
| `is_local_runtime` | self-hosted, so no key applies at all |

Three traps:

- **A hosted deployment of an open-weight model still reports `"Ollama"`** as
  its provider. A family check treats it as key-free and lets a run start
  against an endpoint that 401s every call. The *declared credential source* is
  what distinguishes them, not the family name.
- **Name the variable, not just the state.** "Unavailable" leaves the operator
  guessing which of eight keys is missing.
- **Keep `is_local_runtime` separate from `credential_available`.** A local
  model is not a model with a missing key; showing it as one reads as a problem
  to fix.

---

## 5. The client

```python
# llm/client.py
from __future__ import annotations

import asyncio, json, logging, os, random, re, time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional

import litellm

from .errors import LLMError, classify
from .models import LLMResult, ModelSpec
from .net import DEFAULT_ALLOWLIST, build_pinned_client, validate_endpoint

logger = logging.getLogger("llm")
litellm.drop_params = True
litellm.suppress_debug_info = True


@dataclass
class RetryPolicy:
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
    endpoint_allowlist: Iterable[str] = DEFAULT_ALLOWLIST
    pin_connections: bool = True
    on_result: Optional[Callable[[LLMResult], None]] = None   # metrics hook


def estimate_tokens(text: str) -> int:
    """Cheap deterministic estimate for budgets and usage-less providers."""
    if not text:
        return 0
    return max(1, int(len(text.split()) * 1.35) + text.count("\n"))


class LLMClient:
    def __init__(self, model: ModelSpec,
                 options: Optional[ClientOptions] = None) -> None:
        self.model = model
        self.options = options or ClientOptions()
        self._semaphore = asyncio.Semaphore(self.options.max_parallel)

        if model.api_base:
            # Fail at construction: a bad endpoint is a config error, and
            # finding out at startup is far cheaper than mid-task.
            validate_endpoint(model.api_base,
                              allowlist=self.options.endpoint_allowlist,
                              resolve=False)
        if self.options.pin_connections:
            litellm.aclient_session = build_pinned_client(
                allowlist=self.options.endpoint_allowlist)

    def api_key(self) -> Optional[str]:
        if not self.model.api_key_env:
            return None
        return os.environ.get(self.model.api_key_env, "").strip() or None

    def is_ready(self) -> bool:
        return not self.model.api_key_env or self.api_key() is not None

    async def complete(self, *, system: str, user: str,
                       temperature: Optional[float] = None,
                       max_tokens: Optional[int] = None,
                       json_mode: bool = False,
                       timeout: Optional[int] = None) -> LLMResult:
        request: dict[str, Any] = {
            "model": self.model.key,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "temperature": (self.model.temperature
                            if temperature is None else temperature),
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
                except BaseException as exc:
                    retryable = classify(exc)
                    if retryable is False:
                        raise LLMError(str(exc), model=self.model.key,
                                       retryable=False) from exc
                    if retryable is None:
                        raise                      # unknown: do not swallow
                    last_error = exc
                    if attempt >= policy.max_attempts:
                        break
                    await asyncio.sleep(policy.delay_for(attempt))
                    continue

                result = self._build_result(
                    response, int((time.perf_counter() - started) * 1000), attempt)
                if self.options.on_result:
                    self.options.on_result(result)
                return result

        raise LLMError(str(last_error) if last_error else "unknown failure",
                       model=self.model.key, retryable=True)

    def _build_result(self, response: Any, latency_ms: int,
                      attempts: int) -> LLMResult:
        choices = getattr(response, "choices", None) or []
        if not choices:
            raise LLMError("provider returned no choices",
                           model=self.model.key, retryable=True)
        content = (getattr(choices[0].message, "content", None) or "").strip()
        if not content:
            # Retryable: nearly always a truncated stream or a hiccup, not a
            # considered empty reply.
            raise LLMError("provider returned empty content",
                           model=self.model.key, retryable=True)

        usage = getattr(response, "usage", None)
        input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        if not input_tokens and not output_tokens:
            # Self-hosted runtimes often omit usage. Zero would quietly break
            # every budget built on it.
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
```

---

## 6. Structured output

Models wrap JSON in prose, fences and commentary often enough that failing on
the first attempt throws away a usable answer.

> The pattern below matches a Markdown code fence, so it contains three
> backticks and this block is fenced with four to keep them intact. Copy the
> code, not the outer fence.

````python
_JSON_FENCE = re.compile(r"```(?:json)?\s*(?P<body>[\s\S]*?)```", re.IGNORECASE)


def extract_json_object(text: str) -> dict[str, Any]:
    """Recover a JSON object from a reply that is not only JSON."""
    candidates: list[str] = []

    fenced = _JSON_FENCE.search(text)
    if fenced:
        candidates.append(fenced.group("body").strip())

    stripped = text.strip()
    candidates.append(stripped)

    # Outermost balanced brace pair, tracking string state so a brace inside
    # a value does not end the scan early.
    start = stripped.find("{")
    if start != -1:
        depth, in_string, escaped = 0, False, False
        for index in range(start, len(stripped)):
            char = stripped[index]
            if in_string:
                if escaped:            escaped = False
                elif char == "\\":     escaped = True
                elif char == '"':      in_string = False
                continue
            if char == '"':            in_string = True
            elif char == "{":          depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(stripped[start:index + 1])
                    break

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("Response did not contain a JSON object")
````

One corrective retry, with **both attempts billed** — charging only the second
under-reports:

```python
async def complete_json(self, *, system: str, user: str,
                        max_tokens: Optional[int] = None,
                        timeout: Optional[int] = None
                        ) -> tuple[dict[str, Any], LLMResult]:
    result = await self.complete(system=system, user=user, temperature=0.0,
                                 max_tokens=max_tokens, json_mode=True,
                                 timeout=timeout)
    try:
        return extract_json_object(result.content), result
    except ValueError:
        logger.warning("reply was not JSON, issuing one repair call")

    repair = await self.complete(
        system=system,
        user=(f"{user}\n\nYour previous reply was not valid JSON. Reply with "
              "a single JSON object and nothing else - no prose, no fences."),
        temperature=0.0, max_tokens=max_tokens, json_mode=True, timeout=timeout)

    combined = LLMResult(
        content=repair.content, model=repair.model,
        input_tokens=result.input_tokens + repair.input_tokens,
        output_tokens=result.output_tokens + repair.output_tokens,
        cost_usd=round(result.cost_usd + repair.cost_usd, 6),
        latency_ms=result.latency_ms + repair.latency_ms,
        attempts=result.attempts + repair.attempts,
        finish_reason=repair.finish_reason)
    return extract_json_object(repair.content), combined
```

**Output ceilings** for phases returning a small structured document: set
`max_tokens` to ~1500 rather than inheriting a 4096+ default. These are
ceilings, not targets — a well-behaved model stops at its stop token anyway —
so they bound the worst case. Size with headroom: a truncated reply fails to
parse and costs a repair round-trip, which exceeds the tokens saved.

---

## 7. Endpoint safety

**Skip this section only if every endpoint is a literal constant in your own
source.** The moment configuration, a database row or a tenant can choose where
a call goes, you have handed out a server-side fetch. Point it at
`169.254.169.254` and cloud credentials come back inside your product.

```python
# llm/net.py
from __future__ import annotations

import asyncio, ipaddress, socket
from typing import Iterable, Optional
from urllib.parse import urlparse

import httpx

ALLOWED_SCHEMES = frozenset({"http", "https"})
BLOCKED_ADDRESSES = frozenset({"169.254.169.254", "fd00:ec2::254",
                               "100.100.100.200"})
DEFAULT_ALLOWLIST = frozenset({"localhost", "127.0.0.1", "::1",
                               "ollama", "vllm", "host.docker.internal"})


class UnsafeEndpointError(ValueError):
    pass


def _is_private(host: str) -> bool:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return (address.is_private or address.is_loopback or address.is_link_local
            or address.is_reserved or address.is_unspecified)


def _resolve(host: str) -> list[str]:
    """Every address `host` resolves to. Empty when it does not resolve --
    not an error: a config may name an endpoint whose DNS is not live yet,
    and a name resolving to nothing reaches nothing."""
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return []
    return [info[4][0] for info in infos]


def _reject_if_internal(host: str, address: str) -> None:
    if address in BLOCKED_ADDRESSES:
        raise UnsafeEndpointError(
            f"'{host}' resolves to {address}, an instance-metadata address")
    if _is_private(address):
        raise UnsafeEndpointError(
            f"'{host}' resolves to the private address {address}")


def validate_endpoint(url: str, *, allowlist: Iterable[str] = DEFAULT_ALLOWLIST,
                      resolve: bool = True) -> str:
    """resolve=False skips DNS. getaddrinfo BLOCKS, so pass False anywhere
    this runs on an event loop and use validate_endpoint_async instead."""
    parsed = urlparse(url)
    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise UnsafeEndpointError(
            f"must use http or https, got '{parsed.scheme or 'none'}'")
    if parsed.username or parsed.password:
        raise UnsafeEndpointError("must not embed credentials")

    host = (parsed.hostname or "").strip().lower()
    if not host:
        raise UnsafeEndpointError("must include a host")
    if host in BLOCKED_ADDRESSES:
        raise UnsafeEndpointError(f"'{host}' is an instance-metadata address")

    if host in {e.strip().lower() for e in allowlist}:
        return url
    if _is_private(host):
        raise UnsafeEndpointError(
            f"'{host}' is a private address; add it to the allowlist to permit it")
    if not resolve:
        return url

    for address in _resolve(host):
        _reject_if_internal(host, address)
    return url


async def validate_endpoint_async(
        url: str, *, allowlist: Iterable[str] = DEFAULT_ALLOWLIST) -> str:
    validate_endpoint(url, allowlist=allowlist, resolve=False)
    return await asyncio.to_thread(validate_endpoint, url,
                                   allowlist=allowlist, resolve=True)


async def pick_safe_address(
        host: str, *, allowlist: Iterable[str] = DEFAULT_ALLOWLIST
        ) -> Optional[str]:
    """Return an address that passed validation, so the caller can connect to
    the exact one it checked. None = allowlisted or unresolvable; proceed
    unmodified."""
    if host in BLOCKED_ADDRESSES:
        raise UnsafeEndpointError(f"'{host}' is an instance-metadata address")
    if host.strip().lower() in {e.strip().lower() for e in allowlist}:
        return None                      # allowlisted *because* it is private
    addresses = await asyncio.to_thread(_resolve, host)
    if not addresses:
        return None
    for address in addresses:
        _reject_if_internal(host, address)
    return addresses[0]


def _is_ip_literal(host: Optional[str]) -> bool:
    if not host:
        return False
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def _authority(url: httpx.URL) -> str:
    default = 443 if url.scheme == "https" else 80
    return (f"{url.host}:{url.port}"
            if url.port and url.port != default else url.host)


class PinnedResolutionTransport(httpx.AsyncHTTPTransport):
    """Connect only to an address this transport just validated."""

    def __init__(self, *, allowlist: Iterable[str] = DEFAULT_ALLOWLIST,
                 **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._allowlist = frozenset(e.strip().lower() for e in allowlist)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if _is_ip_literal(host):
            return await super().handle_async_request(request)

        address = await pick_safe_address(host, allowlist=self._allowlist)
        if address is None:
            return await super().handle_async_request(request)

        original_host = request.headers.get("Host") or _authority(request.url)
        request.url = request.url.copy_with(host=address)
        request.headers["Host"] = original_host
        # Without SNI the handshake presents the bare IP and certificate
        # verification fails for every HTTPS provider.
        request.extensions = {**request.extensions, "sni_hostname": host}
        return await super().handle_async_request(request)


def build_pinned_client(*, allowlist: Iterable[str] = DEFAULT_ALLOWLIST
                        ) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=PinnedResolutionTransport(allowlist=allowlist))
```

### Why two checks

A literal check alone stops only the obvious spelling:

```
blocked  http://169.254.169.254/
ALLOWED  http://localtest.me/        ← public name, resolves to 127.0.0.1
ALLOWED  http://127.0.0.1.nip.io/    ← same trick, different wildcard DNS
```

So names are resolved and every answer checked. And validating on *accept* is
still not enough:

> **A stored endpoint outlives its validation by days.** The practical attack
> needs no race: supply a name that resolves somewhere harmless, wait for it to
> be accepted, then repoint the DNS record. Nothing revalidates before the
> request goes out.

Hence pinning at send time. Preserve the hostname for `Host` and SNI or
certificate verification fails against the bare IP.

**Do not pin allowlisted hosts** — they are allowlisted *because* they are
private, and pinning breaks the local runtimes the allowlist exists for.

---

## 8. Guarding the caller

Two preflights, both cheap:

```python
# Before dispatching work
if not client.is_ready():
    raise SystemExit(f"set {client.model.api_key_env} for {client.model.label}")

# Before each call, not after — a budget checked afterwards has already spent
if spent >= budget:
    raise RuntimeError(f"budget exhausted: ${spent:.4f} of ${budget:.2f}")
result = await client.complete(system=..., user=...)
spent += result.cost_usd
```

**Never serve provider exception text to end users.** The message is not built
from a traceback, but provider SDKs embed their own inside it, so absolute
paths and dependency versions reach the caller:

```
File "/usr/local/lib/python3.11/dist-packages/litellm/main.py", line 647
```

Strip frames and paths, keep the actionable part — `"Missing Gemini API key"`
must survive, or you have replaced a diagnosable error with a blank:

```python
_TRACEBACK_HEADER = re.compile(r"Traceback \(most recent call last\):.*", re.DOTALL)
_TRACEBACK_LINE   = re.compile(r'\s*File "[^"]+", line \d+.*', re.MULTILINE)
_ABS_PATH         = re.compile(r"(?:/[\w.\-]+){2,}/[\w.\-]+\.py\b")


def client_safe_error(message: str, *, reveal_internals: bool) -> str:
    if reveal_internals:                     # local dev wants the raw text
        return message
    cleaned = _TRACEBACK_HEADER.sub("", message)
    cleaned = _TRACEBACK_LINE.sub("", cleaned)
    cleaned = _ABS_PATH.sub("<path>", cleaned)
    cleaned = " ".join(cleaned.split()).strip()
    if not cleaned:
        return "The provider call failed. See the server log for details."
    return cleaned[:400].rstrip() + "…" if len(cleaned) > 400 else cleaned
```

---

## 9. Wiring it up

```python
# llm/__init__.py
import os
from typing import Optional

from .client import ClientOptions, LLMClient, RetryPolicy, extract_json_object
from .errors import LLMError
from .models import LLMResult, ModelSpec
from .net import UnsafeEndpointError, validate_endpoint, validate_endpoint_async


def from_env(prefix: str = "LLM_") -> LLMClient:
    """
        LLM_MODEL         anthropic/claude-sonnet-5   (required, with prefix)
        LLM_API_KEY_ENV   ANTHROPIC_API_KEY           (name, not value)
        LLM_API_BASE      https://...                 (optional)
        LLM_MAX_TOKENS    4096
        LLM_COST_IN       0.003
        LLM_COST_OUT      0.015
    """
    key = os.environ.get(f"{prefix}MODEL", "").strip()
    if not key:
        raise ValueError(
            f"{prefix}MODEL is required, e.g. 'anthropic/claude-sonnet-5'. "
            "The provider prefix is not optional.")

    def _f(name: str, default: float) -> float:
        raw = os.environ.get(f"{prefix}{name}", "").strip()
        return float(raw) if raw else default

    def _i(name: str, default: int) -> int:
        raw = os.environ.get(f"{prefix}{name}", "").strip()
        return int(raw) if raw else default

    return LLMClient(ModelSpec(
        key=key, name=key,
        api_key_env=os.environ.get(f"{prefix}API_KEY_ENV", "").strip() or None,
        api_base=os.environ.get(f"{prefix}API_BASE", "").strip() or None,
        max_tokens=_i("MAX_TOKENS", 4096),
        temperature=_f("TEMPERATURE", 0.4),
        cost_per_1k_input=_f("COST_IN", 0.0),
        cost_per_1k_output=_f("COST_OUT", 0.0)))
```

### Usage

```python
from your_app.llm import from_env

llm = from_env()
if not llm.is_ready():
    raise SystemExit(f"set {llm.model.api_key_env}")

result = await llm.complete(system="You are precise.", user="Summarise this.")
print(result.content, result.cost_usd, result.attempts, result.truncated)

payload, result = await llm.complete_json(
    system="Reply with JSON only.", user="Extract name and amount.")
```

### A three-step agent

```python
class Agent:
    def __init__(self, llm, budget_usd: float = 0.50):
        self.llm, self.budget_usd, self.spent_usd = llm, budget_usd, 0.0

    async def step(self, name: str, system: str, user: str) -> str:
        if self.spent_usd >= self.budget_usd:
            raise RuntimeError(f"budget exhausted before '{name}'")
        result = await self.llm.complete(system=system, user=user)
        self.spent_usd += result.cost_usd
        if result.truncated:
            print(f"! {name} hit the output ceiling; reply is incomplete")
        return result.content

    async def run(self, task: str) -> str:
        plan = await self.step("plan",
            "You are a planner. Reply with 2-4 numbered steps, nothing else.",
            f"Task: {task}")
        draft = await self.step("execute",
            "You are an execution agent. Follow the plan exactly.",
            f"Task: {task}\n\nPlan:\n{plan}")
        return await self.step("review",
            "You are a reviewer. Return the corrected final answer only.",
            f"Task: {task}\n\nDraft:\n{draft}")
```

---

## 10. Configuration reference

```bash
# Model — the prefix is mandatory
LLM_MODEL=anthropic/claude-sonnet-5
LLM_API_KEY_ENV=ANTHROPIC_API_KEY
ANTHROPIC_API_KEY=sk-ant-...

# Optional
LLM_API_BASE=https://your-gateway/v1
LLM_MAX_TOKENS=8192
LLM_TEMPERATURE=0.4
LLM_COST_IN=0.003
LLM_COST_OUT=0.015
```

Common specs:

```python
ModelSpec(key="anthropic/claude-sonnet-5", api_key_env="ANTHROPIC_API_KEY",
          max_tokens=8192, cost_per_1k_input=0.003, cost_per_1k_output=0.015)

ModelSpec(key="openai/gpt-4o", api_key_env="OPENAI_API_KEY",
          cost_per_1k_input=0.0025, cost_per_1k_output=0.01)

ModelSpec(key="ollama_chat/qwen2.5:7b-instruct",     # local, no key, free
          api_base="http://localhost:11434")

ModelSpec(key="openai/llama-3.3-70b",                # OpenAI-compatible gateway
          api_base="https://your-gateway/v1", api_key_env="GATEWAY_KEY")
```

---

## 11. Adapting it

**Several models instead of one.** Hold `dict[str, ModelSpec]`, build a client
per spec. Drop uncredentialed models from any *default* selection — otherwise
each is dispatched and burns its full retry ladder, so a run with no usable keys
dies seconds later behind a wall of stacked exceptions instead of naming the
variable to set. Honour an *explicit* selection as given, so an operator can
force a model and see its real error.

**Operator-editable catalogue.** Load specs from a JSON file, re-reading when
its mtime changes. Caching on first read means one process serves a model that
another has never heard of. Keep secrets out of that file — `api_key_env` is
what makes this safe.

**Streaming.** `litellm.acompletion(..., stream=True)` yields chunks; accumulate
them and build `LLMResult` at the end. Retry logic still applies to establishing
the stream, not to a stream that fails midway — decide explicitly which you want.

**Metrics.** `ClientOptions.on_result` fires after every call. That hook exists
so this layer never depends on a metrics library.

**Concurrent cost accumulation.** If several tasks write a shared total, use an
atomic increment (`SET total = total + :n` in SQL). Read-modify-write in Python
loses updates under concurrency — a real bug in the source application.

**Sync instead of async.** Swap `litellm.acompletion` → `litellm.completion`,
drop the semaphore, use `time.sleep`. Connection pinning then needs
`httpx.Client` with a sync transport subclass instead.

---

## 12. Checklist

Before shipping, confirm each:

- [ ] Every `key` carries a provider prefix
- [ ] No credential is in a committed file — only variable *names*
- [ ] Keys read at call time, not captured at construction
- [ ] `InternalServerError` is in your retryable tuple
- [ ] Auth errors are not retried
- [ ] Backoff has jitter
- [ ] Readiness checked before work starts
- [ ] Budget checked *before* each call
- [ ] `truncated` inspected on replies you parse
- [ ] Endpoints validated, and pinned if they come from config
- [ ] Provider exception text is not served to end users
- [ ] Config injected rather than imported from a global singleton
```
