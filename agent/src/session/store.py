"""SQLite-backed persistence for Session, Message, Attempt and SSE event records."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, List, Optional

from src.session.models import Attempt, Message, Session
from src.state.database import StateDatabase

logger = logging.getLogger(__name__)


class SessionStore:
    """Durable session repository backed by the unified SQLite WAL database.

    ``base_dir`` remains part of the constructor so existing API, CLI and MCP
    callers retain their public contract. Legacy JSON/JSONL folders are never
    modified; an explicit migration utility imports them into ``state.db``.
    """

    def __init__(self, base_dir: Path, *, db_path: Path | None = None) -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = Path(db_path) if db_path is not None else self.base_dir / "state.db"
        self.database = StateDatabase(self.db_path)

    # ---- Session CRUD ----

    def create_session(self, session: Session) -> Session:
        payload = session.to_dict()
        try:
            with self.database.transaction() as connection:
                connection.execute(
                    "INSERT INTO sessions(session_id, title, status, created_at, updated_at, "
                    "last_attempt_id, config_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        session.session_id,
                        session.title,
                        payload["status"],
                        session.created_at,
                        session.updated_at,
                        session.last_attempt_id,
                        _json(session.config),
                    ),
                )
        except Exception as exc:
            if "UNIQUE constraint failed" in str(exc):
                raise ValueError(f"Session {session.session_id} already exists") from exc
            raise
        return session

    def get_session(self, session_id: str) -> Optional[Session]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM sessions WHERE session_id=?", (session_id,)
            ).fetchone()
        return _session_from_row(row) if row is not None else None

    def update_session(self, session: Session, *, expected_version: int | None = None) -> int:
        payload = session.to_dict()
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT row_version FROM sessions WHERE session_id=?", (session.session_id,)
            ).fetchone()
            if row is None:
                raise ValueError(f"Session {session.session_id} not found")
            current_version = int(row["row_version"])
            if expected_version is not None and current_version != expected_version:
                from src.state.database import ConcurrentUpdateError

                raise ConcurrentUpdateError(
                    f"session/{session.session_id} version {current_version} != {expected_version}"
                )
            next_version = current_version + 1
            connection.execute(
                "UPDATE sessions SET title=?, status=?, created_at=?, updated_at=?, "
                "last_attempt_id=?, config_json=?, row_version=? WHERE session_id=?",
                (
                    session.title,
                    payload["status"],
                    session.created_at,
                    session.updated_at,
                    session.last_attempt_id,
                    _json(session.config),
                    next_version,
                    session.session_id,
                ),
            )
        return next_version

    def session_version(self, session_id: str) -> int | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT row_version FROM sessions WHERE session_id=?", (session_id,)
            ).fetchone()
        return int(row["row_version"]) if row is not None else None

    def delete_session(self, session_id: str) -> bool:
        with self.database.transaction() as connection:
            cursor = connection.execute("DELETE FROM sessions WHERE session_id=?", (session_id,))
        return cursor.rowcount > 0

    def list_sessions(self, limit: int = 50) -> List[Session]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM sessions ORDER BY updated_at DESC, session_id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [_session_from_row(row) for row in rows]

    # ---- Messages ----

    def append_message(self, message: Message) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO messages(message_id, session_id, role, content, created_at, "
                "linked_attempt_id, metadata_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    message.message_id,
                    message.session_id,
                    message.role,
                    message.content,
                    message.created_at,
                    message.linked_attempt_id,
                    _json(message.metadata),
                ),
            )

    def ensure_queued_attempt(
        self,
        message: Message,
        attempt: Attempt,
        *,
        include_shell_tools: bool,
    ) -> tuple[bool, bool]:
        """Atomically materialize records described by a durable queue job.

        ``INSERT OR IGNORE`` makes this safe both immediately after enqueue and
        during process-restart recovery. The newest attempt remains the
        Session's head, so replaying an older job cannot move it backwards.
        """
        with self.database.transaction() as connection:
            session_row = connection.execute(
                "SELECT * FROM sessions WHERE session_id=?", (message.session_id,)
            ).fetchone()
            if session_row is None:
                raise ValueError(f"Session {message.session_id} not found")
            message_cursor = connection.execute(
                "INSERT OR IGNORE INTO messages(message_id, session_id, role, content, created_at, "
                "linked_attempt_id, metadata_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    message.message_id,
                    message.session_id,
                    message.role,
                    message.content,
                    message.created_at,
                    message.linked_attempt_id,
                    _json(message.metadata),
                ),
            )
            attempt_cursor = connection.execute(
                "INSERT OR IGNORE INTO attempts(attempt_id, session_id, parent_attempt_id, status, "
                "prompt, run_dir, summary, react_trace_json, created_at, completed_at, error, "
                "metrics_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                _attempt_values(attempt),
            )
            config = json.loads(session_row["config_json"])
            config["include_shell_tools"] = include_shell_tools
            current_head = session_row["last_attempt_id"]
            current_created = None
            if current_head:
                head_row = connection.execute(
                    "SELECT created_at FROM attempts WHERE attempt_id=?", (current_head,)
                ).fetchone()
                current_created = head_row["created_at"] if head_row is not None else None
            next_head = (
                attempt.attempt_id
                if current_created is None or attempt.created_at >= current_created
                else current_head
            )
            connection.execute(
                "UPDATE sessions SET last_attempt_id=?, config_json=?, updated_at=?, "
                "row_version=row_version+1 WHERE session_id=?",
                (next_head, _json(config), max(session_row["updated_at"], attempt.created_at), message.session_id),
            )
        return message_cursor.rowcount == 1, attempt_cursor.rowcount == 1

    def get_messages(self, session_id: str, limit: int = 100) -> List[Message]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM (SELECT * FROM messages WHERE session_id=? "
                "ORDER BY sequence DESC LIMIT ?) ORDER BY sequence ASC",
                (session_id, limit),
            ).fetchall()
        return [
            Message(
                message_id=row["message_id"],
                session_id=row["session_id"],
                role=row["role"],
                content=row["content"],
                created_at=row["created_at"],
                linked_attempt_id=row["linked_attempt_id"],
                metadata=json.loads(row["metadata_json"]),
            )
            for row in rows
        ]

    # ---- Attempts ----

    def create_attempt(self, attempt: Attempt) -> Attempt:
        values = _attempt_values(attempt)
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO attempts(attempt_id, session_id, parent_attempt_id, status, prompt, "
                "run_dir, summary, react_trace_json, created_at, completed_at, error, metrics_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                values,
            )
        return attempt

    def get_attempt(self, session_id: str, attempt_id: str) -> Optional[Attempt]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM attempts WHERE session_id=? AND attempt_id=?",
                (session_id, attempt_id),
            ).fetchone()
        return _attempt_from_row(row) if row is not None else None

    def update_attempt(self, attempt: Attempt) -> None:
        values = _attempt_values(attempt)
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "UPDATE attempts SET session_id=?, parent_attempt_id=?, status=?, prompt=?, "
                "run_dir=?, summary=?, react_trace_json=?, created_at=?, completed_at=?, error=?, "
                "metrics_json=?, row_version=row_version+1 WHERE attempt_id=?",
                values[1:] + (values[0],),
            )
        if cursor.rowcount == 0:
            raise ValueError(f"Attempt {attempt.attempt_id} not found")

    def finalize_attempt(self, attempt: Attempt, reply: Message | None = None) -> None:
        """Atomically persist a terminal attempt and its deterministic reply."""
        values = _attempt_values(attempt)
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "UPDATE attempts SET session_id=?, parent_attempt_id=?, status=?, prompt=?, "
                "run_dir=?, summary=?, react_trace_json=?, created_at=?, completed_at=?, error=?, "
                "metrics_json=?, row_version=row_version+1 WHERE attempt_id=?",
                values[1:] + (values[0],),
            )
            if cursor.rowcount == 0:
                raise ValueError(f"Attempt {attempt.attempt_id} not found")
            if reply is not None:
                connection.execute(
                    "INSERT OR IGNORE INTO messages(message_id, session_id, role, content, created_at, "
                    "linked_attempt_id, metadata_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        reply.message_id,
                        reply.session_id,
                        reply.role,
                        reply.content,
                        reply.created_at,
                        reply.linked_attempt_id,
                        _json(reply.metadata),
                    ),
                )

    # ---- Persistent event cursor ----

    def append_event(self, event: Any) -> int:
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO events(event_id, session_id, event_type, data_json, timestamp) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    event.event_id,
                    event.session_id,
                    event.event_type,
                    _json(event.data),
                    event.timestamp,
                ),
            )
            if cursor.rowcount == 0:
                row = connection.execute(
                    "SELECT cursor FROM events WHERE event_id=?", (event.event_id,)
                ).fetchone()
                return int(row["cursor"])
            return int(cursor.lastrowid)

    def get_events(
        self,
        session_id: str,
        *,
        after_event_id: str | None = None,
        limit: int = 500,
        unknown_as_start: bool = False,
    ) -> list[dict[str, Any]]:
        after_cursor = 0
        with self.database.connect() as connection:
            if after_event_id:
                row = connection.execute(
                    "SELECT cursor FROM events WHERE session_id=? AND event_id=?",
                    (session_id, after_event_id),
                ).fetchone()
                if row is None and not unknown_as_start:
                    return []
                if row is not None:
                    after_cursor = int(row["cursor"])
            rows = connection.execute(
                "SELECT * FROM events WHERE session_id=? AND cursor>? "
                "ORDER BY cursor ASC LIMIT ?",
                (session_id, after_cursor, limit),
            ).fetchall()
        return [
            {
                "cursor": int(row["cursor"]),
                "event_id": row["event_id"],
                "session_id": row["session_id"],
                "event_type": row["event_type"],
                "data": json.loads(row["data_json"]),
                "timestamp": float(row["timestamp"]),
            }
            for row in rows
        ]

    def clear_events(self, session_id: str) -> None:
        with self.database.transaction() as connection:
            connection.execute("DELETE FROM events WHERE session_id=?", (session_id,))


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _session_from_row(row: Any) -> Session:
    return Session.from_dict(
        {
            "session_id": row["session_id"],
            "title": row["title"],
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "last_attempt_id": row["last_attempt_id"],
            "config": json.loads(row["config_json"]),
        }
    )


def _attempt_values(attempt: Attempt) -> tuple[Any, ...]:
    payload = attempt.to_dict()
    return (
        attempt.attempt_id,
        attempt.session_id,
        attempt.parent_attempt_id,
        payload["status"],
        attempt.prompt,
        attempt.run_dir,
        attempt.summary,
        _json(attempt.react_trace),
        attempt.created_at,
        attempt.completed_at,
        attempt.error,
        _json(attempt.metrics) if attempt.metrics is not None else None,
    )


def _attempt_from_row(row: Any) -> Attempt:
    return Attempt.from_dict(
        {
            "attempt_id": row["attempt_id"],
            "session_id": row["session_id"],
            "parent_attempt_id": row["parent_attempt_id"],
            "status": row["status"],
            "prompt": row["prompt"],
            "run_dir": row["run_dir"],
            "summary": row["summary"],
            "react_trace": json.loads(row["react_trace_json"]),
            "created_at": row["created_at"],
            "completed_at": row["completed_at"],
            "error": row["error"],
            "metrics": json.loads(row["metrics_json"]) if row["metrics_json"] else None,
        }
    )
