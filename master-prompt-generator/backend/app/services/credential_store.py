"""Provider credentials stored in the database, cached for synchronous reads.

Why a cache rather than a query per call: the credential is needed by
`llm_service._api_key_for`, which is synchronous and sits on the hot path of
every provider call. Making it async would push `await` through the whole
completion path -- including `fan_out`'s per-provider builder -- to fetch a
value that changes when an admin edits it and at no other time.

So the process holds a decrypted snapshot and refreshes it at the points where
staleness would actually be visible:

  * API startup, so the first request is current;
  * immediately after a write, so the writer sees its own change;
  * the start of every pipeline run, which is what makes a key entered in the
    UI take effect in the *worker* process without a restart;
  * on read, once TTL_SECONDS have passed, so a long-idle process still
    converges on an out-of-band change.

The snapshot holds plaintext in memory. That is the same exposure as the
environment variables it replaces -- both are readable from the process -- and
is what lets the value never be written to disk unencrypted.
"""

from __future__ import annotations

import time
import uuid
from typing import Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import (
    CredentialDecryptionError,
    decrypt_secret,
    encrypt_secret,
    last4,
)
from app.core.logging import get_logger
from app.core.provider_families import FAMILIES_BY_NAME
from app.models.domain import ProviderCredential

logger = get_logger(__name__)

# Long enough that the cache is doing its job on a busy instance, short enough
# that an operator who edits a key in one replica does not wait on a restart
# for the others. The explicit refresh points above cover the cases that
# matter; this is the backstop.
TTL_SECONDS = 30.0


class CredentialStore:
    """Decrypted provider keys, readable synchronously."""

    def __init__(self) -> None:
        self._keys: dict[str, str] = {}
        self._undecryptable: set[str] = set()
        self._loaded_at: float = 0.0

    # -- synchronous read (hot path) ---------------------------------------

    def get(self, family: str) -> Optional[str]:
        """The stored key for a family, or None.

        Never raises and never awaits: an unreadable or absent credential is
        reported as "not configured", which the caller already handles by
        falling back to the environment.
        """
        return self._keys.get(family)

    def is_stale(self) -> bool:
        return (time.monotonic() - self._loaded_at) > TTL_SECONDS

    def configured_families(self) -> frozenset[str]:
        return frozenset(self._keys)

    def undecryptable_families(self) -> frozenset[str]:
        return frozenset(self._undecryptable)

    # -- async refresh -----------------------------------------------------

    async def refresh(self, session: AsyncSession) -> None:
        rows = (await session.execute(select(ProviderCredential))).scalars().all()

        keys: dict[str, str] = {}
        undecryptable: set[str] = set()
        for row in rows:
            try:
                keys[row.family] = decrypt_secret(row.encrypted_key)
            except CredentialDecryptionError:
                # Almost always a rotated encryption key. Reported to the UI as
                # "re-enter this key" rather than raised: the models page has to
                # keep working, and the operator needs to be told which entry
                # to fix.
                undecryptable.add(row.family)
                logger.warning(
                    "provider_credential_undecryptable",
                    extra={"family": row.family},
                )

        self._keys = keys
        self._undecryptable = undecryptable
        self._loaded_at = time.monotonic()

    async def refresh_if_stale(self, session: AsyncSession) -> None:
        if self.is_stale():
            await self.refresh(session)

    # -- writes ------------------------------------------------------------

    async def set(
        self,
        session: AsyncSession,
        family: str,
        api_key: str,
        *,
        updated_by: Optional[uuid.UUID] = None,
    ) -> ProviderCredential:
        if family not in FAMILIES_BY_NAME:
            raise KeyError(family)

        secret = api_key.strip()
        if not secret:
            raise ValueError("api_key must not be blank")

        row = await session.get(ProviderCredential, family)
        if row is None:
            row = ProviderCredential(family=family)
            session.add(row)

        row.encrypted_key = encrypt_secret(secret)
        row.last4 = last4(secret)
        row.updated_by = updated_by
        # Assigned explicitly: default_factory only fires on insert, so an
        # update would otherwise keep reporting when the key was first stored.
        from app.models.domain import _utcnow

        row.updated_at = _utcnow()

        await session.flush()
        # The writer's own process must see the new value immediately -- an
        # admin who sets a key and launches a run should not race the TTL.
        self._keys[family] = secret
        self._undecryptable.discard(family)
        logger.info("provider_credential_stored", extra={"family": family})
        return row

    async def clear(self, session: AsyncSession, family: str) -> bool:
        result = await session.execute(
            delete(ProviderCredential).where(ProviderCredential.family == family)
        )
        removed = bool(result.rowcount)
        self._keys.pop(family, None)
        self._undecryptable.discard(family)
        if removed:
            logger.info("provider_credential_cleared", extra={"family": family})
        return removed


credential_store = CredentialStore()
