"""Token revocation.

JWTs are self-validating, so signing one out requires state. Two mechanisms:

  * per-token denial, keyed by the token's `jti`, used by logout;
  * per-user cutoff, which invalidates every token issued before a timestamp,
    used when a role changes or an account is compromised.

Both live in Redis with a TTL matched to the token lifetime, so the store never
grows beyond the set of tokens that could still be presented.
"""

from __future__ import annotations

import time
from typing import Optional

import redis.asyncio as aioredis

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

JTI_KEY = "mpg:revoked:jti:{jti}"
USER_KEY = "mpg:revoked:user:{user_id}"
MAX_TOKEN_TTL_SECONDS = 60 * settings.refresh_token_ttl_minutes


class RevocationStore:
    def __init__(self, redis_url: Optional[str] = None) -> None:
        self._redis_url = redis_url or settings.redis_url
        self._client: Optional[aioredis.Redis] = None

    async def client(self) -> aioredis.Redis:
        if self._client is None:
            self._client = aioredis.from_url(
                self._redis_url, encoding="utf-8", decode_responses=True
            )
        return self._client

    async def revoke_token(self, jti: str, expires_at: int) -> None:
        ttl = max(1, expires_at - int(time.time()))
        client = await self.client()
        await client.setex(JTI_KEY.format(jti=jti), ttl, "1")

    async def revoke_user(self, user_id: str) -> None:
        """Invalidate every token already issued to this user."""
        client = await self.client()
        await client.setex(
            USER_KEY.format(user_id=user_id), MAX_TOKEN_TTL_SECONDS, str(int(time.time()))
        )

    async def is_revoked(self, jti: str, user_id: str, issued_at: int) -> bool:
        """True when the token must be rejected.

        On a store outage the answer depends on `strict_token_revocation`:
        deny (production default, correctness) or allow (local default,
        availability). Either way the failure is logged rather than swallowed.
        """
        try:
            client = await self.client()
            pipe = client.pipeline()
            pipe.exists(JTI_KEY.format(jti=jti))
            pipe.get(USER_KEY.format(user_id=user_id))
            denied, cutoff = await pipe.execute()
        except Exception as exc:
            logger.error(
                "revocation_store_unavailable",
                extra={"error": str(exc), "strict": settings.strict_token_revocation},
            )
            return bool(settings.strict_token_revocation)

        if denied:
            return True
        if cutoff is not None:
            try:
                return issued_at < int(cutoff)
            except (TypeError, ValueError):
                return False
        return False

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


revocation_store = RevocationStore()
