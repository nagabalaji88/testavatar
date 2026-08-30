# How LLMs are configured and called

A reference for reusing this application's LLM layer in a different agent app.
It describes what each piece does, why it is shaped that way, and which parts
are worth copying versus which are specific to this application.

Every file path below is real and can be read alongside this document.

---

## 1. The shape of it

Five layers, each with one job:

```
.env / environment            secrets and tuning
      ↓
app/core/config.py            typed settings, validated at import
      ↓
backend/config/models.json    the model catalogue (no secrets)
      ↓
app/services/model_registry.py  loads it, resolves a selection
      ↓
app/services/llm_service.py   resolves credentials, calls, retries, accounts
      ↓
LiteLLM → provider
```

The important separation is between the **catalogue** and the **secrets**.
`models.json` is committed; `.env` is gitignored. Nothing in the catalogue is
confidential, so it can be reviewed, diffed and shipped like any other config.

---

## 2. A model entry

`backend/config/models.json`, validated by `ProviderConfig` in
`app/models/schemas.py`:

```json
{
  "id": "anthropic-claude-sonnet",
  "name": "Claude Sonnet 5",
  "provider": "Anthropic",
  "model_key": "anthropic/claude-sonnet-5",
  "max_tokens": 8192,
  "cost_per_1k_input": 0.003,
  "cost_per_1k_output": 0.015,
  "enabled": true,
  "temperature": 0.4,
  "supports_json_mode": false,
  "api_base": null,
  "api_key_env": null,
  "weight": 1.0
}
```

| Field | Purpose |
|---|---|
| `id` | Stable handle. Referenced by `ANALYZER_MODEL_ID` and friends, so renaming one breaks config that points at it. |
| `model_key` | Passed to LiteLLM verbatim. **The provider prefix is mandatory** — see §3. |
| `api_base` | Override the endpoint. Validated for SSRF — see §7. |
| `api_key_env` | Names the variable holding this entry's key. Never the key itself. |
| `cost_per_1k_*` | Only used to compute the cost figure on each result. Zero for self-hosted. |
| `enabled` | Operator intent. Distinct from *callable* — see §5. |

### `model_key`: the mistake to avoid

```
claude-3-5-sonnet-20240620   →  BadRequestError: LLM Provider NOT provided
anthropic/claude-sonnet-5    →  routes correctly
```

LiteLLM cannot infer the provider from a bare model name and rejects the call
**before it leaves the process**. The error reads like a provider outage, so it
is easy to misdiagnose. This exact bug shipped in this repo's registry and made
that entry permanently unusable regardless of credentials.

Prefixes: `anthropic/`, `openai/`, `gemini/`, `groq/`, `ollama_chat/`. Use
`openai/<model>` for any OpenAI-compatible gateway — that path sends the key as
a bearer token, which the native `ollama_chat/` path does not.

---

## 3. Where credentials come from

`_api_key_for()` in `app/services/llm_service.py`. Three sources, most specific
first:

1. **`api_key_env`** — the entry names a variable; read from `os.environ`.
2. **`api_key`** — a literal in the entry. Only safe when `models.json` is
   mounted from outside the repository, since the committed file is tracked.
3. **Provider family** — `PROVIDER_KEY_ENV_VARS` maps `"anthropic"` →
   `ANTHROPIC_API_KEY`, resolved through `settings`.

Two rules worth carrying over:

**A blank variable is a missing credential, not an empty key.** Passing `""` to
a provider reads as "no auth" on some and as a malformed header on others; both
fail further downstream with a much worse error than "the variable is unset".

**Read at call time, not at construction.** A key rotated in place is then
picked up without a restart, and a model entry can be built, logged and
serialised without ever holding a secret.

---

## 4. Which model plays which role

Three settings name the model for each pipeline stage:

```bash
ANALYZER_MODEL_ID=anthropic-claude-sonnet
JUDGE_MODEL_ID=anthropic-claude-sonnet
CONSENSUS_MODEL_ID=anthropic-claude-sonnet
```

These are `id` values from the catalogue, not model keys. A role pointed at an
entry with no credential **degrades silently**: the analyzer falls back to a
deterministic heuristic, and the judge and consensus stages fail their calls.
The run still completes, with a noticeably worse result and no obvious cause.
If you copy this pattern, validate the role pointers at startup.

