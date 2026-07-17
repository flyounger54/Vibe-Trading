"""Node 6 contracts for durable jobs, leases, cancellation and event replay."""

from __future__ import annotations

from pathlib import Path

from src.session.events import EventBus
from src.session.models import Session
from src.session.store import SessionStore
from src.state.database import StateDatabase
from src.state.jobs import JobStatus, SQLiteJobQueue


class _Clock:
    def __init__(self, value: float = 1000.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def _queue(tmp_path: Path, clock: _Clock) -> SQLiteJobQueue:
    return SQLiteJobQueue(StateDatabase(tmp_path / "state.db"), now_fn=clock)


def test_enqueue_is_idempotent_and_preserves_original_payload(tmp_path: Path) -> None:
    clock = _Clock()
    queue = _queue(tmp_path, clock)

    first = queue.enqueue(
        "session_attempts", {"attempt_id": "a1"}, idempotency_key="request-1",
    )
    duplicate = queue.enqueue(
        "session_attempts", {"attempt_id": "SHOULD_NOT_REPLACE"},
        idempotency_key="request-1",
    )

    assert duplicate.job_id == first.job_id
    assert duplicate.payload == {"attempt_id": "a1"}
    assert len(queue.list("session_attempts")) == 1


def test_claim_serializes_same_session_but_allows_other_sessions(tmp_path: Path) -> None:
    clock = _Clock()
    queue = _queue(tmp_path, clock)
    first = queue.enqueue("session_attempts", {"n": 1}, concurrency_key="session-a")
    second = queue.enqueue("session_attempts", {"n": 2}, concurrency_key="session-a")
    other = queue.enqueue("session_attempts", {"n": 3}, concurrency_key="session-b")

    claim_a = queue.claim("session_attempts", "worker-1", lease_seconds=30)
    claim_b = queue.claim("session_attempts", "worker-2", lease_seconds=30)

    assert claim_a.job_id == first.job_id
    assert claim_b.job_id == other.job_id
    assert queue.get(second.job_id).status == JobStatus.PENDING


def test_heartbeat_extends_lease_and_restart_recovers_expired_job(tmp_path: Path) -> None:
    clock = _Clock()
    database = StateDatabase(tmp_path / "state.db")
    queue = SQLiteJobQueue(database, now_fn=clock)
    job = queue.enqueue("session_attempts", {"attempt_id": "a1"}, max_attempts=2)
    claimed = queue.claim("session_attempts", "worker-1", lease_seconds=10)
    assert claimed.job_id == job.job_id

    clock.advance(8)
    assert queue.heartbeat(job.job_id, "worker-1", lease_seconds=10) is True
    clock.advance(8)
    restarted = SQLiteJobQueue(StateDatabase(tmp_path / "state.db"), now_fn=clock)
    assert restarted.recover_expired("session_attempts") == []

    clock.advance(3)
    assert restarted.recover_expired("session_attempts") == [job.job_id]
    reclaimed = restarted.claim("session_attempts", "worker-2", lease_seconds=10)
    assert reclaimed.job_id == job.job_id
    assert reclaimed.attempts == 2


def test_cancelled_worker_cannot_commit_result_or_side_effect(tmp_path: Path) -> None:
    clock = _Clock()
    queue = _queue(tmp_path, clock)
    job = queue.enqueue("session_attempts", {"attempt_id": "a1"})
    queue.claim("session_attempts", "worker-1", lease_seconds=30)

    assert queue.cancel(job.job_id, reason="user_cancelled") is True
    assert queue.is_cancelled(job.job_id) is True
    assert queue.complete(job.job_id, "worker-1", {"side_effect": "placed_order"}) is False
    final = queue.get(job.job_id)
    assert final.status == JobStatus.CANCELLED
    assert final.result is None


def test_failure_retries_then_becomes_explicit_terminal_failure(tmp_path: Path) -> None:
    clock = _Clock()
    queue = _queue(tmp_path, clock)
    job = queue.enqueue("session_attempts", {}, max_attempts=2)

    queue.claim("session_attempts", "worker-1", lease_seconds=30)
    assert queue.fail(
        job.job_id, "worker-1", "tool timeout", error_type="tool_timeout",
        retry_delay=5,
    ) == JobStatus.RETRY_WAIT
    assert queue.claim("session_attempts", "worker-2", lease_seconds=30) is None
    clock.advance(5)
    queue.claim("session_attempts", "worker-2", lease_seconds=30)
    assert queue.fail(
        job.job_id, "worker-2", "LLM timeout", error_type="llm_timeout",
    ) == JobStatus.FAILED
    final = queue.get(job.job_id)
    assert final.error_type == "llm_timeout"
    assert final.status == JobStatus.FAILED


def test_persistent_event_replay_handles_ten_thousand_without_loss(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions")
    store.create_session(Session(session_id="session-1"))
    bus = EventBus(max_buffer_size=50, event_store=store)
    first_id = None
    for index in range(10_000):
        event = bus.emit("session-1", "trace", {"index": index})
        if index == 99:
            first_id = event.event_id

    replayed = EventBus(max_buffer_size=50, event_store=store).replay(
        "session-1", first_id,
    )
    assert len(replayed) == 9_900
    assert [event.data["index"] for event in replayed] == list(range(100, 10_000))
