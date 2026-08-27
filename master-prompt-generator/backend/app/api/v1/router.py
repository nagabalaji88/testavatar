"""API v1 aggregate router."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.endpoints import (
    auth_router,
    debate_router,
    models_router,
    runs_router,
    system_router,
)

api_router = APIRouter()
api_router.include_router(auth_router)
api_router.include_router(runs_router)
api_router.include_router(models_router)
api_router.include_router(debate_router)
api_router.include_router(system_router)

__all__ = ["api_router"]
