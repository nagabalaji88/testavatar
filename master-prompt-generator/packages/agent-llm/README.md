# agent-llm

One model, called safely. Extracted from the Master Prompt Generator's LLM
layer and reduced to the single-model case an agent executor actually needs.

```python
from agent_llm import from_env

llm = from_env()
result = await llm.complete(system="You are precise.", user="Summarise this.")
print(result.content, result.cost_usd, result.attempts)
```

## What it is for

Calling a provider SDK directly is four lines. The next six months are spent
adding the things around it, and they are the same things every time:

| | Without | With |
|---|---|---|
| Transient 500 | unhandled exception kills the task | retried with backoff and jitter |
| Bad API key | retried 3× then fails anyway | fails immediately, marked fatal |
| Missing key | discovered mid-task as a provider outage | `is_ready()` before any work starts |
| Model wraps JSON in prose | `json.loads` raises | recovered, or one repair call |
| Reply hits `max_tokens` | silent truncation, parsed as if complete | `result.truncated` |
| Self-hosted model omits usage | cost reported as zero | estimated, budgets keep working |
| Configurable endpoint | SSRF into your metadata service | validated and connection-pinned |

None of it is hard. All of it is easy to forget, and each one is discovered in
production.

## Install

```bash
pip install -e packages/agent-llm
```

Depends only on `litellm` and `httpx`. No web framework, no ORM, no settings
library — deliberately, so it can drop into an application that has its own.

## Configure

Either explicitly:

```python
from agent_llm import LLMClient, ModelSpec, ClientOptions, RetryPolicy

llm = LLMClient(
    ModelSpec(
        key="anthropic/claude-sonnet-5",   # provider prefix is required
        api_key_env="ANTHROPIC_API_KEY",   # the variable name, not the value
        max_tokens=8192,
        cost_per_1k_input=0.003,
        cost_per_1k_output=0.015,
    ),
    ClientOptions(retry=RetryPolicy(max_attempts=5), max_parallel=4),
)
```

Or from the environment:

```bash
AGENT_LLM_MODEL=anthropic/claude-sonnet-5
AGENT_LLM_API_KEY_ENV=ANTHROPIC_API_KEY
ANTHROPIC_API_KEY=sk-ant-...
AGENT_LLM_MAX_TOKENS=8192          # optional
AGENT_LLM_API_BASE=https://...     # optional
AGENT_LLM_COST_IN=0.003            # optional
AGENT_LLM_COST_OUT=0.015           # optional
```

Any model LiteLLM can reach works — hosted or local:

```python
ModelSpec(key="openai/gpt-4o",                 api_key_env="OPENAI_API_KEY")
ModelSpec(key="ollama_chat/qwen2.5:7b-instruct", api_base="http://localhost:11434")
ModelSpec(key="openai/llama2", api_base="https://your-gateway/v1",
          api_key_env="GATEWAY_KEY")   # OpenAI-compatible gateway
```

### Two configuration mistakes worth naming

**The provider prefix is not optional.** `claude-3-5-sonnet-20240620` is
rejected by LiteLLM before the request leaves the process — it cannot infer the
provider — and the error reads like an outage rather than a typo. Use
`anthropic/claude-sonnet-5`.

**`api_key_env` holds a variable name, never a secret.** A `ModelSpec` is then
safe to log, serialise and commit; the value is read from the environment at
call time, so a rotated key is picked up without rebuilding anything.

## Endpoint safety

Any application that lets configuration choose where a model call goes has
handed out a server-side fetch. Two checks, because one is not enough:

```python
validate_endpoint("http://169.254.169.254/v1")   # UnsafeEndpointError
validate_endpoint("http://localtest.me/v1")      # UnsafeEndpointError - public
                                                 # name, resolves to 127.0.0.1
validate_endpoint("http://localhost:11434")      # fine - allowlisted
```

`validate_endpoint` runs when a value is accepted. `PinnedResolutionTransport`
(on by default) runs when the request goes out: it resolves the hostname,
validates every answer, and connects to the address it just checked rather than
to the name — while preserving the hostname for the `Host` header and TLS SNI,
so certificate verification still works.

That second check is not about a millisecond race. A configured endpoint
outlives its validation by days, so the practical attack needs no race at all:
supply a name that resolves somewhere harmless, wait for it to be accepted,
then repoint the DNS record.

`ClientOptions(endpoint_allowlist=...)` permits private hosts by name for local
inference. `pin_connections=False` disables pinning, which only makes sense when
the endpoint is a constant in your own code.

## Structured output

```python
payload, result = await llm.complete_json(
    system="Reply with JSON only.",
    user="Extract name and amount.",
)
```

Recovers JSON from fenced blocks, leading prose and trailing commentary. If the
first reply still does not parse, one corrective call is issued — and the
returned result carries the summed cost of both, so accounting stays honest.

## Budgets

Every result reports its own cost. Check before spending, not after:

```python
if spent >= budget:
    raise RuntimeError("budget exhausted")
result = await llm.complete(system=..., user=...)
spent += result.cost_usd
```

For metrics, `ClientOptions(on_result=...)` fires after every call — the hook
exists so this package need not depend on a metrics library.

## What is deliberately absent

Routing between models, scoring their output, merging it, prompt templates,
conversation memory, tool loops. Those are application decisions. This is the
transport layer under them.

The multi-model fan-out, judging and consensus machinery stays in the parent
application, which is where it belongs.

## Example

`examples/agent_loop.py` — a three-step plan/execute/review agent with a
budget, on one model:

```
model: anthropic/claude-sonnet-5
task : Explain a dead letter queue.

  plan           179 tok  $0.00219   1089ms  attempt 1
  execute        322 tok  $0.00262     88ms  attempt 1
  review         324 tok  $0.00263     88ms  attempt 1

total: $0.00744 across 3 steps
```

## Tests

```bash
cd packages/agent-llm && PYTHONPATH=. python -m pytest tests/ -q
```

32 tests, no network: providers are stubbed, and endpoint safety is verified
with a fake resolver.
