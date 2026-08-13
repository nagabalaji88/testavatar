# Master Prompt Generator (MPG)

Multi-LLM orchestration platform. It takes a business brief, fans prompt
generation out across every configured provider in parallel, scores each
candidate with a deterministic + LLM AI Judge, and synthesises an **Elite
Consensus Prompt** from the strongest sections of each model — with conflicts
resolved rather than concatenated.

```
[ Business brief ]
        │
        ▼
[ Requirement Analyzer ] ──── model-specific seed meta-prompts
        │
        ├──── async fan-out (LiteLLM · Celery) ────┐
        ▼                                          ▼
   [ GPT-4o ]        [ Claude 3.5 ]        [ Gemini 1.5 Pro ]
        └────────────────┬─────────────────────────┘
                         ▼
              [ Evaluation Engine ]        15 weighted criteria
              (AI Judge + rule engine)     deterministic ⊕ LLM
                         ▼
              [ Consensus Engine ]         extract → resolve → merge → optimize
                         ▼
              [ Elite Master Prompt ]
                         ▼
        [ React 19 glassmorphic dashboard ]   ← websocket stream
```

## Quick start

**Windows** — double-click `run-mpg.bat`, or from a console:

```bat
run-mpg.bat            :: auto-detect and start
run-mpg.bat local      :: force the free open-source stack (Ollama)
run-mpg.bat up         :: force the API-key stack
run-mpg.bat logs       :: tail the pipeline logs
run-mpg.bat down       :: stop, keeping the data
run-mpg.bat help       :: all commands
```

With no argument it picks the stack from what is actually configured:

| `.env` state | What starts |
| --- | --- |
| Points at `models.local.json` | Free open-source stack |
| Has an OpenAI / Anthropic / Gemini key | API-key stack |
| Neither | Asks, defaulting to free and open source |

It also checks Docker, generates a random `JWT_SECRET_KEY` on first run, waits
for the API to report healthy, then opens the dashboard.

**macOS / Linux**

```bash
cp .env.example .env          # add at least one provider API key
docker compose up --build
```

| Surface | URL |
| --- | --- |
| Dashboard | http://localhost:8080 |
| API docs | http://localhost:8000/docs |
| Prometheus metrics | http://localhost:8000/metrics |

Register the first account from the sign-in screen, then launch a run. New
accounts default to the `engineer` role; promote to `admin` in the `users`
table to manage the model registry.

## Architecture

### Backend — Python 3.9 · FastAPI · LangGraph

The backend targets **Python 3.9**, so the code uses `Optional[X]` / `Union[X, Y]`
rather than PEP 604 `X | Y` (Pydantic, SQLModel and FastAPI evaluate annotations
at runtime, where `|` is a 3.10 feature), `class Foo(str, Enum)` rather than
`StrEnum`, plain `@dataclass` rather than `slots=True`, and
`typing_extensions.TypedDict` for the LangGraph state schema. Verified with
`vermin -t=3.9- --eval-annotations`.

| Path | Responsibility |
| --- | --- |
| `app/main.py` | ASGI app, middleware, request context, metrics, lifespan |
| `app/agents/graph.py` | LangGraph state machine: analyze → generate → evaluate → consensus → finalize |
| `app/agents/analyzer.py` | Requirement analysis + per-provider seed meta-prompts |
| `app/agents/evaluator.py` | AI Judge: rule engine blended 35/65 with a judge model |
| `app/agents/consensus.py` | Section extraction, conflict resolution, merge, token optimizer |
| `app/services/llm_service.py` | LiteLLM wrapper: retries, concurrency limit, cost/token accounting |
| `app/services/model_registry.py` | Runtime-editable provider catalogue backed by `config/models.json` |
| `app/services/vector_service.py` | Qdrant semantic index over consensus prompts (best-effort) |
| `app/services/export_service.py` | Markdown / JSON / YAML / XML / Python / TypeScript exports |
| `app/workers/celery_app.py` | Celery task that drives the graph on a private event loop |
| `app/core/events.py` | Redis pub/sub event bus with replayable backlog |

Pipeline stages run inside Celery workers while websockets are served by API
processes, so progress events travel over a Redis channel keyed by run id. Every
connect replays the backlog first, so a dropped socket never leaves the
dashboard with a partial picture.

### Degradation

No single failure aborts a run:

- analyzer model unavailable → deterministic heuristic analysis;
- one provider fails → the run continues with the remaining candidates;
- judge model unavailable → the rule-engine score stands alone;
- consensus polish drops a section or over-compresses → the polish is rejected
  and the mechanically merged prompt ships;
- Qdrant or the embedding API unavailable → indexing is skipped, search degrades;
- Celery broker unreachable → the pipeline runs in-process.

### Consensus engine

Four deterministic phases, each pure and unit-tested:

1. **Extract** — parse candidates into a canonical section taxonomy (heading
   aliases are normalised, so "Persona" and "Role & Objective" are one section)
   and score each variant against the rubric metrics that section governs.
2. **Resolve** — detect syntactic (competing output formats), structural
   (divergent headings) and semantic conflicts. Semantic detection runs both
   within a section and *across* sections, because models file the same rule
   under different headings. Polarity and numeric-limit checks run before the
   duplicate threshold: "escalate below 0.7" and "escalate below 0.9" are 0.99
   similar and are a conflict, not a restatement. Losing directives are removed
   from the body, not merely annotated.
3. **Merge — directive level, not section level.** Taking one model's section
   wholesale discards a better-worded version of the same rule from another
   model, and throws away the strongest signal available: that several models
   independently arrived at the same instruction. Instead, equivalent
   directives are clustered across models; each cluster keeps the **best
   phrasing** and records its **support** (how many models produced it).
   Directives are scored on specificity, measurability, imperative phrasing and
   absence of hedging, then ranked by `0.45×quality + 0.30×authority +
   0.25×support`. Agreement rescues a merely-adequate directive; quality
   rescues a unique one; a directive with neither is cut. Bullet lists are
   re-ordered strongest-first (they are unordered by nature); prose keeps
   document order.
4. **Reinforce** — merging can only be as good as its inputs, so when *every*
   candidate omits a production concern the merged prompt inherits that blind
   spot. Six rules detect the omission and close it with a curated directive:
   injection defence, grounding/abstention, failure paths, PII handling,
   determinism and scope boundaries. Additions are capped at four and reported
   with a rationale. Two or more directives targeting a missing section create
   that section in canonical order rather than piling into a fallback.
5. **Optimize** — deduplicate lines, collapse whitespace runs, normalise heading
   levels and strip filler, preserving fenced code blocks verbatim.

Reinforcement is what lifts the consensus *above* the best single model rather
than merely matching it.

An LLM polish pass then smooths the prose. It is **rejected** if it drops a
heading or compresses below 55% of the merged length, so synthesis can never
regress.

### Evaluation rubric

Fifteen weighted criteria across three categories (weights sum to 1.0):

- **Clarity & structure** — instruction clarity, role definition, output
  formatting, constraints completeness, structural organization.
- **Cognitive quality** — reasoning depth, context awareness, hallucination
  prevention, adaptability, example quality.
- **Production readiness** — security guardrails, token efficiency, determinism,
  tool/function calling, maintainability.

The overall score is always recomputed from the weighted metrics, so a judge
model cannot report a headline number that disagrees with its own breakdown.

### Frontend — React 19 · TypeScript strict · Vite

Zustand holds live run state; TanStack Query owns server state. Visuals: React
Flow execution graph, Recharts radar + small-multiple bar charts, a model ×
criterion heatmap, and Monaco for the prompt and its diff against each
candidate.

Chart colours use the first three slots of a categorical palette validated for
colour-vision deficiency against the dashboard surface (worst all-pairs CVD
ΔE 9.4, normal-vision ΔE 20.9, all ≥ 3:1 contrast). Series are capped at three
rather than generating unvalidated hues; the consensus is drawn in ink with a
heavier dashed stroke so its identity never rests on colour alone. Cost and
latency get one axis each — never a dual-axis chart.

> `Vite.config.ts` is capitalised to match the specified file layout, so the npm
> scripts pass `--config Vite.config.ts` explicitly (Vite only auto-detects the
> lowercase name).

## Running on free, open-source models

The whole pipeline can run on open-weight models served from your own hardware,
with **no API keys and no per-token cost**:

```bash
cp .env.local.example .env
docker compose --profile local up --build
docker compose --profile local run --rm ollama-pull   # first run only
```

or on Windows, `run-mpg.bat local` — which does all three steps, including
copying the env file and generating a secret.

This starts an [Ollama](https://ollama.com) service and points the backend at
`backend/config/models.local.json`, which fans out across **Qwen2.5 7B**,
**Llama 3.1 8B** and **Mistral 7B**, with Gemma 2 and DeepSeek-R1 available but
disabled. Embeddings switch to `nomic-embed-text`, so semantic search stays
local too. Every provider reports `cost_per_1k = 0`, so the dashboard's spend
tiles read `$0.00` rather than showing invented numbers.

### What this costs you instead of money

- **Disk:** roughly 12 GB of weights on first pull.
- **Time:** a 7B model on CPU takes minutes per prompt. A three-model consensus
  run can take tens of minutes. The local env therefore sets
  `MAX_PARALLEL_GENERATIONS=1` — Ollama answers one request at a time by
  default, so firing three at it concurrently just makes all three time out
  together. With a GPU, raise `OLLAMA_NUM_PARALLEL` and that limit together.
- **Quality:** 7B open models produce weaker prompts than frontier models. The
  consensus engine partly compensates — its reinforcement phase adds the
  production directives small models routinely omit — but do not expect parity.

On a CPU-only machine, switch to 3B weights (`qwen2.5:3b-instruct`,
`llama3.2:3b-instruct-q4_K_M`) and edit `models.local.json` to match; a run then
finishes in minutes.

### Hosted gateways for open models

`models.local.json` also carries a disabled **Llama 3.3 70B via Groq** entry.
Groq, OpenRouter and Together all serve open-weight models on free tiers and are
far faster than local CPU inference. Set `GROQ_API_KEY` (or the OpenRouter /
Together equivalent) and flip `enabled` to `true`. The models stay open source;
only the hosting is someone else's.

### Mixing open and hosted models

Nothing forces an all-or-nothing choice. Copy entries between the two registry
files — the ids are deliberately disjoint — and set `ANALYZER_MODEL_ID`,
`JUDGE_MODEL_ID` and `CONSENSUS_MODEL_ID` to whichever ids you want driving each
stage. A common split is local models for the fan-out and a stronger hosted
model as the judge.

## Configuring models

`backend/config/models.json` is the source of truth and is mounted into both the
API and worker containers, so providers can be added without rebuilding:

```json
{
  "id": "openai-gpt4o",
  "name": "GPT-4o",
  "provider": "OpenAI",
  "model_key": "gpt-4o",
  "max_tokens": 4096,
  "cost_per_1k_input": 0.0025,
  "cost_per_1k_output": 0.01,
  "enabled": true
}
```

Admins can also toggle, upsert and reload providers through `/api/v1/models`.

## Local development

```bash
# backend
cd backend
pip install -r Requirements.txt
uvicorn app.main:app --reload
celery -A app.workers.celery_app.celery_app worker --loglevel=info

# frontend
cd frontend
npm install
npm run dev
```

## Tests

```bash
cd backend && python -m pytest tests      # 38 tests, consensus + judge logic
cd frontend && npm run typecheck && npm run build
```

The suite covers the deterministic engine paths — section parsing, similarity,
all three conflict kinds, directive scoring, cluster support and best-phrasing
selection, the solo-directive floor, merge provenance, the
single-output-contract invariant, reinforcement placement and capping, the
optimizer, and the rule-engine scorer. Network-dependent agent
paths are exercised through their fallbacks.

## API surface

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/v1/auth/register` · `/login` · `/refresh` · `GET /me` | JWT auth (Argon2, access + refresh) |
| `POST` | `/api/v1/runs` | Queue a consensus run |
| `GET` | `/api/v1/runs` · `/{id}` · `/stats` | Run history, detail, aggregates |
| `GET` | `/api/v1/runs/{id}/consensus` · `/logs` · `/events` | Artefact and telemetry |
| `POST` | `/api/v1/runs/{id}/export` | Export in six formats |
| `POST` | `/api/v1/runs/search` | Semantic search over past prompts |
| `WS` | `/api/v1/runs/{id}/stream?token=…` | Live pipeline events |
| `GET/POST/PATCH/DELETE` | `/api/v1/models` | Provider registry (admin) |

## Security

- Argon2 password hashing; JWT access + refresh tokens with typed claims.
- RBAC (`viewer` < `engineer` < `admin`) enforced by dependency guards; runs are
  scoped to their owner unless the caller is an admin.
- Websockets authenticate via a query-parameter token (browsers cannot set
  handshake headers) and close with 1008 on failure.
- Set `JWT_SECRET_KEY` before any non-local deployment.
