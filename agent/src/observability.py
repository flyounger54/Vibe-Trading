"""Operational logging and Prometheus metrics for the local runtime."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Mapping

from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Gauge, Histogram, generate_latest


REGISTRY = CollectorRegistry(auto_describe=True)

HTTP_REQUESTS = Counter(
    "vibe_http_requests_total",
    "HTTP requests completed by the API.",
    ("method", "route", "status"),
    registry=REGISTRY,
)
HTTP_LATENCY = Histogram(
    "vibe_http_request_duration_seconds",
    "End-to-end API request latency.",
    ("method", "route"),
    buckets=(0.01, 0.025, 0.05, 0.1, 0.3, 0.5, 1, 2.5, 5, 15, 60),
    registry=REGISTRY,
)
QUEUE_DEPTH = Gauge(
    "vibe_job_queue_depth",
    "Durable session jobs by lifecycle status.",
    ("status",),
    registry=REGISTRY,
)
WORKERS_CONFIGURED = Gauge(
    "vibe_session_workers_configured",
    "Configured session worker concurrency.",
    registry=REGISTRY,
)
WORKERS_LIVE = Gauge(
    "vibe_session_workers_live",
    "Currently live session worker tasks.",
    registry=REGISTRY,
)
PROVIDER_REQUESTS = Counter(
    "vibe_provider_requests_total",
    "Market-data provider attempts.",
    ("provider", "status"),
    registry=REGISTRY,
)
PROVIDER_LATENCY = Histogram(
    "vibe_provider_request_duration_seconds",
    "Market-data provider latency.",
    ("provider",),
    registry=REGISTRY,
)
CACHE_EVENTS = Counter(
    "vibe_cache_events_total",
    "Cache hits and misses by cache layer.",
    ("cache", "result"),
    registry=REGISTRY,
)
LLM_TOKENS = Counter(
    "vibe_llm_tokens_total",
    "LLM tokens reported or estimated by direction.",
    ("provider", "direction"),
    registry=REGISTRY,
)
BACKTEST_DURATION = Histogram(
    "vibe_backtest_duration_seconds",
    "Completed backtest wall-clock duration.",
    ("engine", "status"),
    buckets=(0.1, 0.5, 1, 2.5, 5, 15, 30, 60, 300, 900, 3600),
    registry=REGISTRY,
)

_LOG_FIELDS = (
    "request_id",
    "session_id",
    "run_id",
    "job_id",
    "method",
    "path",
    "status",
    "elapsed_ms",
    "provider",
    "error_type",
)


class JsonFormatter(logging.Formatter):
    """Emit one secret-free, machine-readable JSON object per record."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in _LOG_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)


def configure_structured_logging() -> None:
    """Install an idempotent JSON handler for application loggers."""
    if os.getenv("VIBE_TRADING_LOG_FORMAT", "json").strip().lower() != "json":
        return
    root = logging.getLogger()
    for handler in root.handlers:
        if isinstance(handler.formatter, JsonFormatter):
            return
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    from src.security.api_security import SecretRedactionFilter

    handler.addFilter(SecretRedactionFilter())
    root.addHandler(handler)
    root.setLevel(os.getenv("VIBE_TRADING_LOG_LEVEL", "INFO").upper())


def observe_http_request(method: str, route: str, status: int, elapsed_seconds: float) -> None:
    HTTP_REQUESTS.labels(method=method, route=route, status=str(status)).inc()
    HTTP_LATENCY.labels(method=method, route=route).observe(max(0.0, elapsed_seconds))


def record_provider_request(provider: str, status: str, elapsed_seconds: float) -> None:
    PROVIDER_REQUESTS.labels(provider=provider, status=status).inc()
    PROVIDER_LATENCY.labels(provider=provider).observe(max(0.0, elapsed_seconds))


def record_cache_event(cache: str, result: str) -> None:
    CACHE_EVENTS.labels(cache=cache, result=result).inc()


def record_llm_tokens(provider: str, *, input_tokens: int = 0, output_tokens: int = 0) -> None:
    if input_tokens > 0:
        LLM_TOKENS.labels(provider=provider, direction="input").inc(input_tokens)
    if output_tokens > 0:
        LLM_TOKENS.labels(provider=provider, direction="output").inc(output_tokens)


def record_backtest_duration(engine: str, status: str, elapsed_seconds: float) -> None:
    BACKTEST_DURATION.labels(engine=engine, status=status).observe(max(0.0, elapsed_seconds))


def metrics_payload(
    *,
    queue_counts: Mapping[str, int] | None = None,
    configured_workers: int = 0,
    live_workers: int = 0,
) -> tuple[bytes, str]:
    """Refresh runtime gauges and render the process registry."""
    counts = dict(queue_counts or {})
    for status in ("pending", "running", "retry_wait", "completed", "failed", "cancelled"):
        QUEUE_DEPTH.labels(status=status).set(max(0, int(counts.get(status, 0))))
    WORKERS_CONFIGURED.set(max(0, configured_workers))
    WORKERS_LIVE.set(max(0, live_workers))
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST
