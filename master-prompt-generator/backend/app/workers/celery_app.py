"""Celery application and the asynchronous pipeline task."""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from celery import Celery
from celery.signals import worker_process_init

from app.core.config import settings
from app.core.events import EventType, RunEvent, publish_sync
from app.core.logging import configure_logging, get_logger
from app.core.telemetry import configure_telemetry
from app.models.schemas import RunCreate

logger = get_logger(__name__)

celery_app = Celery(
    "mpg",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_track_started=True,
    worker_prefetch_multiplier=1,
    task_time_limit=60 * 20,
    task_soft_time_limit=60 * 18,
    broker_connection_retry_on_startup=True,
    result_expires=60 * 60 * 24,
)


# One event loop, reused for every task this worker process ever runs.
#
# app.db.session.engine, app.core.events.event_bus, app.core.revocation
# .revocation_store and app.core.ratelimit.rate_limiter are all process-global
# singletons that lazily open an asyncpg/Redis connection bound to whichever
# event loop is running the first time they are used. A loop created fresh
# per task and closed at the end leaves every one of those connections
# pointing at a dead loop: no exception is raised, the next task's await on
# that connection simply never wakes up. A single persistent loop for the
# worker process's lifetime -- the same shape uvicorn already uses -- means
# every global singleton only ever sees one loop, for as long as the process
# lives, exactly as it would in a non-Celery deployment.
_worker_loop: Optional[asyncio.AbstractEventLoop] = None


@worker_process_init.connect
def _init_worker(**_: Any) -> None:
    global _worker_loop
    configure_logging()
    configure_telemetry()
    _worker_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_worker_loop)
    logger.info("celery_worker_ready")


def _get_worker_loop() -> asyncio.AbstractEventLoop:
    """Fall back to creating the loop lazily (e.g. under eager/test execution)."""
    global _worker_loop
    if _worker_loop is None or _worker_loop.is_closed():
        _worker_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_worker_loop)
    return _worker_loop


@celery_app.task(
    name="mpg.execute_pipeline",
    bind=True,
    max_retries=0,
    acks_late=True,
)
def execute_pipeline_task(
    self, run_id: str, request: dict[str, Any], provider_ids: list[str]
) -> dict[str, Any]:
    """Execute the LangGraph pipeline for a queued run on the worker's loop."""
    from app.agents.graph import execute_pipeline

    payload = RunCreate.model_validate(request)
    loop = _get_worker_loop()
    try:
        loop.run_until_complete(execute_pipeline(run_id, payload, provider_ids))
        return {"run_id": run_id, "status": "completed"}
    except Exception as exc:
        logger.error(
            "pipeline_task_failed",
            extra={"run_id": run_id, "error": str(exc), "task_id": self.request.id},
        )
        publish_sync(
            RunEvent(
                run_id=run_id,
                type=EventType.RUN_FAILED,
                payload={"error": f"{type(exc).__name__}: {exc}"},
            )
        )
        return {"run_id": run_id, "status": "failed", "error": str(exc)}
