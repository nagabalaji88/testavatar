"""REST and websocket endpoints for API v1."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import datetime, timezone
from collections.abc import Sequence
from typing import Annotated, Any, Optional

from fastapi import (
    APIRouter,
    Body,
    Depends,
    HTTPException,
    Query,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.events import EventType, event_bus
from app.core.logging import get_logger
from app.core.net import UnsafeEndpointError, validate_api_base_async
from app.core.provider_families import FAMILIES_BY_NAME, PROVIDER_FAMILIES
from app.core.security import (
    Principal,
    oauth2_scheme,
    Role,
    create_access_token,
    create_refresh_token,
    create_stream_ticket,
    decode_token,
    get_current_principal,
    hash_password,
    redeem_stream_ticket,
    require_role,
    verify_password,
)
from app.core.ratelimit import client_identity, rate_limiter
from app.core.revocation import revocation_store
from app.core.telemetry import RUNS_STARTED
from app.db.session import get_session
from app.models.domain import (
    ConsensusPrompt,
    ExecutionLog,
    PromptRun,
    ProviderCredential,
    RunStatus,
    User,
)
from app.models.schemas import (
    METRIC_DEFINITIONS,
    ConsensusRead,
    CredentialStatus,
    CredentialTestResult,
    CredentialWrite,
    DiscoveredModelPublic,
    ExportRequest,
    FamilyDiscoveryPublic,
    HealthReport,
    ProviderConfig,
    ProviderPublic,
    ProviderToggle,
    RefreshRequest,
    RegistryImportRequest,
    RegistryImportResult,
    RoleUpdate,
    RunAccepted,
    RunCreate,
    RunDetail,
    RunSummary,
    SemanticSearchHit,
    SemanticSearchRequest,
    StreamTicket,
    TokenPair,
    UserCreate,
    UserRead,
)
from app.services.credential_store import credential_store
from app.services.export_service import export_consensus
from app.services.llm_service import (
    credential_env_var,
    credential_family,
    credential_source,
    is_local_runtime,
    requires_credential,
)
from app.services.model_discovery import model_discovery
from app.services.model_registry import UnknownProviderError, model_registry
from app.services.vector_service import vector_service

logger = get_logger(__name__)

auth_router = APIRouter(prefix="/auth", tags=["auth"])
runs_router = APIRouter(prefix="/runs", tags=["runs"])
models_router = APIRouter(prefix="/models", tags=["models"])
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
async def register(
    payload: UserCreate, request: Request, session: SessionDep
) -> User:
    """Create an account.

    The role is assigned by the server, never taken from the request. The first
    account on a fresh instance becomes the admin so the deployment is usable;
    everyone after that is an engineer and must be promoted by an admin.
    """
    if not settings.allow_open_registration:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Registration is closed. Ask an administrator for an account.",
        )

    await rate_limiter.check(
        "register",
        client_identity(request),
        settings.rate_limit_register_per_hour,
        3600,
    )

    existing = (
        await session.execute(select(User).where(User.email == payload.email.lower()))
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Email already registered"
        )

    user_count = (
        await session.execute(select(func.count()).select_from(User))
    ).scalar_one()
    role = Role.ADMIN if user_count == 0 else Role.ENGINEER

    user = User(
        email=payload.email.lower(),
        hashed_password=hash_password(payload.password),
        full_name=payload.full_name,
        role=role.value,
    )
    session.add(user)
    await session.flush()
    logger.info(
        "user_registered",
        extra={"user_id": str(user.id), "role": role.value, "bootstrap": user_count == 0},
    )
    return user


@auth_router.post("/login", response_model=TokenPair)
async def login(
    request: Request,
    session: SessionDep,
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
) -> TokenPair:
    # Throttle by caller and by account, so neither a spray across accounts nor
    # a focused attack on one account gets unlimited attempts.
    await rate_limiter.check(
        "login", client_identity(request), settings.rate_limit_login_per_minute, 60
    )
    await rate_limiter.check(
        "login-account",
        form.username.strip().lower(),
        settings.rate_limit_login_per_minute,
        60,
    )

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


@auth_router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    principal: CurrentUser,
    token: Annotated[Optional[str], Depends(oauth2_scheme)],
    payload: Optional[RefreshRequest] = Body(default=None),
) -> Response:
    """Revoke the presented tokens.

    A JWT is valid until it expires, so signing out has to be recorded
    server-side; without this, a leaked token stays usable for its full hour
    (or fourteen days, for a refresh token).
    """
    if token:
        claims = decode_token(token)
        await revocation_store.revoke_token(claims.jti, claims.exp)
    if payload and payload.refresh_token:
        try:
            refresh_claims = decode_token(payload.refresh_token, expected_type="refresh")
            await revocation_store.revoke_token(refresh_claims.jti, refresh_claims.exp)
        except HTTPException:
            pass  # already invalid; nothing left to revoke

    logger.info("user_logged_out", extra={"user_id": str(principal.user_id)})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@auth_router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
async def logout_everywhere(principal: CurrentUser) -> Response:
    """Invalidate every token already issued to the caller."""
    await revocation_store.revoke_user(str(principal.user_id))
    logger.info("user_sessions_revoked", extra={"user_id": str(principal.user_id)})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@auth_router.get("/users", response_model=list[UserRead])
async def list_users(session: SessionDep, principal: AdminUser) -> list[User]:
    result = await session.execute(select(User).order_by(User.created_at.asc()))
    return list(result.scalars().all())


@auth_router.patch("/users/{user_id}/role", response_model=UserRead)
async def set_user_role(
    user_id: uuid.UUID,
    payload: RoleUpdate,
    session: SessionDep,
    principal: AdminUser,
) -> User:
    """Change a user's role. Admin only -- the sole way to grant admin."""
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )
    if user.id == principal.user_id and payload.role != Role.ADMIN.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Refusing to remove your own admin role; promote another admin first",
        )

    user.role = payload.role
    session.add(user)
    # Tokens carry the role, so existing sessions would keep the old privileges.
    await revocation_store.revoke_user(str(user.id))
    logger.info(
        "user_role_changed",
        extra={"user_id": str(user.id), "role": payload.role, "by": str(principal.user_id)},
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
    # Every run spends real money at a provider, so this is a budget control as
    # much as an abuse control.
    await rate_limiter.check(
        "runs", str(principal.user_id), settings.rate_limit_runs_per_hour, 3600
    )

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

    task_id: Optional[str] = None
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
    run_status: Optional[str] = Query(default=None, alias="status"),
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
    payload: Optional[ExportRequest] = Body(default=None),
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
    # The Qdrant collection spans every tenant, so the search is scoped to the
    # caller. Admins search all of it, matching how list_runs and run_stats
    # already widen for the admin role.
    return await vector_service.search(
        payload.query,
        payload.limit,
        payload.min_score,
        owner_id=str(principal.user_id),
        include_all_owners=principal.has_at_least(Role.ADMIN),
    )


@runs_router.post("/{run_id}/stream-ticket", response_model=StreamTicket)
async def issue_stream_ticket(
    run_id: uuid.UUID, session: SessionDep, principal: CurrentUser
) -> StreamTicket:
    """Exchange a header-authenticated request for a ticket the socket accepts.

    Ownership is checked here, where the Authorization header is available, so
    the credential that ends up in the URL is already narrowed to one run.
    """
    run = await _load_run(session, run_id)
    _authorize_run(run, principal)
    return StreamTicket(
        ticket=create_stream_ticket(str(principal.user_id), principal.role, str(run_id)),
        expires_in=settings.ws_ticket_ttl_seconds,
    )


@runs_router.websocket("/{run_id}/stream")
async def stream_run(
    websocket: WebSocket, run_id: uuid.UUID, session: SessionDep
) -> None:
    """Stream pipeline events for a run.

    The credential is a query parameter because a browser cannot set headers on
    a websocket handshake -- and because a URL reaches proxy logs, access logs
    and browser history, what goes there is a ticket from
    POST /runs/{id}/stream-ticket: scoped to one run, valid for about a minute,
    and burned on first use. An access token was accepted here until every
    client had moved over; it is account-wide and lives an hour, so a copy
    recovered from a log was worth having.

    Redeeming the ticket establishes who is calling, not whether they may read
    *this* run. Every REST route pairs authentication with _authorize_run, and
    the events streamed here carry the same generated content those routes
    guard, so the pairing holds here too.
    """
    try:
        principal = await redeem_stream_ticket(
            websocket.query_params.get("ticket"), str(run_id)
        )
        run = await _load_run(session, run_id)
        _authorize_run(run, principal)
    except HTTPException:
        # One close code for "bad token", "no such run" and "not yours": before
        # the handshake completes there is no channel to carry a reason, and
        # distinguishing them here would let a caller enumerate run ids.
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()
    logger.info(
        "websocket_connected",
        extra={"run_id": str(run_id), "user_id": str(principal.user_id)},
    )

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
# Model registry
# ---------------------------------------------------------------------------


def _to_public(provider: ProviderConfig) -> ProviderPublic:
    """Registry entry plus whether it can actually be called right now.

    Computed per request rather than stored: a credential arrives from the
    environment or the credential store, so the same registry file is
    answerable differently on two deployments, and setting a key has to change
    the answer without anyone editing models.json.
    """
    return ProviderPublic(
        **provider.model_dump(exclude={"api_key"}),
        credential_available=not requires_credential(provider),
        credential_env_var=credential_env_var(provider),
        is_local_runtime=is_local_runtime(provider),
        credential_family=credential_family(provider),
        credential_source=credential_source(provider),
    )


@models_router.get("", response_model=list[ProviderPublic])
async def list_models(
    principal: CurrentUser, session: SessionDep
) -> list[ProviderPublic]:
    # The listing is what the UI decides selectability from, so it must not be
    # answered from a credential snapshot that predates an edit made in another
    # process. Cheap: one query at most every TTL_SECONDS.
    await credential_store.refresh_if_stale(session)
    return [_to_public(provider) for provider in model_registry.all()]


@models_router.post("", response_model=ProviderPublic, status_code=status.HTTP_201_CREATED)
async def upsert_model(payload: ProviderConfig, principal: AdminUser) -> ProviderConfig:
    # The schema validator only ran the checks that cost nothing, so the
    # resolving half happens here, off the event loop. This is the path an
    # attacker-supplied api_base actually arrives on.
    if payload.api_base:
        try:
            await validate_api_base_async(payload.api_base)
        except UnsafeEndpointError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc
    return _to_public(model_registry.upsert(payload))


@models_router.patch("/{provider_id}", response_model=ProviderPublic)
async def toggle_model(
    provider_id: str, payload: ProviderToggle, principal: AdminUser
) -> ProviderConfig:
    try:
        return _to_public(model_registry.set_enabled(provider_id, payload.enabled))
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


@models_router.post("/reload", response_model=list[ProviderPublic])
async def reload_models(principal: AdminUser) -> list[ProviderPublic]:
    return [_to_public(p) for p in model_registry.reload().providers]


# --- live provider catalogue ------------------------------------------------


@models_router.get("/catalog", response_model=list[FamilyDiscoveryPublic])
async def model_catalog(
    principal: AdminUser,
    session: SessionDep,
    refresh: bool = Query(default=False),
) -> list[FamilyDiscoveryPublic]:
    """Ask every configured provider which models its key can reach.

    Admin-only because it spends the stored credentials to make outbound calls
    on the caller's behalf, and because the answer names every model the
    account has access to.

    A family with no key is returned as `configured: false` with an empty list
    rather than omitted -- the UI shows it as a provider awaiting a key, which
    is the state an operator most needs to see.
    """
    await credential_store.refresh_if_stale(session)

    # Matched on model_key, not id: the same model added under a hand-picked id
    # is still the same model, and offering it again would create a duplicate
    # registry entry that fans out to one provider twice.
    existing = {p.model_key: p.id for p in model_registry.all()}

    discoveries = await model_discovery.discover_all(refresh=refresh)
    return [
        FamilyDiscoveryPublic(
            family=discovery.family,
            label=discovery.label,
            configured=discovery.configured,
            error=discovery.error,
            models=[
                DiscoveredModelPublic(
                    **{
                        field: getattr(model, field)
                        for field in (
                            "family",
                            "provider_label",
                            "model_key",
                            "remote_id",
                            "display_name",
                            "cost_per_1k_input",
                            "cost_per_1k_output",
                            "max_tokens",
                            "supports_json_mode",
                        )
                    },
                    in_registry=model.model_key in existing,
                    registry_id=existing.get(model.model_key),
                )
                for model in discovery.models
            ],
        )
        for discovery in discoveries
    ]


# --- bulk import / export --------------------------------------------------


@models_router.get("/export")
async def export_registry(principal: AdminUser) -> Response:
    """Download the registry as the JSON document that /import accepts.

    Round-trips deliberately: the export is the template for an edit-and-
    re-upload, so it has to be a valid import payload rather than a report.

    api_key is excluded. An inline credential is the one field that must not
    leave the server, and an export is the easiest possible way for one to end
    up in a chat message or a ticket.
    """
    registry = model_registry.load()
    document = {
        "version": registry.version,
        "providers": [
            provider.model_dump(mode="json", exclude={"api_key"})
            for provider in registry.providers
        ],
    }
    return Response(
        content=json.dumps(document, indent=2) + "\n",
        media_type="application/json",
        headers={
            "Content-Disposition": 'attachment; filename="models.json"'
        },
    )


@models_router.post("/import", response_model=RegistryImportResult)
async def import_registry(
    payload: RegistryImportRequest, principal: AdminUser
) -> RegistryImportResult:
    """Apply a supplied model list to the registry.

    Every api_base in the batch is resolved and checked before anything is
    written. Doing it up front is what makes a rejected upload a no-op: a
    single unsafe endpoint in a fifty-model file must not leave the first
    forty-nine applied.
    """
    for provider in payload.providers:
        if not provider.api_base:
            continue
        try:
            await validate_api_base_async(provider.api_base)
        except UnsafeEndpointError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"{provider.id}: {exc}",
            ) from exc

    before = {p.id for p in model_registry.all()}
    added, updated = model_registry.import_providers(
        payload.providers, replace=payload.mode == "replace"
    )
    after = model_registry.all()

    return RegistryImportResult(
        mode=payload.mode,
        added=added,
        updated=updated,
        removed=sorted(before - {p.id for p in after}),
        total=len(after),
        providers=[_to_public(p) for p in after],
    )


