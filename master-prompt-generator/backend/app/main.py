"""FastAPI application entrypoint for the Master Prompt Generator."""

from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Awaitable, Callable

from fastapi import FastAPI, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.events import event_bus
from app.core.logging import configure_logging, get_logger, request_id_ctx
from app.core.telemetry import configure_telemetry
from app.db.session import dispose_database, init_database, session_scope
from app.services.credential_store import credential_store
from app.services.model_registry import model_registry
from app.services.vector_service import vector_service

configure_logging()
configure_telemetry()
logger = get_logger(__name__)

API_VERSION = "1.0.0"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    logger.info(
        "startup_begin",
        extra={"environment": settings.environment, "version": API_VERSION},
    )
    await init_database()
    model_registry.load()
    # Before anything reports on provider availability. The credential store is
    # read synchronously from the LLM path, so the snapshot has to exist by the
    # time the first request lands.
    async with session_scope() as session:
        await credential_store.refresh(session)
    await vector_service.ensure_collection()
    logger.info(
        "startup_complete",
        extra={
            "enabled_providers": [p.id for p in model_registry.enabled()],
            "stored_credentials": sorted(credential_store.configured_families()),
        },
    )
    try:
        yield
    finally:
        await vector_service.close()
        await event_bus.close()
        await dispose_database()
        logger.info("shutdown_complete")


app = FastAPI(
    title=settings.app_name,
    version=API_VERSION,
    description=(
        "Multi-LLM orchestration platform that fans prompt generation across "
        "providers, scores every candidate with an AI Judge, and synthesises an "
        "Elite Consensus Prompt."
    ),
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-Id", "X-Process-Time-Ms"],
)
app.add_middleware(GZipMiddleware, minimum_size=1024)


@app.middleware("http")
async def request_context(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    request_id = request.headers.get("X-Request-Id") or uuid.uuid4().hex
    request_id_ctx.set(request_id)
    started = time.perf_counter()

    try:
        response = await call_next(request)
    except Exception:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        logger.exception(
            "request_unhandled_error",
            extra={
                "method": request.method,
                "path": request.url.path,
                "duration_ms": elapsed_ms,
            },
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "Internal server error", "request_id": request_id},
            headers={"X-Request-Id": request_id},
        )

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    response.headers["X-Request-Id"] = request_id
    response.headers["X-Process-Time-Ms"] = str(elapsed_ms)

    if request.url.path not in {settings.metrics_path, "/health"}:
        logger.info(
            "request_completed",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": elapsed_ms,
            },
        )
    return response


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "request_id": request_id_ctx.get()},
        headers=getattr(exc, "headers", None),
    )


def _serialisable_errors(exc: RequestValidationError) -> list[dict[str, Any]]:
    """Pydantic's error list, with the parts json.dumps refuses removed.

    A validator that raises ValueError -- which is how every custom check in
    schemas.py rejects input -- puts the exception *object* in the error's
    `ctx`. json.dumps cannot encode it, so building the 422 body raised
    TypeError inside the handler and the client got a bare 500 with no
    indication of what was wrong. That made the api_base SSRF rejection, in
    particular, indistinguishable from a server fault.

    `msg` already carries the validator's message, so ctx is stringified rather
    than preserved: it exists to explain the failure, not to be machine-read.
    """
    cleaned: list[dict[str, Any]] = []
    for error in exc.errors():
        entry = {k: v for k, v in error.items() if k != "ctx"}
        # loc is a tuple and may contain ints for list indices; both encode
        # fine, but make it a list so the shape is stable JSON.
        if "loc" in entry:
            entry["loc"] = list(entry["loc"])
        if ctx := error.get("ctx"):
            entry["ctx"] = {key: str(value) for key, value in ctx.items()}
        cleaned.append(entry)
    return cleaned


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "detail": "Request validation failed",
            "errors": _serialisable_errors(exc),
            "request_id": request_id_ctx.get(),
        },
    )


app.include_router(api_router, prefix=settings.api_v1_prefix)


@app.get("/", tags=["system"])
async def root() -> dict[str, str]:
    return {
        "service": settings.app_name,
        "version": API_VERSION,
        "docs": "/docs",
        "api": settings.api_v1_prefix,
    }


@app.get("/health", tags=["system"])
async def liveness() -> dict[str, str]:
    return {"status": "ok", "version": API_VERSION}


@app.get(settings.metrics_path, tags=["system"])
async def metrics() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
