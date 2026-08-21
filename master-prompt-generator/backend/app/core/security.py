"""Authentication primitives: password hashing, JWT issuance, RBAC checks."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Final, Optional

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from passlib.context import CryptContext
from pydantic import BaseModel, ValidationError

from app.core.config import settings

ALGORITHM: Final[str] = settings.jwt_algorithm

pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl=f"{settings.api_v1_prefix}/auth/login", auto_error=False
)


class Role(str, Enum):
    VIEWER = "viewer"
    ENGINEER = "engineer"
    ADMIN = "admin"


ROLE_RANK: Final[dict[Role, int]] = {Role.VIEWER: 0, Role.ENGINEER: 1, Role.ADMIN: 2}


class TokenPayload(BaseModel):
    sub: str
    role: Role
    type: str
    jti: str
    exp: int
    iat: int
    # Set only on a stream ticket, naming the single run it may open. Without
    # it a ticket minted for one run would open any of them.
    run_id: Optional[str] = None


class Principal(BaseModel):
    """The authenticated caller, derived from a verified access token."""

    user_id: uuid.UUID
    role: Role

    def has_at_least(self, required: Role) -> bool:
        return ROLE_RANK[self.role] >= ROLE_RANK[required]


def hash_password(raw_password: str) -> str:
    return pwd_context.hash(raw_password)


def verify_password(raw_password: str, hashed_password: str) -> bool:
    try:
        return pwd_context.verify(raw_password, hashed_password)
    except ValueError:
        return False


def _create_token(
    subject: str,
    role: Role,
    token_type: str,
    ttl_minutes: float,
    **extra_claims: Any,
) -> str:
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": subject,
        "role": role.value,
        "type": token_type,
        "jti": uuid.uuid4().hex,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=ttl_minutes)).timestamp()),
        **extra_claims,
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=ALGORITHM)


def create_access_token(subject: str, role: Role) -> str:
    return _create_token(subject, role, "access", settings.access_token_ttl_minutes)


def create_refresh_token(subject: str, role: Role) -> str:
    return _create_token(subject, role, "refresh", settings.refresh_token_ttl_minutes)


def decode_token(token: str, expected_type: str = "access") -> TokenPayload:
    try:
        raw = jwt.decode(token, settings.jwt_secret_key, algorithms=[ALGORITHM])
        payload = TokenPayload.model_validate(raw)
    except (jwt.PyJWTError, ValidationError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired credentials",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    if payload.type != expected_type:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Expected a {expected_type} token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return payload


async def get_current_principal(token: Optional[str] = Depends(oauth2_scheme)) -> Principal:
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    payload = decode_token(token)
    try:
        user_id = uuid.UUID(payload.sub)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Malformed subject claim"
        ) from exc

    # A signed token is not enough: it may have been logged out, or issued
    # before the account's privileges changed.
    from app.core.revocation import revocation_store

    if await revocation_store.is_revoked(payload.jti, payload.sub, payload.iat):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has been revoked",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return Principal(user_id=user_id, role=payload.role)


def require_role(required: Role):
    """Dependency factory enforcing a minimum role for a route."""

    async def _guard(principal: Principal = Depends(get_current_principal)) -> Principal:
        if not principal.has_at_least(required):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{required.value}' or higher is required",
            )
        return principal

    return _guard


WS_TICKET_TYPE = "ws_ticket"


def create_stream_ticket(subject: str, role: Role, run_id: str) -> str:
    """Mint a single-use, short-lived credential for one run's event stream.

    A websocket handshake cannot carry an Authorization header, so whatever
    authenticates it travels in the URL -- and URLs are written to proxy logs,
    access logs and browser history. Sending the access token there exposes an
    hour-long, account-wide credential to all three. This ticket is scoped to
    one run, expires in about a minute, and is burned on first use, so a copy
    recovered from a log is worth nothing.
    """
    return _create_token(
        subject,
        role,
        WS_TICKET_TYPE,
        settings.ws_ticket_ttl_seconds / 60,
        run_id=run_id,
    )


async def redeem_stream_ticket(ticket: Optional[str], run_id: str) -> Principal:
    """Verify and consume a stream ticket, or raise HTTPException."""
    if not ticket:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing ticket"
        )
    payload = decode_token(ticket, expected_type=WS_TICKET_TYPE)

    if payload.run_id != run_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Ticket was issued for a different run",
        )

    from app.core.revocation import revocation_store

    # Also catches a ticket belonging to a session that has since logged out,
    # because revoke_user covers every jti issued before that moment.
    if await revocation_store.is_revoked(payload.jti, payload.sub, payload.iat):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Ticket already used"
        )
    await revocation_store.revoke_token(payload.jti, payload.exp)

    return Principal(user_id=uuid.UUID(payload.sub), role=payload.role)


async def authenticate_websocket(token: Optional[str]) -> Principal:
    """Verify a full access token supplied as a websocket query parameter.

    Retained for callers that have not moved to tickets. Unlike the previous
    version this honours revocation, so a logged-out token no longer opens a
    socket.
    """
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing token"
        )
    payload = decode_token(token)

    from app.core.revocation import revocation_store

    if await revocation_store.is_revoked(payload.jti, payload.sub, payload.iat):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Token has been revoked"
        )
    return Principal(user_id=uuid.UUID(payload.sub), role=payload.role)
