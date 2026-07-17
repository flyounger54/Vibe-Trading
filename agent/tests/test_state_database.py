"""Node 2 unified SQLite state, concurrency, migration and rollback contracts."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from src.session.events import EventBus
from src.session.models import Attempt, Message, Session
from src.session.store import SessionStore
from src.scheduled_research.models import ScheduledResearchJob
from src.scheduled_research.store import ScheduledResearchJobStore
from src.state.database import ConcurrentUpdateError, StateDatabase
from src.state.legacy import (
    migrate_legacy_sessions,
    read_database_snapshot,
    read_legacy_snapshot,
    rollback_migration,
)
from src.swarm.models import SwarmRun
from src.swarm.store import SwarmStore


def test_database_enables_wal_and_applies_schema_once(tmp_path: Path) -> None:
    database = StateDatabase(tmp_path / "state.db")
    second = StateDatabase(tmp_path / "state.db")

    assert database.journal_mode() == "wal"
    assert database.schema_version() == 1
    assert second.schema_version() == 1


@pytest.mark.parametrize("record_type", ["job", "swarm_run", "schedule"])
def test_generic_state_records_use_optimistic_concurrency(
    tmp_path: Path, record_type: str
) -> None:
    database = StateDatabase(tmp_path / "state.db")

    version = database.upsert_record(record_type, "record-1", {"status": "pending"})
    next_version = database.upsert_record(
        record_type,
        "record-1",
        {"status": "running"},
        expected_version=version,
    )

    assert next_version == 2
    assert database.get_record(record_type, "record-1") == ({"status": "running"}, 2)
    with pytest.raises(ConcurrentUpdateError):
        database.upsert_record(
            record_type,
            "record-1",
            {"status": "lost-update"},
            expected_version=version,
        )


def test_concurrent_create_allows_one_winner_without_overwrite(tmp_path: Path) -> None:
    database = StateDatabase(tmp_path / "state.db")
    barrier = Barrier(2)

    def create(status: str) -> str:
        barrier.wait()
        try:
            database.upsert_record(
                "job", "job-1", {"status": status}, expected_version=0
            )
            return "created"
        except ConcurrentUpdateError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(create, ("pending-a", "pending-b")))

    assert sorted(results) == ["conflict", "created"]
    assert database.get_record("job", "job-1")[1] == 1


def test_concurrent_update_and_cancel_allow_one_winner(tmp_path: Path) -> None:
    database = StateDatabase(tmp_path / "state.db")
    version = database.upsert_record("job", "job-1", {"status": "running"})
    barrier = Barrier(2)

    def mutate(status: str) -> str:
        barrier.wait()
        try:
            database.upsert_record(
                "job", "job-1", {"status": status}, expected_version=version
            )
            return status
        except ConcurrentUpdateError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(mutate, ("completed", "cancelled")))

    assert results.count("conflict") == 1
    payload, final_version = database.get_record("job", "job-1")
    assert payload["status"] in {"completed", "cancelled"}
    assert final_version == 2


def test_concurrent_message_appends_have_no_lost_writes(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions")
    store.create_session(Session(session_id="session-1"))

    def append(index: int) -> None:
        store.append_message(
            Message(
                message_id=f"message-{index:03d}",
                session_id="session-1",
                content=str(index),
            )
        )

    with ThreadPoolExecutor(max_workers=12) as executor:
        list(executor.map(append, range(100)))

    messages = store.get_messages("session-1", limit=200)
    assert len(messages) == 100
    assert {message.message_id for message in messages} == {
        f"message-{index:03d}" for index in range(100)
    }


def test_session_update_rejects_stale_version(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions")
    session = store.create_session(Session(session_id="session-1", title="old"))
    initial_version = store.session_version(session.session_id)
    session.title = "first"
    store.update_session(session, expected_version=initial_version)
    session.title = "stale"

    with pytest.raises(ConcurrentUpdateError):
        store.update_session(session, expected_version=initial_version)

    assert store.get_session(session.session_id).title == "first"


def test_event_cursor_survives_event_bus_restart(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions")
    store.create_session(Session(session_id="session-1"))
    first_bus = EventBus(event_store=store)
    first = first_bus.emit("session-1", "attempt.started", {"step": 1})
    second = first_bus.emit("session-1", "attempt.completed", {"step": 2})

    restarted_bus = EventBus(event_store=SessionStore(tmp_path / "sessions"))

    assert restarted_bus.replay("session-1", first.event_id) == [second]
    assert restarted_bus.replay("session-1", replay_all=True) == [first, second]


def test_legacy_migration_preserves_counts_hash_and_source_files(tmp_path: Path) -> None:
    legacy = tmp_path / "legacy"
    session_dir = legacy / "session-1"
    attempt_dir = session_dir / "attempts" / "attempt-1"
    attempt_dir.mkdir(parents=True)
    session = Session(session_id="session-1", title="legacy")
    message = Message(message_id="message-1", session_id="session-1", content="hello")
    attempt = Attempt(attempt_id="attempt-1", session_id="session-1", prompt="research")
    (session_dir / "session.json").write_text(
        json.dumps(session.to_dict(), ensure_ascii=False), encoding="utf-8"
    )
    (session_dir / "messages.jsonl").write_text(
        json.dumps(message.to_dict(), ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (attempt_dir / "attempt.json").write_text(
        json.dumps(attempt.to_dict(), ensure_ascii=False), encoding="utf-8"
    )
    source = read_legacy_snapshot(legacy)
    original_session_bytes = (session_dir / "session.json").read_bytes()

    result = migrate_legacy_sessions(
        legacy,
        tmp_path / "state.db",
        tmp_path / "backups",
    )
    database = StateDatabase(tmp_path / "state.db")
    target = read_database_snapshot(database, {"session-1"})

    assert result["verified"] is True
    assert result["counts"] == {"sessions": 1, "messages": 1, "attempts": 1}
    assert target.sha256 == source.sha256 == result["sha256"]
    assert (session_dir / "session.json").read_bytes() == original_session_bytes
    assert Path(result["legacy_backup"]).is_dir()


def test_migration_rollback_restores_previous_database(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    database = StateDatabase(db_path)
    database.upsert_record("job", "existing", {"status": "safe"})
    legacy = tmp_path / "legacy"
    session_dir = legacy / "session-1"
    session_dir.mkdir(parents=True)
    (session_dir / "session.json").write_text(
        json.dumps(Session(session_id="session-1").to_dict()), encoding="utf-8"
    )

    result = migrate_legacy_sessions(legacy, db_path, tmp_path / "backups")
    manifest = Path(result["migration_dir"]) / "manifest.json"
    rollback_migration(manifest)
    restored = StateDatabase(db_path)

    assert restored.get_record("job", "existing") == ({"status": "safe"}, 1)
    with restored.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


def test_migration_covers_schedule_and_swarm_state(tmp_path: Path) -> None:
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    schedule_path = tmp_path / "scheduled.json"
    job = ScheduledResearchJob(id="schedule-1", prompt="research", schedule="60000")
    schedule_path.write_text(
        json.dumps({"schema_version": 1, "jobs": [job.to_dict()]}),
        encoding="utf-8",
    )
    swarm_root = tmp_path / "swarm"
    run_dir = swarm_root / "run-1"
    run_dir.mkdir(parents=True)
    run = SwarmRun(
        id="run-1",
        preset_name="test",
        user_vars={},
        created_at="2026-07-17T00:00:00+00:00",
    )
    (run_dir / "run.json").write_text(run.model_dump_json(indent=2), encoding="utf-8")

    result = migrate_legacy_sessions(
        legacy,
        tmp_path / "state.db",
        tmp_path / "backups",
        schedule_path=schedule_path,
        swarm_root=swarm_root,
    )
    database = StateDatabase(tmp_path / "state.db")

    assert result["verified"] is True
    assert result["domains"]["schedules"]["count"] == 1
    assert result["domains"]["swarm_runs"]["count"] == 1
    assert database.get_record("schedule", job.id)[0] == job.to_dict()
    assert database.get_record("swarm_run", run.id)[0] == run.model_dump(mode="json")
    migration_dir = Path(result["migration_dir"])
    assert (migration_dir / "scheduled-research.json").is_file()
    assert (migration_dir / "swarm-runs" / run.id / "run.json").is_file()


def test_migration_hash_is_stable_for_multiple_swarm_records(tmp_path: Path) -> None:
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    swarm_root = tmp_path / "swarm"
    for run_id in ("run-z", "run-a"):
        run_dir = swarm_root / run_id
        run_dir.mkdir(parents=True)
        run = SwarmRun(
            id=run_id,
            preset_name="test",
            user_vars={},
            created_at="2026-07-17T00:00:00+00:00",
        )
        (run_dir / "run.json").write_text(run.model_dump_json(indent=2), encoding="utf-8")

    result = migrate_legacy_sessions(
        legacy,
        tmp_path / "state.db",
        tmp_path / "backups",
        swarm_root=swarm_root,
    )

    assert result["verified"] is True
    assert result["domains"]["swarm_runs"]["count"] == 2


def test_corrupted_legacy_json_aborts_without_creating_database(tmp_path: Path) -> None:
    legacy = tmp_path / "legacy"
    session_dir = legacy / "broken"
    session_dir.mkdir(parents=True)
    (session_dir / "session.json").write_text("{broken", encoding="utf-8")
    db_path = tmp_path / "state.db"

    with pytest.raises(ValueError, match="Invalid JSON"):
        migrate_legacy_sessions(legacy, db_path, tmp_path / "backups")

    assert not db_path.exists()


def test_default_scheduled_store_uses_unified_database(tmp_path: Path, monkeypatch) -> None:
    database_path = tmp_path / "state" / "vibe.db"
    legacy_path = tmp_path / "scheduled" / "jobs.json"
    monkeypatch.setattr("src.scheduled_research.store._default_database_path", lambda: database_path)
    monkeypatch.setattr("src.scheduled_research.store._default_store_path", lambda: legacy_path)
    store = ScheduledResearchJobStore()
    job = ScheduledResearchJob(id="schedule-1", prompt="research", schedule="60000")

    store.upsert(job)
    restarted = ScheduledResearchJobStore()

    assert restarted.get(job.id).to_dict() == job.to_dict()
    assert StateDatabase(database_path).get_record("schedule", job.id)[0] == job.to_dict()
    assert not legacy_path.exists()


def test_swarm_store_dual_writes_state_and_recovers_missing_snapshot(tmp_path: Path) -> None:
    store = SwarmStore(tmp_path / "swarm")
    run = SwarmRun(
        id="run-1",
        preset_name="test",
        user_vars={},
        created_at="2026-07-17T00:00:00+00:00",
    )
    run_dir = store.create_run(run)

    (run_dir / "run.json").unlink()
    recovered = SwarmStore(tmp_path / "swarm").load_run(run.id)

    assert recovered == run
    assert store.database.get_record("swarm_run", run.id)[0]["preset_name"] == "test"
