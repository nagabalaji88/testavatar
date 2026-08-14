"""LangGraph execution pipeline.

Nodes: analyze → generate → evaluate → consensus → finalize.

Each node is idempotent with respect to the database: it writes its own slice
of the run record and emits websocket events through the Redis event bus, so a
retried node cannot corrupt earlier state. Any node may fail; failures route to
the terminal `fail` node which records the error and closes the run.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timezone
from typing import Annotated, Any, Optional

# On Python < 3.12 the typing_extensions backport is required: it is the only
# variant that reports __required_keys__ correctly, which LangGraph and Pydantic
# both rely on when they resolve a TypedDict state schema.
from typing_extensions import TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy import select, update

from app.agents.analyzer import RequirementAnalyzer, requirement_analyzer
from app.agents.consensus import CandidateInput, ConsensusEngine, consensus_engine
from app.agents.evaluator import PromptEvaluator, prompt_evaluator
from app.core.events import EventType, event_bus
from app.core.logging import get_logger, run_id_ctx
from app.core.telemetry import (
    RUNS_COMPLETED,
    RUNS_IN_FLIGHT,
    STAGE_LATENCY,
    span,
)
from app.db.session import session_scope
from app.models.domain import (
    CandidateStatus,
    ConsensusPrompt,
    ExecutionLog,
    PromptCandidate,
    PromptRun,
    RunStatus,
)
from app.models.schemas import (
    JudgeVerdict,
    ProviderConfig,
    RequirementAnalysis,
    RunCreate,
)
from app.services.llm_service import LLMFailure, LLMResult, llm_service
from app.services.model_registry import UnknownProviderError, model_registry
from app.services.vector_service import vector_service

logger = get_logger(__name__)


def _merge_dict(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    return {**left, **right}


class PipelineState(TypedDict, total=False):
    """State threaded through the graph."""

    run_id: str
    request: dict[str, Any]
    provider_ids: list[str]
    analysis: dict[str, Any]
    seed_prompts: dict[str, str]
    candidates: list[dict[str, Any]]
    verdicts: list[dict[str, Any]]
    consensus: dict[str, Any]
    usage: Annotated[dict[str, Any], _merge_dict]
    error: str
    stage: str


class PipelineDependencies:
    """Injectable collaborators — swapped wholesale in tests."""

    def __init__(
        self,
        analyzer: Optional[RequirementAnalyzer] = None,
        evaluator: Optional[PromptEvaluator] = None,
        consensus: Optional[ConsensusEngine] = None,
    ) -> None:
        self.analyzer = analyzer or requirement_analyzer
        self.evaluator = evaluator or prompt_evaluator
        self.consensus = consensus or consensus_engine


# --- persistence helpers ---------------------------------------------------


async def _set_status(run_id: str, status: RunStatus, **fields: Any) -> None:
    async with session_scope() as session:
        run = await session.get(PromptRun, uuid.UUID(run_id))
        if run is None:
            return
        run.status = status.value
        for key, value in fields.items():
            setattr(run, key, value)
        session.add(run)


async def _log_execution(
    run_id: str,
    stage: str,
    status: str,
    *,
    model_id: Optional[str] = None,
    result: Optional[LLMResult] = None,
    detail: Optional[dict[str, Any]] = None,
    latency_ms: int = 0,
) -> None:
    async with session_scope() as session:
        session.add(
            ExecutionLog(
                run_id=uuid.UUID(run_id),
                stage=stage,
                model_id=model_id,
                status=status,
                latency_ms=result.latency_ms if result else latency_ms,
                input_tokens=result.input_tokens if result else 0,
                output_tokens=result.output_tokens if result else 0,
                cost_usd=result.cost_usd if result else 0.0,
                attempts=result.attempts if result else 1,
                detail=detail,
            )
        )


async def _accumulate_usage(run_id: str, result: Optional[LLMResult]) -> None:
    """Add one call's usage to the run totals atomically.

    Read-modify-write in Python loses updates here: generation fans out and
    evaluation now judges concurrently, so several callers hold the same row
    at once and the last commit wins. Incrementing in SQL makes each addition
    a single atomic statement, so no usage or cost is silently dropped.
    """
    if result is None:
        return
    async with session_scope() as session:
        await session.execute(
            update(PromptRun)
            .where(PromptRun.id == uuid.UUID(run_id))
            .values(
                total_input_tokens=PromptRun.total_input_tokens + result.input_tokens,
                total_output_tokens=PromptRun.total_output_tokens
                + result.output_tokens,
                total_cost_usd=PromptRun.total_cost_usd + result.cost_usd,
            )
        )


def _resolve_providers(provider_ids: list[str]) -> list[ProviderConfig]:
    try:
        return model_registry.resolve(provider_ids)
    except UnknownProviderError as exc:
        raise ValueError(f"Unknown provider(s): {exc}") from exc


# --- nodes -----------------------------------------------------------------


def build_nodes(deps: PipelineDependencies):
    async def analyze_node(state: PipelineState) -> PipelineState:
        run_id = state["run_id"]
        run_id_ctx.set(run_id)
        started = time.perf_counter()

        with span("pipeline.analyze", **{"mpg.run_id": run_id}):
            await event_bus.emit(run_id, EventType.STAGE_STARTED, stage="analysis")
            await _set_status(
                run_id,
                RunStatus.ANALYZING,
                started_at=datetime.now(timezone.utc),
            )

            request = RunCreate.model_validate(state["request"])
            providers = _resolve_providers(state.get("provider_ids") or [])
            analysis, result = await deps.analyzer.analyze(request)
            seeds = deps.analyzer.seed_prompts(request, analysis, providers)

            await _accumulate_usage(run_id, result)
            await _log_execution(
                run_id,
                "analysis",
                "succeeded",
                model_id=result.model_id if result else None,
                result=result,
                detail={"heuristic_fallback": result is None},
            )

            async with session_scope() as session:
                run = await session.get(PromptRun, uuid.UUID(run_id))
                if run is not None:
                    run.analysis = analysis.model_dump(mode="json")
                    run.selected_model_ids = [p.id for p in providers]
                    session.add(run)

                for provider in providers:
                    session.add(
                        PromptCandidate(
                            run_id=uuid.UUID(run_id),
                            model_id=provider.id,
                            model_name=provider.name,
                            provider=provider.provider,
                            seed_prompt=seeds[provider.id],
                            status=CandidateStatus.PENDING.value,
                        )
                    )

            elapsed = time.perf_counter() - started
            STAGE_LATENCY.labels("analysis").observe(elapsed)
            await event_bus.emit(
                run_id,
                EventType.STAGE_COMPLETED,
                stage="analysis",
                analysis=analysis.model_dump(mode="json"),
                providers=[
                    {"id": p.id, "name": p.name, "provider": p.provider}
                    for p in providers
                ],
                duration_ms=int(elapsed * 1000),
            )

        return {
            "analysis": analysis.model_dump(mode="json"),
            "seed_prompts": seeds,
            "provider_ids": [provider.id for provider in providers],
            "stage": "analysis",
        }

    async def generate_node(state: PipelineState) -> PipelineState:
        run_id = state["run_id"]
        run_id_ctx.set(run_id)
        started = time.perf_counter()
        analysis = RequirementAnalysis.model_validate(state["analysis"])
        providers = _resolve_providers(state["provider_ids"])
        seeds = state["seed_prompts"]
        system_prompt = deps.analyzer.generator_system_prompt(analysis)

        with span("pipeline.generate", **{"mpg.run_id": run_id}):
            await event_bus.emit(
                run_id,
                EventType.STAGE_STARTED,
                stage="generation",
                models=[provider.id for provider in providers],
            )
            await _set_status(run_id, RunStatus.GENERATING)

            async def on_start(provider: ProviderConfig) -> None:
                await _update_candidate(
                    run_id, provider.id, status=CandidateStatus.RUNNING
                )
                await event_bus.emit(
                    run_id,
                    EventType.CANDIDATE_STARTED,
                    model_id=provider.id,
                    model_name=provider.name,
                )

            async def on_success(result: LLMResult) -> None:
                await _update_candidate(
                    run_id,
                    result.model_id,
                    status=CandidateStatus.SUCCEEDED,
                    content=result.content,
                    input_tokens=result.input_tokens,
                    output_tokens=result.output_tokens,
                    cost_usd=result.cost_usd,
                    latency_ms=result.latency_ms,
                    attempts=result.attempts,
                )
                await _accumulate_usage(run_id, result)
                await _log_execution(
                    run_id,
                    "generation",
                    "succeeded",
                    model_id=result.model_id,
                    result=result,
                )
                await event_bus.emit(
                    run_id,
                    EventType.CANDIDATE_COMPLETED,
                    model_id=result.model_id,
                    model_name=result.model_name,
                    latency_ms=result.latency_ms,
                    cost_usd=result.cost_usd,
                    input_tokens=result.input_tokens,
                    output_tokens=result.output_tokens,
                    content=result.content,
                )

            async def on_failure(failure: LLMFailure) -> None:
                await _update_candidate(
                    run_id,
                    failure.model_id,
                    status=CandidateStatus.FAILED,
                    error=failure.error,
                    latency_ms=failure.latency_ms,
                    attempts=failure.attempts,
                )
                await _log_execution(
                    run_id,
                    "generation",
                    "failed",
                    model_id=failure.model_id,
                    detail={"error": failure.error},
                    latency_ms=failure.latency_ms,
                )
                await event_bus.emit(
                    run_id,
                    EventType.CANDIDATE_FAILED,
                    model_id=failure.model_id,
                    model_name=failure.model_name,
                    error=failure.error,
                )

            results, failures = await llm_service.fan_out(
                providers,
                build_messages=lambda provider: (system_prompt, seeds[provider.id]),
                phase="generation",
                on_start=on_start,
                on_success=on_success,
                on_failure=on_failure,
            )

        if not results:
            raise RuntimeError(
                "Every model failed during generation: "
                + "; ".join(f"{f.model_id}: {f.error}" for f in failures)
            )

        elapsed = time.perf_counter() - started
        STAGE_LATENCY.labels("generation").observe(elapsed)
        await event_bus.emit(
            run_id,
            EventType.STAGE_COMPLETED,
            stage="generation",
            succeeded=len(results),
            failed=len(failures),
            duration_ms=int(elapsed * 1000),
        )

        return {
            "candidates": [
                {
                    "model_id": result.model_id,
                    "model_name": result.model_name,
                    "provider": result.provider,
                    "content": result.content,
                    "input_tokens": result.input_tokens,
                    "output_tokens": result.output_tokens,
                    "cost_usd": result.cost_usd,
                    "latency_ms": result.latency_ms,
                }
                for result in results
            ],
            "usage": {"generation_failures": len(failures)},
            "stage": "generation",
        }

    async def evaluate_node(state: PipelineState) -> PipelineState:
        run_id = state["run_id"]
        run_id_ctx.set(run_id)
        started = time.perf_counter()
        request = RunCreate.model_validate(state["request"])
        analysis = RequirementAnalysis.model_validate(state["analysis"])
        candidates = state["candidates"]

        with span("pipeline.evaluate", **{"mpg.run_id": run_id}):
            await event_bus.emit(run_id, EventType.STAGE_STARTED, stage="evaluation")
            await _set_status(run_id, RunStatus.EVALUATING)

            # Judged concurrently, not in a loop. Each candidate's verdict is
            # independent of every other, so a serial loop made this stage cost
            # the sum of N judge calls when it only ever needed the slowest one
            # -- on a local-model deployment that was the single largest block
            # of wall-clock in the entire run. llm_service's semaphore still
            # bounds how many actually reach the provider at once.
            async def judge(candidate: dict[str, Any]) -> dict[str, Any]:
                verdict, result = await deps.evaluator.evaluate(
                    prompt_id=candidate["model_id"],
                    content=candidate["content"],
                    business_problem=request.business_problem,
                    analysis=analysis,
                )
                payload = verdict.model_dump(mode="json")

                await _update_candidate(
                    run_id,
                    candidate["model_id"],
                    overall_score=verdict.overall_score,
                    metrics=verdict.metrics,
                    evaluation=payload,
                )
                await _accumulate_usage(run_id, result)
                await _log_execution(
                    run_id,
                    "evaluation",
                    "succeeded",
                    model_id=candidate["model_id"],
                    result=result,
                    detail={"overall_score": verdict.overall_score},
                )
                await event_bus.emit(
                    run_id,
                    EventType.EVALUATION_COMPLETED,
                    model_id=candidate["model_id"],
                    model_name=candidate["model_name"],
                    overall_score=verdict.overall_score,
                    metrics=verdict.metrics,
                    evaluation=payload,
                )
                return payload

            # gather preserves input order, so verdicts stay aligned with
            # candidates regardless of which judge finishes first.
            verdicts: list[dict[str, Any]] = list(
                await asyncio.gather(*(judge(c) for c in candidates))
            )

        elapsed = time.perf_counter() - started
        STAGE_LATENCY.labels("evaluation").observe(elapsed)
        await event_bus.emit(
            run_id,
            EventType.STAGE_COMPLETED,
            stage="evaluation",
            evaluated=len(verdicts),
            duration_ms=int(elapsed * 1000),
        )
        return {"verdicts": verdicts, "stage": "evaluation"}

    async def consensus_node(state: PipelineState) -> PipelineState:
        run_id = state["run_id"]
        run_id_ctx.set(run_id)
        started = time.perf_counter()
        request = RunCreate.model_validate(state["request"])
        analysis = RequirementAnalysis.model_validate(state["analysis"])
        verdict_by_model = {
            verdict["prompt_id"]: JudgeVerdict.model_validate(verdict)
            for verdict in state["verdicts"]
        }

        inputs: list[CandidateInput] = []
        for candidate in state["candidates"]:
            verdict = verdict_by_model.get(candidate["model_id"])
            if verdict is None:
                continue
            try:
                weight = model_registry.get(candidate["model_id"]).weight
            except UnknownProviderError:
                weight = 1.0
            inputs.append(
                CandidateInput(
                    model_id=candidate["model_id"],
                    model_name=candidate["model_name"],
                    content=candidate["content"],
                    verdict=verdict,
                    weight=weight,
                )
            )

        if not inputs:
            raise RuntimeError("No evaluated candidates available for synthesis")

        with span("pipeline.consensus", **{"mpg.run_id": run_id}):
            await event_bus.emit(run_id, EventType.STAGE_STARTED, stage="consensus")
            await _set_status(run_id, RunStatus.SYNTHESIZING)

            result = await deps.consensus.synthesize(inputs, analysis)
            await _accumulate_usage(run_id, result.polish_result)

            verdict, judge_result = await deps.evaluator.evaluate(
                prompt_id="consensus",
                content=result.content,
                business_problem=request.business_problem,
                analysis=analysis,
            )
            await _accumulate_usage(run_id, judge_result)

            best_candidate = max(
                inputs, key=lambda candidate: candidate.verdict.overall_score
            )
            improvement = round(
                verdict.overall_score - best_candidate.verdict.overall_score, 2
            )

            point_id = await vector_service.index_prompt(
                run_id=run_id,
                title=request.title,
                target_domain=request.target_domain,
                content=result.content,
                score=verdict.overall_score,
            )

            async with session_scope() as session:
                existing = (
                    await session.execute(
                        select(ConsensusPrompt).where(
                            ConsensusPrompt.run_id == uuid.UUID(run_id)
                        )
                    )
                ).scalar_one_or_none()
                record = existing or ConsensusPrompt(run_id=uuid.UUID(run_id), content="")
                record.content = result.content
                record.raw_merged_content = result.raw_merged_content
                record.overall_score = verdict.overall_score
                record.metrics = verdict.metrics
                record.evaluation = verdict.model_dump(mode="json")
                record.section_provenance = [
                    item.model_dump(mode="json") for item in result.provenance
                ]
                record.conflicts = [
                    item.model_dump(mode="json") for item in result.conflicts
                ]
                record.reinforcements = [
                    item.model_dump(mode="json") for item in result.reinforcements
                ]
                record.optimization_report = result.optimization.model_dump(mode="json")
                record.token_count = result.token_count
                record.tokens_saved = result.optimization.tokens_saved
                record.improvement_over_best = improvement
                record.vector_point_id = point_id
                session.add(record)

            await _log_execution(
                run_id,
                "consensus",
                "succeeded",
                model_id=result.polished_by,
                result=result.polish_result,
                detail={
                    "conflicts": len(result.conflicts),
                    "sections": len(result.provenance),
                    "reinforcements": len(result.reinforcements),
                    "improvement_over_best": improvement,
                },
            )

            payload = {
                "content": result.content,
                "overall_score": verdict.overall_score,
                "metrics": verdict.metrics,
                "evaluation": verdict.model_dump(mode="json"),
                "section_provenance": [
                    item.model_dump(mode="json") for item in result.provenance
                ],
                "conflicts": [item.model_dump(mode="json") for item in result.conflicts],
                "reinforcements": [
                    item.model_dump(mode="json") for item in result.reinforcements
                ],
                "optimization_report": result.optimization.model_dump(mode="json"),
                "token_count": result.token_count,
                "tokens_saved": result.optimization.tokens_saved,
                "improvement_over_best": improvement,
            }
            await event_bus.emit(run_id, EventType.CONSENSUS_COMPLETED, **payload)

        elapsed = time.perf_counter() - started
        STAGE_LATENCY.labels("consensus").observe(elapsed)
        return {"consensus": payload, "stage": "consensus"}

    async def finalize_node(state: PipelineState) -> PipelineState:
        run_id = state["run_id"]
        completed_at = datetime.now(timezone.utc)

        async with session_scope() as session:
            run = await session.get(PromptRun, uuid.UUID(run_id))
            if run is not None:
                run.status = RunStatus.COMPLETED.value
                run.completed_at = completed_at
                if run.started_at is not None:
                    started_at = run.started_at
                    if started_at.tzinfo is None:
                        started_at = started_at.replace(tzinfo=timezone.utc)
                    run.duration_ms = int(
                        (completed_at - started_at).total_seconds() * 1000
                    )
                session.add(run)
                total_cost = run.total_cost_usd
                duration_ms = run.duration_ms
            else:
                total_cost, duration_ms = 0.0, None

        RUNS_COMPLETED.labels("completed").inc()
        RUNS_IN_FLIGHT.dec()
        await event_bus.emit(
            run_id,
            EventType.RUN_COMPLETED,
            total_cost_usd=total_cost,
            duration_ms=duration_ms,
            overall_score=state.get("consensus", {}).get("overall_score"),
        )
        logger.info(
            "run_completed",
            extra={"run_id": run_id, "cost_usd": total_cost, "duration_ms": duration_ms},
        )
        return {"stage": "completed"}

    return analyze_node, generate_node, evaluate_node, consensus_node, finalize_node


def build_graph(deps: Optional[PipelineDependencies] = None):
    """Compile the LangGraph state machine."""
    analyze, generate, evaluate, consensus, finalize = build_nodes(
        deps or PipelineDependencies()
    )

    graph = StateGraph(PipelineState)
    graph.add_node("analyze", analyze)
    graph.add_node("generate", generate)
    graph.add_node("evaluate", evaluate)
    # Named "synthesize", not "consensus": LangGraph forbids a node name that
    # collides with a PipelineState key, and "consensus" is already the state
    # field the node writes into.
    graph.add_node("synthesize", consensus)
    graph.add_node("finalize", finalize)

    graph.add_edge(START, "analyze")
    graph.add_edge("analyze", "generate")
    graph.add_edge("generate", "evaluate")
    graph.add_edge("evaluate", "synthesize")
    graph.add_edge("synthesize", "finalize")
    graph.add_edge("finalize", END)

    return graph.compile()


_compiled_graph = None


def get_graph():
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_graph()
    return _compiled_graph


async def _update_candidate(run_id: str, model_id: str, **fields: Any) -> None:
    async with session_scope() as session:
        candidate = (
            await session.execute(
                select(PromptCandidate).where(
                    PromptCandidate.run_id == uuid.UUID(run_id),
                    PromptCandidate.model_id == model_id,
                )
            )
        ).scalar_one_or_none()
        if candidate is None:
            return
        for key, value in fields.items():
            setattr(candidate, key, value.value if isinstance(value, CandidateStatus) else value)
        session.add(candidate)


async def execute_pipeline(run_id: str, request: RunCreate, provider_ids: list[str]) -> None:
    """Run the full pipeline for a persisted run, recording terminal failures."""
    run_id_ctx.set(run_id)
    RUNS_IN_FLIGHT.inc()
    await event_bus.emit(run_id, EventType.RUN_STARTED, title=request.title)

    initial: PipelineState = {
        "run_id": run_id,
        "request": request.model_dump(mode="json"),
        "provider_ids": provider_ids,
        "usage": {},
    }

    try:
        with span("pipeline.run", **{"mpg.run_id": run_id}):
            await get_graph().ainvoke(initial)
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        logger.exception("run_failed", extra={"run_id": run_id})
        completed_at = datetime.now(timezone.utc)
        await _set_status(
            run_id, RunStatus.FAILED, error=message, completed_at=completed_at
        )
        await _log_execution(run_id, "pipeline", "failed", detail={"error": message})
        RUNS_COMPLETED.labels("failed").inc()
        RUNS_IN_FLIGHT.dec()
        await event_bus.emit(run_id, EventType.RUN_FAILED, error=message)
        raise
