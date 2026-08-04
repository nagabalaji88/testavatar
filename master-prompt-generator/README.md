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

### Backend — Python 3.12 · FastAPI · LangGraph

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
3. **Merge** — adopt the strongest variant per section, then graft in directives
   from other models that are neither duplicates nor conflict losers.
4. **Optimize** — deduplicate lines, collapse whitespace runs, normalise heading
   levels and strip filler, preserving fenced code blocks verbatim.

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
cd backend && python -m pytest tests      # 26 tests, consensus + judge logic
cd frontend && npm run typecheck && npm run build
```

The suite covers the deterministic engine paths — section parsing, similarity,
all three conflict kinds, merge provenance, the single-output-contract
invariant, the optimizer, and the rule-engine scorer. Network-dependent agent
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