---

## 5. Enabled is not the same as callable

`enabled` says an operator wants the model. Whether it can actually be called
depends on the credential, which lives in the environment. The two must be
answered separately or the UI offers models that cannot run.

`requires_credential()` decides; `_to_public()` in
`app/api/v1/endpoints.py` reports it:

```
credential_available   can this be called right now
credential_env_var     which variable to set — the name, not the value
is_local_runtime       served from your own hardware, so no key applies
```

Two traps this closed:

**A hosted deployment of an open-weight model still reads `"Ollama"`** in
`provider`, so a provider-family check treats it as key-free and lets a run
start against an endpoint that answers 401 on every call. The declared
credential source is what distinguishes them, not the family name.

**Name the variable, not just the state.** "Unavailable" alone leaves an
operator guessing which of eight keys is missing, and once entries can carry
their own `api_key_env` it is no longer inferable from the provider name.

`is_local_runtime` is deliberately separate from `credential_available`: a
local model is not a model with a missing key, and showing it as one reads as a
problem to fix.

---

## 6. Calling: retries, accounting, JSON

`LLMService.complete()` in `app/services/llm_service.py`.

### Retry classification

The distinction matters more than it looks. Retrying a 401 burns the whole
ladder and fails anyway; not retrying a transient 500 turns a blip into a dead
run.

```python
RETRYABLE = (RateLimitError, ServiceUnavailableError,
             APIConnectionError, Timeout, InternalServerError)
FATAL     = (AuthenticationError, BadRequestError, ContextWindowExceededError)
```

**`InternalServerError` subclasses `openai.APIError`, not litellm's own.** An
`except APIError` that looks exhaustive catches nothing, and a transient 500
escapes as an unhandled exception. It has to be listed explicitly. This was a
real bug here: it crashed whole runs from the analysis, judging and consensus
stages, none of which sit behind the fan-out's per-provider try/except.

Backoff is exponential with jitter. Jitter is not decoration — without it every
caller that hit the same rate limit retries at the same instant and reproduces
the burst.

### Accounting

Each call returns `LLMResult` with `input_tokens`, `output_tokens`, `cost_usd`,
`latency_ms`, `attempts`. Two details:

- **When a provider omits `usage`** — common on self-hosted runtimes — tokens
  are estimated rather than reported as zero, so budgets keep working.
- **Accumulate with an atomic SQL increment**, not read-modify-write. With
  concurrent stages writing the same row, Python-side accumulation loses
  updates. `_accumulate_usage()` in `app/agents/graph.py` shows the pattern.

### JSON

`complete_json()` extracts an object from a reply that may not be only JSON:
fenced blocks, leading prose, trailing commentary. The brace scan tracks string
state so a `}` inside a value does not end it early.

If the first reply still does not parse, **one** corrective call is issued, and
the returned result carries the summed cost of both. Charging only the second
would under-report.

### Output ceilings

`analysis_max_tokens` and `judge_max_tokens` (1536) bound phases that return a
small structured document. These are ceilings, not targets — a well-behaved
model stops at its stop token anyway — so they bound the worst case rather than
shortening the typical one. Size them with headroom: a truncated reply fails to
parse and costs a repair round-trip, which is more expensive than the tokens
saved.

---

## 7. A configurable endpoint is a server-side fetch

If an operator, tenant or config file can choose where a model call goes, you
have handed out an SSRF primitive. `app/core/net.py` and
`app/core/pinned_transport.py`.

**Two checks, because one is not enough.**

**On accept** — `validate_api_base()` rejects non-HTTP schemes, embedded
credentials, instance-metadata addresses and private literals. Private hosts
are permitted by explicit name through `API_BASE_ALLOWLIST`, because local
inference legitimately needs them.

A literal check alone only stops the obvious spelling:

```
blocked  http://169.254.169.254/
ALLOWED  http://localtest.me/       ← public name, resolves to 127.0.0.1
```

So the name is resolved and every answer checked.

**On send** — `PinnedResolutionTransport` resolves the hostname, validates the
answers, and connects to the address it just checked rather than to the name.

That second check is not about a millisecond race. **A registry entry outlives
its validation by days**, so the practical attack needs no race: supply a name
that resolves somewhere harmless, wait for it to be accepted, then repoint the
DNS record.

