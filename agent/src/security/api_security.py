"""API key lifecycle, rate limiting, and redacted HTTP audit logging."""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.config.paths import get_runtime_root


_KEY_LOCK = threading.Lock()
_AUDIT_LOCK = threading.Lock()
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
_IDEMPOTENCY_KEY_RE = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
_QUERY_KEY_RE = re.compile(r"(?i)(api_key=)[^&\s]+")
_BEARER_RE = re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+|bearer\s+)[A-Za-z0-9._~+/-]+")


def api_key_path() -> Path:
    configured = os.getenv("VIBE_TRADING_API_KEY_PATH", "").strip()
    return Path(configured).expanduser() if configured else get_runtime_root() / "security" / "api.key"


def audit_log_path() -> Path:
    configured = os.getenv("VIBE_TRADING_AUDIT_LOG_PATH", "").strip()
    return Path(configured).expanduser() if configured else get_runtime_root() / "security" / "audit.jsonl"


def _ensure_private_parent(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        path.chmod(0o700)
    except OSError:
        pass


def _read_key(path: Path) -> str:
    value = path.read_text(encoding="utf-8").strip()
    if len(value) < 43:
        raise RuntimeError(f"API key file is empty or too short: {path}")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return value


def get_or_create_api_key() -> str:
    """Return the configured key, creating a private 256-bit local key if absent."""
    configured = os.getenv("API_AUTH_KEY", "").strip()
    if configured:
        return configured

    path = api_key_path()
    with _KEY_LOCK:
        if path.exists():
            return _read_key(path)
        _ensure_private_parent(path.parent)
        value = secrets.token_urlsafe(32)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        try:
            fd = os.open(path, flags, 0o600)
        except FileExistsError:
            return _read_key(path)
        try:
            os.write(fd, (value + "\n").encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        try:
            path.chmod(0o600)
        except OSError:
            pass
        return value


def normalize_request_id(value: str | None) -> str:
    candidate = (value or "").strip()
    return candidate if _REQUEST_ID_RE.fullmatch(candidate) else secrets.token_hex(16)


def append_audit_event(event: dict[str, Any]) -> None:
    """Append one secret-free event to the private JSONL audit ledger."""
    path = audit_log_path()
    _ensure_private_parent(path.parent)
    safe = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "request_id": str(event.get("request_id", ""))[:64],
        "method": str(event.get("method", ""))[:16],
        "path": str(event.get("path", ""))[:2048],
        "client": str(event.get("client", ""))[:128],
        "status": int(event.get("status", 0)),
        "auth": str(event.get("auth", "unknown"))[:32],
        "elapsed_ms": round(float(event.get("elapsed_ms", 0.0)), 3),
    }
    payload = (json.dumps(safe, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    with _AUDIT_LOCK:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, payload)
        finally:
            os.close(fd)


def redact_log_text(value: object) -> str:
    text = str(value)
    text = _QUERY_KEY_RE.sub(r"\1[redacted]", text)
    return _BEARER_RE.sub(r"\1[redacted]", text)


class SecretRedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # Do not flatten records with ``getMessage()``.  Uvicorn's
        # ``AccessFormatter`` reads its five structured values from
        # ``record.args`` after filters run; replacing them with an empty tuple
        # makes otherwise successful requests emit logging tracebacks.
        record.msg = redact_log_text(record.msg)
        if isinstance(record.args, dict):
            record.args = {
                key: redact_log_text(value) if isinstance(value, str) else value
                for key, value in record.args.items()
            }
        elif isinstance(record.args, tuple):
            record.args = tuple(
                redact_log_text(value) if isinstance(value, str) else value
                for value in record.args
            )
        elif isinstance(record.args, str):
            record.args = redact_log_text(record.args)
        return True


def install_secret_redaction_filters() -> None:
    """Install idempotent filters on application and Uvicorn access logs."""
    for name in ("uvicorn.access", "uvicorn.error", "api_server"):
        target = logging.getLogger(name)
        if not any(isinstance(item, SecretRedactionFilter) for item in target.filters):
            target.addFilter(SecretRedactionFilter())


class SlidingWindowRateLimiter:
    """Small in-process limiter used before expensive API route handling."""

    def __init__(self) -> None:
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, *, now: float | None = None) -> tuple[bool, int]:
        current = time.monotonic() if now is None else now
        cutoff = current - 60.0
        with self._lock:
            events = self._events[key]
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= limit:
                retry_after = max(1, int(60.0 - (current - events[0])))
                return False, retry_after
            events.append(current)
            return True, 0

    def clear(self) -> None:
        with self._lock:
            self._events.clear()


rate_limiter = SlidingWindowRateLimiter()


class IdempotencyRegistry:
    """Bounded in-memory duplicate mutation guard keyed by client token."""

    def __init__(self) -> None:
        self._claimed: dict[str, float] = {}
        self._lock = threading.Lock()

    def claim(self, key: str, *, scope: str = "", ttl_seconds: int = 600) -> bool:
        if not _IDEMPOTENCY_KEY_RE.fullmatch(key):
            raise ValueError("Invalid Idempotency-Key")
        storage_key = f"{scope}:{key}" if scope else key
        now = time.monotonic()
        with self._lock:
            expired = [item for item, created in self._claimed.items() if now - created > ttl_seconds]
            for item in expired:
                self._claimed.pop(item, None)
            if storage_key in self._claimed:
                return False
            if len(self._claimed) >= 10_000:
                oldest = min(self._claimed, key=self._claimed.get)  # type: ignore[arg-type]
                self._claimed.pop(oldest, None)
            self._claimed[storage_key] = now
            return True

    def release(self, key: str) -> None:
        with self._lock:
            self._claimed.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._claimed.clear()


idempotency_registry = IdempotencyRegistry()
