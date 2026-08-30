"""Structured JSON logging with trace correlation."""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Optional

from app.core.config import settings

run_id_ctx: ContextVar[Optional[str]] = ContextVar("run_id", default=None)
request_id_ctx: ContextVar[Optional[str]] = ContextVar("request_id", default=None)

_RESERVED = frozenset(
    {
        "args", "asctime", "created", "exc_info", "exc_text", "filename",
        "funcName", "levelname", "levelno", "lineno", "module", "msecs",
        "message", "msg", "name", "pathname", "process", "processName",
        "relativeCreated", "stack_info", "thread", "threadName", "taskName",
    }
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "service": settings.otel_service_name,
            "environment": settings.environment,
        }

        if (run_id := run_id_ctx.get()) is not None:
            payload["run_id"] = run_id
        if (request_id := request_id_ctx.get()) is not None:
            payload["request_id"] = request_id
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value

        return json.dumps(payload, default=str, separators=(",", ":"))


def configure_logging() -> None:
    """Install the JSON formatter on the root logger exactly once."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(settings.log_level.upper())

    for noisy in ("uvicorn.access", "httpx", "LiteLLM", "litellm"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