# ---------------------------------------------------------------------------
# Provider credentials
# ---------------------------------------------------------------------------


def _credential_statuses(
    rows: dict[str, ProviderCredential],
) -> list[CredentialStatus]:
    """Per-family credential state, with the value withheld.

    model_count is included because "configured" on its own does not tell an
    operator whether setting this key would achieve anything -- a key for a
    family with no registry entries changes nothing they can see.
    """
    providers = model_registry.all()
    undecryptable = credential_store.undecryptable_families()

    statuses: list[CredentialStatus] = []
    for family in PROVIDER_FAMILIES:
        row = rows.get(family.name)
        from_db = credential_store.get(family.name) is not None
        from_env = bool(
            (getattr(settings, family.settings_attr, None) or "").strip()
        )
        statuses.append(
            CredentialStatus(
                family=family.name,
                label=family.label,
                env_var=family.env_var,
                console_url=family.console_url,
                configured=from_db or from_env,
                # From the stored row, so it describes the key on record even
                # when the effective one comes from the environment.
                last4=row.last4 if row else None,
                source="database" if from_db else ("environment" if from_env else None),
                needs_reentry=family.name in undecryptable,
                updated_at=row.updated_at if row else None,
                model_count=sum(
                    1
                    for p in providers
                    if p.enabled
                    and not p.api_key_env
                    and not p.api_key
                    and family.matches(p.provider)
                ),
            )
        )

    statuses.extend(_entry_credential_statuses(providers))
    return statuses


def _entry_credential_statuses(
    providers: Sequence[ProviderConfig],
) -> list[CredentialStatus]:
    """Rows for variables named by a registry entry's api_key_env.

    A hosted deployment of an open-weight model belongs to no family -- its
    `provider` still reads "Ollama" -- so it names its own variable instead.
    Nothing above describes those, which left an operator exporting
    OLLAMA_CLOUD_API_KEY seeing no mention of it on this page while the Models
    page showed the key as present: two screens disagreeing about one
    credential, with the one they went to for keys being the wrong one.

    Read-only by construction. There is no family to store a value against, so
    the environment is the only place these can be set.
    """
    covered = {family.env_var for family in PROVIDER_FAMILIES}

    grouped: dict[str, list[ProviderConfig]] = {}
    for provider in providers:
        variable = (provider.api_key_env or "").strip()
        if variable and variable not in covered:
            grouped.setdefault(variable, []).append(provider)

    statuses: list[CredentialStatus] = []
    for variable, entries in sorted(grouped.items()):
        present = bool(os.environ.get(variable, "").strip())
        # Name it after the provider when the entries agree, since that is what
        # the operator recognises; the variable is shown beside it either way.
        names = sorted({e.provider.strip() for e in entries if e.provider.strip()})
        statuses.append(
            CredentialStatus(
                family=f"env:{variable}",
                label=names[0] if len(names) == 1 else variable,
                env_var=variable,
                configured=present,
                source="environment" if present else None,
                editable=False,
                model_count=sum(1 for e in entries if e.enabled),
            )
        )
    return statuses