The hostname is preserved for the `Host` header and TLS SNI, or certificate
verification fails against the bare IP and every HTTPS provider breaks.

`getaddrinfo` blocks, so it must not run on the event loop — the schema
validator uses `resolve=False` and the admin write path awaits
`validate_api_base_async()`, which resolves on a worker thread.

---

## 8. Failing fast

Two preflights, both worth copying:

**Drop uncredentialed models from a default selection.** `ModelRegistry.resolve()`
filters them out when the caller names none. Without it, each is dispatched and
burns its full retry ladder, so a run with no usable keys dies several seconds
in with a wall of stacked provider exceptions instead of naming the variable to
set. An explicit selection is still honoured, so an operator can force a model
and see its real error.

**Do not serve provider exceptions verbatim.** The message is not built from a
traceback, but provider SDKs embed their own inside the exception text, so
absolute paths and dependency versions reach the caller.
`app/core/redaction.py` strips frames and paths while keeping the actionable
part — `"Missing Gemini API key"` survives, which is the difference between a
redaction and a blank.

---

## 9. Copying this into a different agent app

**Take these.** They are provider-agnostic and each was learned the hard way:

- retry classification (§6) — especially the `InternalServerError` hierarchy
- credential resolution by variable name (§3)
- `enabled` vs `callable` (§5)
- endpoint validation and connection pinning (§7)
- JSON recovery with one repair call (§6)
- token estimation when `usage` is absent (§6)

**Leave these.** They are this application's decisions, not general ones:

- the fan-out across several models, and `fan_out()` itself
- the 15-metric judge rubric (`app/agents/evaluator.py`)
- the consensus merge (`app/agents/consensus.py`)
- the LangGraph pipeline (`app/agents/graph.py`)
- the run/candidate database schema

**Change this.** `settings` is a module-level singleton, imported directly by
`llm_service`. It is what makes the layer hard to lift: taking the file means
taking pydantic-settings and this application's entire `.env` schema. Inject a
config object instead.

### A single-model executor

Most agent apps want one model, not a fan-out. The minimum:

```python
spec = registry.get(os.environ["AGENT_MODEL_ID"])

if requires_credential(spec):                 # §5 — before any work
    raise SystemExit(f"set {credential_env_var(spec)}")

result = await llm_service.complete(
    spec, system_prompt=..., user_prompt=..., phase="step-1",
)
budget -= result.cost_usd                      # §6 — check before the next call
```

Skip the registry entirely if one model is hardcoded; construct a
`ProviderConfig` directly. The registry earns its place when models are
operator-editable at runtime.

---

## 10. Environment reference

```bash
# Credentials — at least one, or use a local model
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
GEMINI_API_KEY=
GROQ_API_KEY=
OLLAMA_CLOUD_API_KEY=          # named per-entry via api_key_env

# Catalogue and roles
MODEL_CONFIG_PATH=backend/config/models.json
ANALYZER_MODEL_ID=openai-gpt4o
JUDGE_MODEL_ID=anthropic-claude-sonnet
CONSENSUS_MODEL_ID=anthropic-claude-sonnet

# Local inference
OLLAMA_BASE_URL=http://ollama:11434
API_BASE_ALLOWLIST=localhost,127.0.0.1,::1,ollama,vllm,host.docker.internal

# Tuning
GENERATION_TIMEOUT_SECONDS=180
JUDGE_TIMEOUT_SECONDS=120
MAX_PARALLEL_GENERATIONS=8
LLM_MAX_RETRIES=3
LLM_RETRY_BASE_DELAY=1.5
ANALYSIS_MAX_TOKENS=1536
JUDGE_MAX_TOKENS=1536
```

---

## Files

| Path | What it holds |
|---|---|
| `app/core/config.py` | Typed settings; deployment guards |
| `app/core/net.py` | Endpoint validation, DNS resolution |
| `app/core/pinned_transport.py` | Connection pinning |
| `app/core/redaction.py` | Trimming provider errors for clients |
| `app/models/schemas.py` | `ProviderConfig`, `ProviderPublic` |
| `app/services/model_registry.py` | Catalogue loading, selection |
| `app/services/llm_service.py` | Credentials, calls, retries, accounting |
| `backend/config/models.json` | The catalogue |
