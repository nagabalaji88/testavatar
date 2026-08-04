"""Run event bus.

Pipeline stages execute inside Celery workers while websocket clients are
attached to API processes, so progress events travel over a Redis pub/sub
channel keyed by run id. Each API process fans a subscription out to its
locally connected sockets.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, AsyncIterator, Optional, Union

import redis.asyncio as aioredis
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

CHANNEL_TEMPLATE = "mpg:run:{run_id}"
EVENT_HISTORY_KEY = "mpg:run:{run_id}:history"
HISTORY_LIMIT = 500
HISTORY_TTL_SECONDS = 60 * 60 * 24


class EventType(str, Enum):
    RUN_QUEUED = "run.queued"
    RUN_STARTED = "run.started"
    STAGE_STARTED = "stage.started"
    STAGE_COMPLETED = "stage.completed"
    CANDIDATE_STARTED = "candidate.started"
    CANDIDATE_COMPLETED = "candidate.completed"
    CANDIDATE_FAILED = "candidate.failed"
    EVALUATION_COMPLETED = "evaluation.completed"
    CONSENSUS_COMPLETED = "consensus.completed"
    RUN_COMPLETED = "run.completed"
    RUN_FAILED = "run.failed"
    LOG = "log"


class RunEvent(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    run_id: str
    type: EventType
    payload: dict[str, Any] = Field(default_factory=dict)
    emitted_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def encode(self) -> str:
        return self.model_dump_json()


class EventBus:
    """Thin async wrapper over Redis pub/sub with a replayable backlog."""

    def __init__(self, redis_url: Optional[str] = None) -> None:
        self._redis_url = redis_url or settings.redis_url
        self._client: Optional[aioredis.Redis] = None
        self._lock = asyncio.Lock()

    async def client(self) -> aioredis.Redis:
        if self._client is None:
            async with self._lock:
                if self._client is None:
                    self._client = aioredis.from_url(
                        self._redis_url, encoding="utf-8", decode_responses=True
                    )
        return self._client

    async def publish(self, event: RunEvent) -> None:
        client = await self.client()
        channel = CHANNEL_TEMPLATE.format(run_id=event.run_id)
        history = EVENT_HISTORY_KEY.format(run_id=event.run_id)
        encoded = event.encode()

        pipe = client.pipeline()
        pipe.publish(channel, encoded)
        pipe.rpush(history, encoded)
        pipe.ltrim(history, -HISTORY_LIMIT, -1)
        pipe.expire(history, HISTORY_TTL_SECONDS)
        await pipe.execute()

    async def emit(
        self, run_id: Union[str, uuid.UUID], event_type: EventType, **payload: Any
    ) -> None:
        await self.publish(
            RunEvent(run_id=str(run_id), type=event_type, payload=payload)
        )

    async def history(self, run_id: Union[str, uuid.UUID]) -> list[RunEvent]:
        client = await self.client()
        raw = await client.lrange(EVENT_HISTORY_KEY.format(run_id=run_id), 0, -1)
        events: list[RunEvent] = []
        for item in raw:
            try:
                events.append(RunEvent.model_validate_json(item))
            except ValueError:
                logger.warning("event_history_decode_failed", extra={"raw": item[:200]})
        return events

    async def subscribe(self, run_id: Union[str, uuid.UUID]) -> AsyncIterator[RunEvent]:
        """Yield backlog then live events for a run until the caller detaches."""
        client = await self.client()
        pubsub = client.pubsub()
        channel = CHANNEL_TEMPLATE.format(run_id=run_id)
        await pubsub.subscribe(channel)

        seen: set[str] = set()
        try:
            for event in await self.history(run_id):
                seen.add(event.id)
                yield event

            while True:
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=1.0
                )
                if message is None:
                    continue
                try:
                    event = RunEvent.model_validate_json(message["data"])
                except ValueError:
                    continue
                if event.id in seen:
                    continue
                seen.add(event.id)
                yield event
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


event_bus = EventBus()


def publish_sync(event: RunEvent) -> None:
    """Publish from synchronous contexts (Celery task bodies)."""
    import redis as sync_redis

    client = sync_redis.from_url(settings.redis_url, decode_responses=True)
    try:
        encoded = event.encode()
        history = EVENT_HISTORY_KEY.format(run_id=event.run_id)
        pipe = client.pipeline()
        pipe.publish(CHANNEL_TEMPLATE.format(run_id=event.run_id), encoded)
        pipe.rpush(history, encoded)
        pipe.ltrim(history, -HISTORY_LIMIT, -1)
        pipe.expire(history, HISTORY_TTL_SECONDS)
        pipe.execute()
    finally:
        client.close()


def json_safe(value: Any) -> Any:
    """Coerce arbitrary payloads into JSON-serialisable structures."""
    return json.loads(json.dumps(value, default=str))
