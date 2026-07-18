"""SQLite WAL state database with explicit, forward-only schema migrations."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from src.config.paths import get_runtime_root


def default_state_db_path() -> Path:
    """Return the single production database used by durable local state."""
    configured = os.getenv("VIBE_TRADING_STATE_DB_PATH", "").strip()
    if configured:
        return Path(configured).expanduser()
    return get_runtime_root() / "state" / "vibe.db"


_SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_attempt_id TEXT,
    config_json TEXT NOT NULL,
    row_version INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_sessions_updated_at ON sessions(updated_at DESC);

CREATE TABLE IF NOT EXISTS messages (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT NOT NULL UNIQUE,
    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    linked_attempt_id TEXT,
    metadata_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_session_sequence
    ON messages(session_id, sequence);

CREATE TABLE IF NOT EXISTS attempts (
    attempt_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    parent_attempt_id TEXT,
    status TEXT NOT NULL,
    prompt TEXT NOT NULL,
    run_dir TEXT,
    summary TEXT,
    react_trace_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    completed_at TEXT,
    error TEXT,
    metrics_json TEXT,
    row_version INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_attempts_session_created
    ON attempts(session_id, created_at);

CREATE TABLE IF NOT EXISTS events (
    cursor INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    session_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    data_json TEXT NOT NULL,
    timestamp REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_session_cursor ON events(session_id, cursor);

CREATE TABLE IF NOT EXISTS state_records (
    record_type TEXT NOT NULL,
    record_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    row_version INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY(record_type, record_id)
);
CREATE INDEX IF NOT EXISTS idx_state_records_type_updated
    ON state_records(record_type, updated_at DESC);
"""

_SCHEMA_V2 = """
CREATE TABLE IF NOT EXISTS runtime_jobs (
    job_id TEXT PRIMARY KEY,
    queue_name TEXT NOT NULL,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    result_json TEXT,
    idempotency_key TEXT,
    concurrency_key TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    available_at REAL NOT NULL,
    lease_owner TEXT,
    lease_expires_at REAL,
    heartbeat_at REAL,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    error_type TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE(queue_name, idempotency_key)
);
CREATE INDEX IF NOT EXISTS idx_runtime_jobs_claim
    ON runtime_jobs(queue_name, status, available_at, created_at);
CREATE INDEX IF NOT EXISTS idx_runtime_jobs_concurrency
    ON runtime_jobs(queue_name, concurrency_key, status, lease_expires_at);
"""

_MIGRATIONS: tuple[tuple[int, str], ...] = (
    (1, _SCHEMA_V1),
    (2, _SCHEMA_V2),
)
# Every durable product surface must use this shared state database.  Keep the
# allow-list explicit so a typo cannot silently create an ungoverned namespace.
_RECORD_TYPES = {
    "job",
    "swarm_run",
    "schedule",
    "industry_chain",
    "industry_chain_history",
    "industry_chain_schedule",
}


class ConcurrentUpdateError(RuntimeError):
    """Raised when optimistic concurrency detects a stale writer."""


class StateDatabase:
    """Small SQLite wrapper shared by sessions, jobs, events, swarms and schedules."""

    expected_schema_version = max(version for version, _ in _MIGRATIONS)

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._migration_lock = threading.Lock()
        self._initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=30.0,
            isolation_level=None,
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    @contextmanager
    def transaction(self, *, immediate: bool = True) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._migration_lock:
            with self.transaction() as connection:
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS schema_migrations "
                    "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
                )
                applied = {
                    int(row["version"])
                    for row in connection.execute("SELECT version FROM schema_migrations")
                }
                for version, sql in _MIGRATIONS:
                    if version in applied:
                        continue
                    connection.executescript(sql)
                    connection.execute(
                        "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                        (version, datetime.now(timezone.utc).isoformat()),
                    )

    def schema_version(self) -> int:
        with self.connect() as connection:
            row = connection.execute("SELECT MAX(version) AS version FROM schema_migrations").fetchone()
        return int(row["version"] or 0)

    def journal_mode(self) -> str:
        with self.connect() as connection:
            row = connection.execute("PRAGMA journal_mode").fetchone()
        return str(row[0]).lower()

    def integrity_check(self) -> str:
        """Return SQLite's quick integrity verdict for readiness and drills."""
        with self.connect() as connection:
            row = connection.execute("PRAGMA quick_check").fetchone()
        return str(row[0]).lower()

    def upsert_record(
        self,
        record_type: str,
        record_id: str,
        payload: dict[str, Any],
        *,
        expected_version: int | None = None,
    ) -> int:
        """Persist job/swarm/schedule state with optional optimistic locking."""

        if record_type not in _RECORD_TYPES:
            raise ValueError(f"Unsupported state record type: {record_type}")
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        updated_at = datetime.now(timezone.utc).isoformat()
        with self.transaction() as connection:
            current = connection.execute(
                "SELECT row_version FROM state_records WHERE record_type=? AND record_id=?",
                (record_type, record_id),
            ).fetchone()
            if current is None:
                if expected_version not in {None, 0}:
                    raise ConcurrentUpdateError(
                        f"{record_type}/{record_id} does not exist at version {expected_version}"
                    )
                version = 1
                connection.execute(
                    "INSERT INTO state_records(record_type, record_id, payload_json, updated_at, row_version) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (record_type, record_id, encoded, updated_at, version),
                )
                return version

            current_version = int(current["row_version"])
            if expected_version is not None and current_version != expected_version:
                raise ConcurrentUpdateError(
                    f"{record_type}/{record_id} version {current_version} != {expected_version}"
                )
            version = current_version + 1
            connection.execute(
                "UPDATE state_records SET payload_json=?, updated_at=?, row_version=? "
                "WHERE record_type=? AND record_id=?",
                (encoded, updated_at, version, record_type, record_id),
            )
            return version

    def get_record(self, record_type: str, record_id: str) -> tuple[dict[str, Any], int] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload_json, row_version FROM state_records "
                "WHERE record_type=? AND record_id=?",
                (record_type, record_id),
            ).fetchone()
        if row is None:
            return None
        return json.loads(row["payload_json"]), int(row["row_version"])

    def list_records(self, record_type: str, *, limit: int = 1000) -> list[tuple[str, dict[str, Any], int]]:
        if record_type not in _RECORD_TYPES:
            raise ValueError(f"Unsupported state record type: {record_type}")
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT record_id, payload_json, row_version FROM state_records "
                "WHERE record_type=? ORDER BY updated_at DESC, record_id DESC LIMIT ?",
                (record_type, limit),
            ).fetchall()
        return [
            (row["record_id"], json.loads(row["payload_json"]), int(row["row_version"]))
            for row in rows
        ]

    def delete_record(self, record_type: str, record_id: str) -> bool:
        if record_type not in _RECORD_TYPES:
            raise ValueError(f"Unsupported state record type: {record_type}")
        with self.transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM state_records WHERE record_type=? AND record_id=?",
                (record_type, record_id),
            )
        return cursor.rowcount > 0

    def replace_records(self, record_type: str, records: dict[str, dict[str, Any]]) -> None:
        """Atomically replace all records of one type."""

        if record_type not in _RECORD_TYPES:
            raise ValueError(f"Unsupported state record type: {record_type}")
        updated_at = datetime.now(timezone.utc).isoformat()
        with self.transaction() as connection:
            connection.execute("DELETE FROM state_records WHERE record_type=?", (record_type,))
            connection.executemany(
                "INSERT INTO state_records(record_type, record_id, payload_json, updated_at, row_version) "
                "VALUES (?, ?, ?, ?, 1)",
                [
                    (
                        record_type,
                        record_id,
                        json.dumps(payload, ensure_ascii=False, sort_keys=True),
                        updated_at,
                    )
                    for record_id, payload in records.items()
                ],
            )

    def backup_to(self, target: Path) -> Path:
        """Create a transactionally consistent SQLite backup."""

        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as source, sqlite3.connect(target) as destination:
            source.backup(destination)
        return target