async def _credential_rows(session: AsyncSession) -> dict[str, ProviderCredential]:
    rows = (await session.execute(select(ProviderCredential))).scalars().all()
    return {row.family: row for row in rows}


@models_router.get("/credentials", response_model=list[CredentialStatus])
async def list_credentials(
    principal: AdminUser, session: SessionDep
) -> list[CredentialStatus]:
    await credential_store.refresh(session)
    return _credential_statuses(await _credential_rows(session))


@models_router.put("/credentials/{family}", response_model=CredentialStatus)
async def set_credential(
    family: str,
    payload: CredentialWrite,
    principal: AdminUser,
    session: SessionDep,
) -> CredentialStatus:
    """Store a provider key. Admin only; the value is never readable again."""
    if family not in FAMILIES_BY_NAME:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown provider family: {family}",
        )

    try:
        await credential_store.set(
            session, family, payload.api_key, updated_by=principal.user_id
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    # The cached listing was produced with the old key -- or with none -- so it
    # would answer the next catalogue request with a stale "not configured".
    model_discovery.invalidate(family)

    logger.info(
        "provider_credential_set",
        extra={"family": family, "by": str(principal.user_id)},
    )
    await session.flush()
    return next(
        s
        for s in _credential_statuses(await _credential_rows(session))
        if s.family == family
    )


@models_router.delete(
    "/credentials/{family}", status_code=status.HTTP_204_NO_CONTENT
)
async def clear_credential(
    family: str, principal: AdminUser, session: SessionDep
) -> Response:
    if family not in FAMILIES_BY_NAME:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown provider family: {family}",
        )
    await credential_store.clear(session, family)
    model_discovery.invalidate(family)
    logger.info(
        "provider_credential_deleted",
        extra={"family": family, "by": str(principal.user_id)},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@models_router.post(
    "/credentials/{family}/test", response_model=CredentialTestResult
)
async def test_credential(
    family: str, principal: AdminUser, session: SessionDep
) -> CredentialTestResult:
    """Prove a key against the provider instead of assuming it is present.

    The check calls the provider's *listing* endpoint, so it settles whether
    the key is accepted and what it can reach, and the provider's own wording
    comes back on failure -- "rejected" and "out of quota" need opposite fixes
    and must not collapse into one message.

    Listing does not consume completion quota, so a pass does not promise the
    account can afford a run; the success message says so rather than implying
    a guarantee it cannot make. Spending a real completion to find out would
    charge the operator for a diagnostic.
    """
    if family not in FAMILIES_BY_NAME:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown provider family: {family}",
        )

    await credential_store.refresh(session)
    discovery = next(
        d
        for d in await model_discovery.discover_all(refresh=True)
        if d.family == family
    )

    if not discovery.configured:
        return CredentialTestResult(
            family=family, ok=False, detail="no key is configured"
        )
    if discovery.error:
        return CredentialTestResult(family=family, ok=False, detail=discovery.error)
    return CredentialTestResult(
        family=family,
        ok=True,
        detail=(
            f"key is valid — {len(discovery.models)} chat models reachable "
            "(does not check remaining quota)"
        ),
        model_count=len(discovery.models),
    )


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
