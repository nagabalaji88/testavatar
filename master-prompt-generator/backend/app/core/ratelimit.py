"""Fixed-window rate limiting backed by Redis.

Guards two distinct abuses: credential stuffing against the auth routes, and
cost exhaustion against run creation, where every request spends real money at
a provider.

Limits fail open. A Redis outage should not lock every user out of the product;
the failure is logged so it is visible rather than silent.
"""

from __future__ import annotations

import time
from typing import Optional

import redis.asyncio as aioredis
from fastapi import HTTPException, Request, status

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

KEY = "mpg:ratelimit:{scope}:{identity}:{window}"


class RateLimiter:
    def __init__(self, redis_url: Optional[str] = None) -> None:
        self._redis_url = redis_url or settings.redis_url
        self._client: Optional[aioredis.Redis] = None

    async def client(self) -> aioredis.Redis:
        if self._client is None:
            self._client = aioredis.from_url(
                self._redis_url, encoding="utf-8", decode_responses=True
            )
        return self._client

    async def check(self, scope: str, identity: str, limit: int, window_seconds: int) -> None:
        if not settings.rate_limit_enabled or limit <= 0:
            return

        window = int(time.time()) // window_seconds
        key = KEY.format(scope=scope, identity=identity, window=window)

        try:
            client = await self.client()
            pipe = client.pipeline()
            pipe.incr(key)
            pipe.expire(key, window_seconds)
            count, _ = await pipe.execute()
        except Exception as exc:
            logger.error("rate_limit_store_unavailable", extra={"error": str(exc)})
            return

        if int(count) > limit:
            retry_after = window_seconds - (int(time.time()) % window_seconds)
            logger.warning(
                "rate_limit_exceeded",
                extra={"scope": scope, "identity": identity, "count": int(count)},
            )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded for {scope}. Retry in {retry_after}s.",
                headers={"Retry-After": str(retry_after)},
            )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


rate_limiter = RateLimiter()


def client_identity(request: Request) -> str:
    """Best-effort caller identity for anonymous routes.

    X-Forwarded-For is trusted only because the app is expected to sit behind
    the bundled nginx; exposed directly, a caller can spoof it and sidestep the
    limit. Deployments that terminate elsewhere must strip or rewrite it.
    """
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
