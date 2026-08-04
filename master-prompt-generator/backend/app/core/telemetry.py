"""OpenTelemetry tracing plus Prometheus metric definitions."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import Counter, Gauge, Histogram

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

RUNS_STARTED = Counter("mpg_runs_started_total", "Pipeline runs started")
RUNS_COMPLETED = Counter(
    "mpg_runs_completed_total", "Pipeline runs finished", ["status"]
)
RUNS_IN_FLIGHT = Gauge("mpg_runs_in_flight", "Pipeline runs currently executing")
LLM_CALLS = Counter(
    "mpg_llm_calls_total", "LLM invocations", ["model_id", "phase", "outcome"]
)
LLM_LATENCY = Histogram(
    "mpg_llm_latency_seconds",
    "LLM call wall-clock latency",
    ["model_id", "phase"],
    buckets=(0.5, 1, 2, 5, 10, 20, 40, 80, 160),
)
LLM_TOKENS = Counter(
    "mpg_llm_tokens_total", "Token usage", ["model_id", "direction"]
)
LLM_COST = Counter("mpg_llm_cost_usd_total", "Estimated spend in USD", ["model_id"])
STAGE_LATENCY = Histogram(
    "mpg_stage_latency_seconds",
    "Pipeline stage duration",
    ["stage"],
    buckets=(0.1, 0.5, 1, 5, 10, 30, 60, 120, 300),
)

_tracer: trace.Tracer | None = None


def configure_telemetry() -> None:
    """Initialise the tracer provider; a no-op exporter when disabled."""
    global _tracer

    resource = Resource.create(
        {
            "service.name": settings.otel_service_name,
            "deployment.environment": settings.environment,
        }
    )
    provider = TracerProvider(resource=resource)

    if settings.otel_enabled and settings.otel_exporter_otlp_endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )

            provider.add_span_processor(
                BatchSpanProcessor(
                    OTLPSpanExporter(
                        endpoint=f"{settings.otel_exporter_otlp_endpoint}/v1/traces"
                    )
                )
            )
            logger.info(
                "otel_exporter_configured",
                extra={"endpoint": settings.otel_exporter_otlp_endpoint},
            )
        except Exception as exc:  # exporter must never break the app
            logger.warning("otel_exporter_failed", extra={"error": str(exc)})

    trace.set_tracer_provider(provider)
    _tracer = trace.get_tracer(settings.otel_service_name)


def get_tracer() -> trace.Tracer:
    global _tracer
    if _tracer is None:
        _tracer = trace.get_tracer(settings.otel_service_name)
    return _tracer


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[trace.Span]:
    """Start a span, recording exceptions and marking the status on failure."""
    with get_tracer().start_as_current_span(name) as current:
        for key, value in attributes.items():
            if value is not None:
                current.set_attribute(key, value)
        try:
            yield current
        except Exception as exc:
            current.record_exception(exc)
            current.set_status(trace.Status(trace.StatusCode.ERROR, str(exc)))
            raise
