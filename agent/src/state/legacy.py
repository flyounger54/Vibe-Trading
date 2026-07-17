"""Import, validate and roll back legacy session JSON/JSONL state."""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.session.models import Attempt, Message, Session
from src.session.store import SessionStore
from src.scheduled_research.models import ScheduledResearchJob
from src.state.database import StateDatabase
from src.swarm.models import SwarmRun


@dataclass(frozen=True)
class StateSnapshot:
    sessions: tuple[dict[str, Any], ...]
    messages: tuple[dict[str, Any], ...]
    attempts: tuple[dict[str, Any], ...]

    @property
    def counts(self) -> dict[str, int]:
        return {
            "sessions": len(self.sessions),
            "messages": len(self.messages),
            "attempts": len(self.attempts),
        }

    @property
    def sha256(self) -> str:
        payload = {
            "sessions": self.sessions,
            "messages": self.messages,
            "attempts": self.attempts,
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class RecordSnapshot:
    """Canonical snapshot for legacy single-record JSON stores."""

    records: tuple[tuple[str, dict[str, Any]], ...]

    @property
    def count(self) -> int:
        return len(self.records)

    @property
    def sha256(self) -> str:
        encoded = json.dumps(
            self.records,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


def read_legacy_schedules(path: Path | None) -> RecordSnapshot:
    """Read the scheduled-research JSON envelope without mutating it."""
    if path is None or not Path(path).is_file():
        return RecordSnapshot(())
    envelope = _read_json_object(Path(path))
    jobs = envelope.get("jobs")
    if not isinstance(jobs, list) or not all(isinstance(item, dict) for item in jobs):
        raise ValueError(f"Invalid scheduled research store {path}: jobs must be a list of objects")
    records: list[tuple[str, dict[str, Any]]] = []
    for raw in jobs:
        job = ScheduledResearchJob.from_dict(raw)
        records.append((job.id, job.to_dict()))
    return RecordSnapshot(tuple(sorted(records, key=lambda item: item[0])))


def read_legacy_swarm_runs(root: Path | None) -> RecordSnapshot:
    """Read canonical run.json snapshots while leaving artifacts untouched."""
    if root is None or not Path(root).is_dir():
        return RecordSnapshot(())
    records: list[tuple[str, dict[str, Any]]] = []
    for run_file in sorted(Path(root).glob("*/run.json")):
        try:
            run = SwarmRun.model_validate_json(run_file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError(f"Invalid swarm state {run_file}: {exc}") from exc
        records.append((run.id, run.model_dump(mode="json")))
    return RecordSnapshot(tuple(sorted(records, key=lambda item: item[0])))


def read_legacy_snapshot(root: Path) -> StateSnapshot:
    """Read legacy session folders strictly; corrupted records abort migration."""

    sessions: list[dict[str, Any]] = []
    messages: list[dict[str, Any]] = []
    attempts: list[dict[str, Any]] = []
    root = Path(root)
    if not root.exists():
        return StateSnapshot((), (), ())

    for directory in sorted(path for path in root.iterdir() if path.is_dir()):
        session_file = directory / "session.json"
        if not session_file.is_file():
            continue
        session_data = _read_json_object(session_file)
        session = Session.from_dict(session_data)
        sessions.append(session.to_dict())

        messages_file = directory / "messages.jsonl"
        if messages_file.is_file():
            for line_number, raw in enumerate(
                messages_file.read_text(encoding="utf-8").splitlines(), start=1
            ):
                if not raw.strip():
                    continue
                try:
                    message = Message.from_dict(json.loads(raw))
                except (ValueError, TypeError, json.JSONDecodeError) as exc:
                    raise ValueError(
                        f"Invalid legacy message {messages_file}:{line_number}: {exc}"
                    ) from exc
                if message.session_id != session.session_id:
                    raise ValueError(
                        f"Message {message.message_id} belongs to {message.session_id}, "
                        f"expected {session.session_id}"
                    )
                messages.append(message.to_dict())

        attempts_dir = directory / "attempts"
        if attempts_dir.is_dir():
            for attempt_file in sorted(attempts_dir.glob("*/attempt.json")):
                attempt = Attempt.from_dict(_read_json_object(attempt_file))
                if attempt.session_id != session.session_id:
                    raise ValueError(
                        f"Attempt {attempt.attempt_id} belongs to {attempt.session_id}, "
                        f"expected {session.session_id}"
                    )
                attempts.append(attempt.to_dict())

    return StateSnapshot(
        tuple(sorted(sessions, key=lambda row: row["session_id"])),
        tuple(sorted(messages, key=lambda row: (row["session_id"], row["created_at"], row["message_id"]))),
        tuple(sorted(attempts, key=lambda row: (row["session_id"], row["created_at"], row["attempt_id"]))),
    )


def read_database_snapshot(database: StateDatabase, session_ids: set[str]) -> StateSnapshot:
    if not session_ids:
        return StateSnapshot((), (), ())
    placeholders = ",".join("?" for _ in session_ids)
    params = tuple(sorted(session_ids))
    with database.connect() as connection:
        session_rows = connection.execute(
            f"SELECT * FROM sessions WHERE session_id IN ({placeholders})", params
        ).fetchall()
        message_rows = connection.execute(
            f"SELECT * FROM messages WHERE session_id IN ({placeholders})", params
        ).fetchall()
        attempt_rows = connection.execute(
            f"SELECT * FROM attempts WHERE session_id IN ({placeholders})", params
        ).fetchall()

    sessions = tuple(
        sorted(
            (
                {
                    "session_id": row["session_id"],
                    "title": row["title"],
                    "status": row["status"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                    "last_attempt_id": row["last_attempt_id"],
                    "config": json.loads(row["config_json"]),
                }
                for row in session_rows
            ),
            key=lambda row: row["session_id"],
        )
    )
    messages = tuple(
        sorted(
            (
                {
                    "message_id": row["message_id"],
                    "session_id": row["session_id"],
                    "role": row["role"],
                    "content": row["content"],
                    "created_at": row["created_at"],
                    "linked_attempt_id": row["linked_attempt_id"],
                    "metadata": json.loads(row["metadata_json"]),
                }
                for row in message_rows
            ),
            key=lambda row: (row["session_id"], row["created_at"], row["message_id"]),
        )
    )
    attempts = tuple(
        sorted(
            (
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
                for row in attempt_rows
            ),
            key=lambda row: (row["session_id"], row["created_at"], row["attempt_id"]),
        )
    )
    return StateSnapshot(sessions, messages, attempts)


def migrate_legacy_sessions(
    legacy_root: Path,
    db_path: Path,
    backup_root: Path,
    *,
    schedule_path: Path | None = None,
    swarm_root: Path | None = None,
) -> dict[str, Any]:
    """Backup and import legacy state, rolling back automatically on mismatch."""

    legacy_root = Path(legacy_root)
    db_path = Path(db_path)
    backup_root = Path(backup_root)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    migration_dir = backup_root / f"state-migration-{timestamp}"
    migration_dir.mkdir(parents=True, exist_ok=False)
    legacy_backup = migration_dir / "legacy-sessions"
    if legacy_root.exists():
        shutil.copytree(legacy_root, legacy_backup)
    else:
        legacy_backup.mkdir()

    source = read_legacy_snapshot(legacy_root)
    schedule_source = read_legacy_schedules(schedule_path)
    swarm_source = read_legacy_swarm_runs(swarm_root)
    if schedule_path is not None and Path(schedule_path).is_file():
        shutil.copy2(schedule_path, migration_dir / "scheduled-research.json")
    if swarm_root is not None:
        swarm_backup = migration_dir / "swarm-runs"
        for run_file in sorted(Path(swarm_root).glob("*/run.json")):
            target = swarm_backup / run_file.parent.name / "run.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(run_file, target)
    db_existed = db_path.exists()
    db_backup = migration_dir / "state-before.db"
    if db_existed:
        StateDatabase(db_path).backup_to(db_backup)

    try:
        store = SessionStore(legacy_root, db_path=db_path)
        for raw in source.sessions:
            existing = store.get_session(raw["session_id"])
            if existing is None:
                store.create_session(Session.from_dict(raw))
            elif existing.to_dict() != raw:
                raise ValueError(f"Conflicting session already exists: {raw['session_id']}")

        with store.database.connect() as connection:
            existing_messages = {
                row["message_id"] for row in connection.execute("SELECT message_id FROM messages")
            }
            existing_attempts = {
                row["attempt_id"] for row in connection.execute("SELECT attempt_id FROM attempts")
            }
        for raw in source.messages:
            if raw["message_id"] not in existing_messages:
                store.append_message(Message.from_dict(raw))
        for raw in source.attempts:
            if raw["attempt_id"] not in existing_attempts:
                store.create_attempt(Attempt.from_dict(raw))

        for record_type, records in (
            ("schedule", schedule_source.records),
            ("swarm_run", swarm_source.records),
        ):
            for record_id, payload in records:
                existing = store.database.get_record(record_type, record_id)
                if existing is not None and existing[0] != payload:
                    raise ValueError(f"Conflicting {record_type} already exists: {record_id}")
                if existing is None:
                    store.database.upsert_record(record_type, record_id, payload)

        session_ids = {row["session_id"] for row in source.sessions}
        target = read_database_snapshot(store.database, session_ids)
        if target.counts != source.counts or target.sha256 != source.sha256:
            raise ValueError(
                "Migration verification failed: "
                f"source={source.counts}/{source.sha256}, target={target.counts}/{target.sha256}"
            )
        for record_type, record_source in (
            ("schedule", schedule_source),
            ("swarm_run", swarm_source),
        ):
            target_records = RecordSnapshot(
                tuple(
                    sorted(
                        (
                            (record_id, payload)
                            for record_id, payload, _version in store.database.list_records(record_type)
                            if any(source_id == record_id for source_id, _payload in record_source.records)
                        ),
                        key=lambda item: item[0],
                    )
                )
            )
            if target_records.count != record_source.count or target_records.sha256 != record_source.sha256:
                raise ValueError(
                    f"Migration verification failed for {record_type}: "
                    f"source={record_source.count}/{record_source.sha256}, "
                    f"target={target_records.count}/{target_records.sha256}"
                )
    except Exception:
        _restore_database(db_path, db_backup if db_existed else None)
        raise

    manifest = {
        "schema_version": store.database.schema_version(),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "legacy_root": str(legacy_root),
        "database": str(db_path),
        "database_existed": db_existed,
        "database_backup": str(db_backup) if db_existed else None,
        "legacy_backup": str(legacy_backup),
        "counts": source.counts,
        "sha256": source.sha256,
        "domains": {
            "sessions": {"counts": source.counts, "sha256": source.sha256},
            "schedules": {"count": schedule_source.count, "sha256": schedule_source.sha256},
            "swarm_runs": {"count": swarm_source.count, "sha256": swarm_source.sha256},
        },
        "verified": True,
    }
    (migration_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {**manifest, "migration_dir": str(migration_dir)}


def rollback_migration(manifest_path: Path) -> None:
    manifest = _read_json_object(Path(manifest_path))
    db_path = Path(manifest["database"])
    backup = Path(manifest["database_backup"]) if manifest.get("database_backup") else None
    _restore_database(db_path, backup)


def _restore_database(db_path: Path, backup: Path | None) -> None:
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{db_path}{suffix}")
        if candidate.exists():
            candidate.unlink()
    if backup is not None:
        shutil.copy2(backup, db_path)


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid JSON file {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return value
