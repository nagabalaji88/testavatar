"""REST and websocket endpoints for API v1."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    Body,
    Depends,
    HTTPException,
    Query,
    Response,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.agents.debate import DebateError, debate_engine, debate_to_read
from app.core.config import settings
from app.core.events import EventType, event_bus
from app.core.logging import get_logger
from app.core.security import (
    Principal,
    Role,
    authenticate_websocket,
    create_access_token,
    create_refresh_token,
    decode_token,
    get_current_principal,
    hash_password,
    require_role,
    verify_password,
)
from app.core.telemetry import RUNS_STARTED
from app.db.session import get_session
from app.models.domain import ConsensusPrompt, ExecutionLog, PromptRun, RunStatus, User
from app.models.schemas import (
    METRIC_DEFINITIONS,
    ConsensusRead,
    DebateRead,
    DebateRequest,
    ExportRequest,
    HealthReport,
    ProviderConfig,
    ProviderToggle,
    RefreshRequest,
    RunAccepted,
    RunCreate,
    RunDetail,
    RunSummary,
    SemanticSearchHit,
    SemanticSearchRequest,
    TokenPair,
    UserCreate,
    UserRead,
)
from app.services.export_service import export_consensus
from app.services.model_registry import UnknownProviderError, model_registry
from app.services.vector_service import vector_service

logger = get_logger(__name__)

auth_router = APIRouter(prefix="/auth", tags=["auth"])
runs_router = APIRouter(prefix="/runs", tags=["runs"])
models_router = APIRouter(prefix="/models", tags=["models"])
debate_router = APIRouter(prefix="/debate", tags=["debate"])
system_router = APIRouter(tags=["system"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
CurrentUser = Annotated[Principal, Depends(get_current_principal)]
EngineerUser = Annotated[Principal, Depends(require_role(Role.ENGINEER))]
AdminUser = Annotated[Principal, Depends(require_role(Role.ADMIN))]


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


@auth_router.post(
    "/register", response_model=UserRead, status_code=status.HTTP_201_CREATED
)
async def register(payload: UserCreate, session: SessionDep) -> User:
    existing = (
        await session.execute(select(User).where(User.email == payload.email.lower()))
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Email already registered"
        )

    user = User(
        email=payload.email.lower(),
        hashed_password=hash_password(payload.password),
        full_name=payload.full_name,
        role=payload.role,
    )
    session.add(user)
    await session.flush()
    logger.info("user_registered", extra={"user_id": str(user.id)})
    return user


@auth_router.post("/login", response_model=TokenPair)
async def login(
    session: SessionDep,
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
) -> TokenPair:
    user = (
        await session.execute(
            select(User).where(User.email == form.username.strip().lower())
        )
    ).scalar_one_or_none()

    if user is None or not verify_password(form.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Account is disabled"
        )

    role = Role(user.role)
    return TokenPair(
        access_token=create_access_token(str(user.id), role),
        refresh_token=create_refresh_token(str(user.id), role),
        expires_in=settings.access_token_ttl_minutes * 60,
    )


@auth_router.post("/refresh", response_model=TokenPair)
async def refresh_tokens(payload: RefreshRequest, session: SessionDep) -> TokenPair:
    claims = decode_token(payload.refresh_token, expected_type="refresh")
    user = await session.get(User, uuid.UUID(claims.sub))
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Account is no longer valid"
        )
    role = Role(user.role)
    return TokenPair(
        access_token=create_access_token(str(user.id), role),
        refresh_token=create_refresh_token(str(user.id), role),
        expires_in=settings.access_token_ttl_minutes * 60,
    )


@auth_router.get("/me", response_model=UserRead)
async def read_me(principal: CurrentUser, session: SessionDep) -> User:
    user = await session.get(User, principal.user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )
    return user


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------


async def _load_run(session: AsyncSession, run_id: uuid.UUID) -> PromptRun:
    run = (
        await session.execute(
            select(PromptRun)
            .where(PromptRun.id == run_id)
            .options(
                selectinload(PromptRun.candidates), selectinload(PromptRun.consensus)
            )
        )
    ).scalar_one_or_none()
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Run not found"
        )
    return run


def _authorize_run(run: PromptRun, principal: Principal) -> None:
    if principal.has_at_least(Role.ADMIN):
        return
    if run.owner_id is not None and run.owner_id != principal.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Run belongs to another user"
        )


@runs_router.post("", response_model=RunAccepted, status_code=status.HTTP_202_ACCEPTED)
async def create_run(
    payload: RunCreate, session: SessionDep, principal: EngineerUser
) -> RunAccepted:
    """Persist a run and dispatch the pipeline to a Celery worker."""
    try:
        providers = model_registry.resolve(payload.model_ids)
    except UnknownProviderError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown or unavailable provider(s): {exc}",
        ) from exc

    run = PromptRun(
        owner_id=principal.user_id,
        title=payload.title,
        business_problem=payload.business_problem,
        target_domain=payload.target_domain,
        constraints=payload.constraints,
        requirements=payload.requirements,
        audience=payload.audience,
        output_format=payload.output_format,
        selected_model_ids=[provider.id for provider in providers],
        status=RunStatus.QUEUED.value,
    )
    session.add(run)
    await session.flush()
    run_id = str(run.id)

    RUNS_STARTED.inc()
    await event_bus.emit(
        run_id,
        EventType.RUN_QUEUED,
        title=run.title,
        models=[
            {"id": p.id, "name": p.name, "provider": p.provider} for p in providers
        ],
    )

    task_id: str | None = None
    try:
        from app.workers.celery_app import execute_pipeline_task

        task = execute_pipeline_task.delay(
            run_id, payload.model_dump(mode="json"), [p.id for p in providers]
        )
        task_id = task.id
    except Exception as exc:
        # Broker unreachable: run in-process so a single-container deployment
        # still works rather than silently queueing forever.
        logger.warning(
            "celery_dispatch_failed_running_inline",
            extra={"run_id": run_id, "error": str(exc)},
        )
        asyncio.create_task(_run_inline(run_id, payload, [p.id for p in providers]))

    logger.info("run_created", extra={"run_id": run_id, "task_id": task_id})
    return RunAccepted(
        run_id=run.id,
        status=run.status,
        task_id=task_id,
        websocket_url=f"{settings.api_v1_prefix}/runs/{run_id}/stream",
    )


async def _run_inline(run_id: str, payload: RunCreate, provider_ids: list[str]) -> None:
    from app.agents.graph import execute_pipeline

    try:
        await execute_pipeline(run_id, payload, provider_ids)
    except Exception:
        logger.exception("inline_pipeline_failed", extra={"run_id": run_id})


@runs_router.get("", response_model=list[RunSummary])
async def list_runs(
    session: SessionDep,
    principal: CurrentUser,
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    run_status: str | None = Query(default=None, alias="status"),
) -> list[PromptRun]:
    query = select(PromptRun).order_by(PromptRun.created_at.desc())
    if not principal.has_at_least(Role.ADMIN):
        query = query.where(PromptRun.owner_id == principal.user_id)
    if run_status:
        query = query.where(PromptRun.status == run_status)
    result = await session.execute(query.limit(limit).offset(offset))
    return list(result.scalars().all())


@runs_router.get("/stats", response_model=dict)
async def run_stats(session: SessionDep, principal: CurrentUser) -> dict[str, Any]:
    base = select(PromptRun)
    if not principal.has_at_least(Role.ADMIN):
        base = base.where(PromptRun.owner_id == principal.user_id)
    subquery = base.subquery()

    totals = (
        await session.execute(
            select(
                func.count(subquery.c.id),
                func.coalesce(func.sum(subquery.c.total_cost_usd), 0.0),
                func.coalesce(func.avg(subquery.c.duration_ms), 0.0),
            )
        )
    ).one()

    best = (
        await session.execute(
            select(func.coalesce(func.max(ConsensusPrompt.overall_score), 0.0))
            .select_from(ConsensusPrompt)
            .join(subquery, subquery.c.id == ConsensusPrompt.run_id)
        )
    ).scalar_one()

    by_status = (
        await session.execute(
            select(subquery.c.status, func.count(subquery.c.id)).group_by(
                subquery.c.status
            )
        )
    ).all()

    return {
        "total_runs": int(totals[0]),
        "total_cost_usd": round(float(totals[1]), 4),
        "avg_duration_ms": int(totals[2] or 0),
        "best_score": round(float(best), 2),
        "by_status": {row[0]: int(row[1]) for row in by_status},
    }


@runs_router.get("/{run_id}", response_model=RunDetail)
async def get_run(
    run_id: uuid.UUID, session: SessionDep, principal: CurrentUser
) -> PromptRun:
    run = await _load_run(session, run_id)
    _authorize_run(run, principal)
    return run


@runs_router.get("/{run_id}/consensus", response_model=ConsensusRead)
async def get_consensus(
    run_id: uuid.UUID, session: SessionDep, principal: CurrentUser
) -> ConsensusPrompt:
    run = await _load_run(session, run_id)
    _authorize_run(run, principal)
    if run.consensus is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Consensus is not available for this run yet",
        )
    return run.consensus


@runs_router.get("/{run_id}/logs", response_model=list[dict])
async def get_run_logs(
    run_id: uuid.UUID, session: SessionDep, principal: CurrentUser
) -> list[dict[str, Any]]:
    run = await _load_run(session, run_id)
    _authorize_run(run, principal)
    rows = (
        await session.execute(
            select(ExecutionLog)
            .where(ExecutionLog.run_id == run_id)
            .order_by(ExecutionLog.created_at.asc())
        )
    ).scalars()
    return [
        {
            "id": str(row.id),
            "stage": row.stage,
            "model_id": row.model_id,
            "status": row.status,
            "latency_ms": row.latency_ms,
            "input_tokens": row.input_tokens,
            "output_tokens": row.output_tokens,
            "cost_usd": row.cost_usd,
            "attempts": row.attempts,
            "detail": row.detail,
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]


@runs_router.get("/{run_id}/events", response_model=list[dict])
async def get_run_events(
    run_id: uuid.UUID, session: SessionDep, principal: CurrentUser
) -> list[dict[str, Any]]:
    run = await _load_run(session, run_id)
    _authorize_run(run, principal)
    return [event.model_dump(mode="json") for event in await event_bus.history(run_id)]


@runs_router.post("/{run_id}/export")
async def export_run(
    run_id: uuid.UUID,
    session: SessionDep,
    principal: CurrentUser,
    payload: ExportRequest | None = Body(default=None),
) -> Response:
    run = await _load_run(session, run_id)
    _authorize_run(run, principal)
    if run.consensus is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Run has no consensus prompt to export",
        )

    options = payload or ExportRequest()
    artifact = export_consensus(
        run, run.consensus, options.format, options.include_evaluation
    )
    return Response(
        content=artifact.content,
        media_type=artifact.media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{artifact.filename}"'
        },
    )


@runs_router.delete("/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_run(
    run_id: uuid.UUID, session: SessionDep, principal: EngineerUser
) -> Response:
    run = await _load_run(session, run_id)
    _authorize_run(run, principal)
    await session.delete(run)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@runs_router.post("/search", response_model=list[SemanticSearchHit])
async def semantic_search(
    payload: SemanticSearchRequest, principal: CurrentUser
) -> list[SemanticSearchHit]:
    return await vector_service.search(payload.query, payload.limit, payload.min_score)


@runs_router.websocket("/{run_id}/stream")
async def stream_run(websocket: WebSocket, run_id: uuid.UUID) -> None:
    """Stream pipeline events for a run.

    The token is supplied as a query parameter because browsers cannot set
    headers on a websocket handshake.
    """
    token = websocket.query_params.get("token")
    try:
        authenticate_websocket(token)
    except HTTPException:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()
    logger.info("websocket_connected", extra={"run_id": str(run_id)})

    try:
        async for event in event_bus.subscribe(run_id):
            await websocket.send_text(event.encode())
            if event.type in (EventType.RUN_COMPLETED, EventType.RUN_FAILED):
                break
    except WebSocketDisconnect:
        logger.info("websocket_disconnected", extra={"run_id": str(run_id)})
    except Exception as exc:
        logger.warning(
            "websocket_stream_error", extra={"run_id": str(run_id), "error": str(exc)}
        )
    finally:
        if websocket.client_state.name == "CONNECTED":
            await websocket.close()


# ---------------------------------------------------------------------------
# Debate
# ---------------------------------------------------------------------------


@debate_router.post("", response_model=DebateRead)
async def run_debate(payload: DebateRequest, principal: CurrentUser) -> DebateRead:
    """Debate one question across several models and return the judged answer.

    Synchronous on purpose: a debate is three sequential rounds, so the caller
    holds the connection for the duration. Runs are the surface for work that
    needs backgrounding, streaming and persistence.
    """
    try:
        providers = model_registry.resolve(payload.provider_ids)
    except UnknownProviderError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown provider: {exc}",
        ) from exc

    try:
        result = await debate_engine.debate(payload.question, providers)
    except DebateError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    return debate_to_read(result)


# ---------------------------------------------------------------------------
# Model registry
# ---------------------------------------------------------------------------


@models_router.get("", response_model=list[ProviderConfig])
async def list_models(principal: CurrentUser) -> list[ProviderConfig]:
    return model_registry.all()


@models_router.post("", response_model=ProviderConfig, status_code=status.HTTP_201_CREATED)
async def upsert_model(payload: ProviderConfig, principal: AdminUser) -> ProviderConfig:
    return model_registry.upsert(payload)


@models_router.patch("/{provider_id}", response_model=ProviderConfig)
async def toggle_model(
    provider_id: str, payload: ProviderToggle, principal: AdminUser
) -> ProviderConfig:
    try:
        return model_registry.set_enabled(provider_id, payload.enabled)
    except UnknownProviderError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown provider: {exc}"
        ) from exc


@models_router.delete("/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_model(provider_id: str, principal: AdminUser) -> Response:
    try:
        model_registry.delete(provider_id)
    except UnknownProviderError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown provider: {exc}"
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@models_router.post("/reload", response_model=list[ProviderConfig])
async def reload_models(principal: AdminUser) -> list[ProviderConfig]:
    return model_registry.reload().providers


# ---------------------------------------------------------------------------
# System
# ---------------------------------------------------------------------------


@system_router.get("/metrics-definitions", response_model=list[dict])
async def metric_definitions() -> list[dict[str, Any]]:
    return [metric.model_dump(mode="json") for metric in METRIC_DEFINITIONS]


@system_router.get("/health", response_model=HealthReport)
async def health(session: SessionDep) -> HealthReport:
    dependencies: dict[str, str] = {}

    try:
        await session.execute(select(1))
        dependencies["postgres"] = "ok"
    except Exception:
        dependencies["postgres"] = "unavailable"

    try:
        client = await event_bus.client()
        await client.ping()
        dependencies["redis"] = "ok"
    except Exception:
        dependencies["redis"] = "unavailable"

    dependencies["qdrant"] = await vector_service.health()
    dependencies["providers"] = str(len(model_registry.enabled()))

    degraded = any(value == "unavailable" for value in dependencies.values())
    return HealthReport(
        status="degraded" if degraded else "ok",
        version=settings.app_name,
        environment=settings.environment,
        dependencies=dependencies,
    )


@system_router.get("/time")
async def server_time() -> dict[str, str]:
    return {"now": datetime.now(timezone.utc).isoformat()}
