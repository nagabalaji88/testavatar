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


def _create_token(subject: str, role: Role, token_type: str, ttl_minutes: int) -> str:
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": subject,
        "role": role.value,
        "type": token_type,
        "jti": uuid.uuid4().hex,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=ttl_minutes)).timestamp()),
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


def authenticate_websocket(token: Optional[str]) -> Principal:
    """Verify a token supplied as a websocket query parameter."""
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing token"
        )
    payload = decode_token(token)
    return Principal(user_id=uuid.UUID(payload.sub), role=payload.role)
