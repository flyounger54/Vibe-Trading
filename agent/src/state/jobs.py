"""Durable job queue with leases, retries and cancellation write fences.

SQLite is the local implementation.  The :class:`JobQueue` protocol keeps the
runtime independent from the storage backend so a server deployment can later
use Postgres or a managed queue without changing Session or Swarm semantics.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Callable, List, Protocol

from src.state.database import StateDatabase


class JobStatus(StrEnum):
    """Persistent job lifecycle states."""

    PENDING = "pending"
    RUNNING = "running"
    RETRY_WAIT = "retry_wait"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class JobRecord:
    """Storage-neutral snapshot of one queued job."""

    job_id: str
    queue_name: str
    status: JobStatus
    payload: dict[str, Any]
    result: dict[str, Any] | None
    idempotency_key: str | None
    concurrency_key: str | None
    attempts: int
    max_attempts: int
    available_at: float
    lease_owner: str | None
    lease_expires_at: float | None
    heartbeat_at: float | None
    cancel_requested: bool
    error: str | None
    error_type: str | None
    created_at: float
    updated_at: float


class JobQueue(Protocol):
    """Backend contract consumed by durable runtimes."""

    def enqueue(
        self,
        queue_name: str,
        payload: dict[str, Any],
        *,
        idempotency_key: str | None = None,
        concurrency_key: str | None = None,
        max_attempts: int = 3,
        available_at: float | None = None,
    ) -> JobRecord: ...

    def claim(
        self, queue_name: str, worker_id: str, *, lease_seconds: float
    ) -> JobRecord | None: ...

    def heartbeat(
        self, job_id: str, worker_id: str, *, lease_seconds: float
    ) -> bool: ...

    def complete(
        self, job_id: str, worker_id: str, result: dict[str, Any] | None = None
    ) -> bool: ...

    def cancel(self, job_id: str, *, reason: str = "cancelled") -> bool: ...


class SQLiteJobQueue:
    """Transactional SQLite implementation of :class:`JobQueue`.

    ``BEGIN IMMEDIATE`` serializes claim decisions.  A non-empty
    ``concurrency_key`` acts as a durable mutex, which is used to guarantee
    sequential attempts within one Session while allowing other Sessions to
    progress concurrently.
    """

    def __init__(
        self,
        database: StateDatabase,
        *,
        now_fn: Callable[[], float] = time.time,
    ) -> None:
        self.database = database
        self._now = now_fn

    def enqueue(
        self,
        queue_name: str,
        payload: dict[str, Any],
        *,
        idempotency_key: str | None = None,
        concurrency_key: str | None = None,
        max_attempts: int = 3,
        available_at: float | None = None,
    ) -> JobRecord:
        queue_name = _required(queue_name, "queue_name")
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        now = self._now()
        ready_at = now if available_at is None else float(available_at)
        encoded = _encode(payload)
        with self.database.transaction() as connection:
            if idempotency_key:
                existing = connection.execute(
                    "SELECT * FROM runtime_jobs WHERE queue_name=? AND idempotency_key=?",
                    (queue_name, idempotency_key),
                ).fetchone()
                if existing is not None:
                    return _from_row(existing)

            job_id = uuid.uuid4().hex
            try:
                connection.execute(
                    "INSERT INTO runtime_jobs("
                    "job_id, queue_name, status, payload_json, idempotency_key, "
                    "concurrency_key, max_attempts, available_at, created_at, updated_at"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        job_id,
                        queue_name,
                        JobStatus.PENDING.value,
                        encoded,
                        idempotency_key,
                        concurrency_key or None,
                        max_attempts,
                        ready_at,
                        now,
                        now,
                    ),
                )
            except Exception as exc:
                # Another process may win the unique idempotency race between
                # our read and insert. Return that original job unchanged.
                if not idempotency_key or "UNIQUE constraint failed" not in str(exc):
                    raise
                existing = connection.execute(
                    "SELECT * FROM runtime_jobs WHERE queue_name=? AND idempotency_key=?",
                    (queue_name, idempotency_key),
                ).fetchone()
                if existing is None:
                    raise
                return _from_row(existing)
            row = connection.execute(
                "SELECT * FROM runtime_jobs WHERE job_id=?", (job_id,)
            ).fetchone()
        return _from_row(row)

    def get(self, job_id: str) -> JobRecord | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM runtime_jobs WHERE job_id=?", (job_id,)
            ).fetchone()
        return _from_row(row) if row is not None else None

    def get_by_idempotency(
        self, queue_name: str, idempotency_key: str
    ) -> JobRecord | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM runtime_jobs WHERE queue_name=? AND idempotency_key=?",
                (queue_name, idempotency_key),
            ).fetchone()
        return _from_row(row) if row is not None else None

    def list(self, queue_name: str, *, limit: int = 1000) -> list[JobRecord]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM runtime_jobs WHERE queue_name=? "
                "ORDER BY rowid ASC LIMIT ?",
                (queue_name, limit),
            ).fetchall()
        return [_from_row(row) for row in rows]

    def status_counts(self, queue_name: str) -> dict[str, int]:
        """Return durable job counts grouped by lifecycle status."""
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT status, COUNT(*) AS count FROM runtime_jobs "
                "WHERE queue_name=? GROUP BY status",
                (queue_name,),
            ).fetchall()
        return {str(row["status"]): int(row["count"]) for row in rows}

    def claim(
        self, queue_name: str, worker_id: str, *, lease_seconds: float
    ) -> JobRecord | None:
        _required(worker_id, "worker_id")
        _positive(lease_seconds, "lease_seconds")
        now = self._now()
        with self.database.transaction() as connection:
            self._recover_expired_in_transaction(connection, now, queue_name)
            connection.execute(
                "UPDATE runtime_jobs SET status=?, updated_at=? "
                "WHERE queue_name=? AND status=? AND available_at<=?",
                (
                    JobStatus.PENDING.value,
                    now,
                    queue_name,
                    JobStatus.RETRY_WAIT.value,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT candidate.* FROM runtime_jobs AS candidate "
                "WHERE candidate.queue_name=? AND candidate.status=? "
                "AND candidate.available_at<=? AND candidate.cancel_requested=0 "
                "AND (candidate.concurrency_key IS NULL OR NOT EXISTS ("
                "  SELECT 1 FROM runtime_jobs AS active "
                "  WHERE active.queue_name=candidate.queue_name "
                "  AND active.concurrency_key=candidate.concurrency_key "
                "  AND active.status IN (?, ?) AND active.lease_expires_at>?"
                ")) ORDER BY candidate.rowid ASC LIMIT 1",
                (
                    queue_name,
                    JobStatus.PENDING.value,
                    now,
                    JobStatus.RUNNING.value,
                    JobStatus.CANCELLED.value,
                    now,
                ),
            ).fetchone()
            if row is None:
                return None
            job_id = str(row["job_id"])
            connection.execute(
                "UPDATE runtime_jobs SET status=?, attempts=attempts+1, "
                "lease_owner=?, lease_expires_at=?, heartbeat_at=?, updated_at=? "
                "WHERE job_id=? AND status=? AND cancel_requested=0",
                (
                    JobStatus.RUNNING.value,
                    worker_id,
                    now + lease_seconds,
                    now,
                    now,
                    job_id,
                    JobStatus.PENDING.value,
                ),
            )
            claimed = connection.execute(
                "SELECT * FROM runtime_jobs WHERE job_id=?", (job_id,)
            ).fetchone()
        return _from_row(claimed)

    def heartbeat(
        self, job_id: str, worker_id: str, *, lease_seconds: float
    ) -> bool:
        _positive(lease_seconds, "lease_seconds")
        now = self._now()
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "UPDATE runtime_jobs SET lease_expires_at=?, heartbeat_at=?, updated_at=? "
                "WHERE job_id=? AND status=? AND lease_owner=? "
                "AND cancel_requested=0 AND lease_expires_at>?",
                (
                    now + lease_seconds,
                    now,
                    now,
                    job_id,
                    JobStatus.RUNNING.value,
                    worker_id,
                    now,
                ),
            )
        return cursor.rowcount == 1

    def complete(
        self,
        job_id: str,
        worker_id: str,
        result: dict[str, Any] | None = None,
    ) -> bool:
        now = self._now()
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "UPDATE runtime_jobs SET status=?, result_json=?, lease_owner=NULL, "
                "lease_expires_at=NULL, heartbeat_at=NULL, updated_at=? "
                "WHERE job_id=? AND status=? AND lease_owner=? "
                "AND cancel_requested=0 AND lease_expires_at>?",
                (
                    JobStatus.COMPLETED.value,
                    _encode(result) if result is not None else None,
                    now,
                    job_id,
                    JobStatus.RUNNING.value,
                    worker_id,
                    now,
                ),
            )
        return cursor.rowcount == 1

    def is_owned(self, job_id: str, worker_id: str) -> bool:
        """Return whether a worker still owns a live, writable lease."""
        now = self._now()
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM runtime_jobs WHERE job_id=? AND status=? "
                "AND lease_owner=? AND cancel_requested=0 AND lease_expires_at>?",
                (job_id, JobStatus.RUNNING.value, worker_id, now),
            ).fetchone()
        return row is not None

    def fail(
        self,
        job_id: str,
        worker_id: str,
        error: str,
        *,
        error_type: str,
        retry_delay: float = 0,
    ) -> JobStatus:
        if retry_delay < 0:
            raise ValueError("retry_delay cannot be negative")
        now = self._now()
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM runtime_jobs WHERE job_id=?", (job_id,)
            ).fetchone()
            if row is None:
                raise KeyError(job_id)
            current = _from_row(row)
            if current.status == JobStatus.CANCELLED or current.cancel_requested:
                return JobStatus.CANCELLED
            if (
                current.status != JobStatus.RUNNING
                or current.lease_owner != worker_id
                or current.lease_expires_at is None
                or current.lease_expires_at <= now
            ):
                return current.status
            status = (
                JobStatus.RETRY_WAIT
                if current.attempts < current.max_attempts
                else JobStatus.FAILED
            )
            connection.execute(
                "UPDATE runtime_jobs SET status=?, available_at=?, lease_owner=NULL, "
                "lease_expires_at=NULL, heartbeat_at=NULL, error=?, error_type=?, updated_at=? "
                "WHERE job_id=? AND status=? AND lease_owner=? AND cancel_requested=0",
                (
                    status.value,
                    now + retry_delay,
                    error,
                    error_type,
                    now,
                    job_id,
                    JobStatus.RUNNING.value,
                    worker_id,
                ),
            )
        return status

    def cancel(self, job_id: str, *, reason: str = "cancelled") -> bool:
        now = self._now()
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "UPDATE runtime_jobs SET status=?, cancel_requested=1, error=?, "
                "error_type=?, updated_at=? WHERE job_id=? AND status NOT IN (?, ?, ?)",
                (
                    JobStatus.CANCELLED.value,
                    reason,
                    "cancelled",
                    now,
                    job_id,
                    JobStatus.COMPLETED.value,
                    JobStatus.FAILED.value,
                    JobStatus.CANCELLED.value,
                ),
            )
        return cursor.rowcount == 1

    def release_cancelled(self, job_id: str, worker_id: str) -> bool:
        """Release the serial-execution fence after a cancelled worker exits."""
        now = self._now()
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "UPDATE runtime_jobs SET lease_owner=NULL, lease_expires_at=NULL, "
                "heartbeat_at=NULL, updated_at=? WHERE job_id=? AND status=? AND lease_owner=?",
                (now, job_id, JobStatus.CANCELLED.value, worker_id),
            )
        return cursor.rowcount == 1

    def active_for_concurrency(
        self, queue_name: str, concurrency_key: str
    ) -> JobRecord | None:
        """Return the running job, or the oldest queued job, for one key."""
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM runtime_jobs WHERE queue_name=? AND concurrency_key=? "
                "AND status IN (?, ?, ?) "
                "ORDER BY CASE status WHEN ? THEN 0 ELSE 1 END, rowid ASC LIMIT 1",
                (
                    queue_name,
                    concurrency_key,
                    JobStatus.RUNNING.value,
                    JobStatus.PENDING.value,
                    JobStatus.RETRY_WAIT.value,
                    JobStatus.RUNNING.value,
                ),
            ).fetchone()
        return _from_row(row) if row is not None else None

    def has_unfinished(self, queue_name: str) -> bool:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM runtime_jobs WHERE queue_name=? AND status IN (?, ?, ?) LIMIT 1",
                (
                    queue_name,
                    JobStatus.PENDING.value,
                    JobStatus.RUNNING.value,
                    JobStatus.RETRY_WAIT.value,
                ),
            ).fetchone()
        return row is not None

    def is_cancelled(self, job_id: str) -> bool:
        job = self.get(job_id)
        return bool(job and (job.cancel_requested or job.status == JobStatus.CANCELLED))

    def recover_expired(self, queue_name: str | None = None) -> List[str]:
        now = self._now()
        with self.database.transaction() as connection:
            return self._recover_expired_in_transaction(connection, now, queue_name)

    @staticmethod
    def _recover_expired_in_transaction(
        connection: Any, now: float, queue_name: str | None
    ) -> List[str]:
        where = "status=? AND lease_expires_at IS NOT NULL AND lease_expires_at<=?"
        params: list[Any] = [JobStatus.RUNNING.value, now]
        if queue_name is not None:
            where += " AND queue_name=?"
            params.append(queue_name)
        rows = connection.execute(
            f"SELECT * FROM runtime_jobs WHERE {where} ORDER BY rowid ASC",
            params,
        ).fetchall()
        recovered: list[str] = []
        for row in rows:
            job = _from_row(row)
            if job.cancel_requested:
                status = JobStatus.CANCELLED
                error_type = "cancelled"
            elif job.attempts >= job.max_attempts:
                status = JobStatus.FAILED
                error_type = "lease_expired"
            else:
                status = JobStatus.PENDING
                error_type = "lease_expired"
            connection.execute(
                "UPDATE runtime_jobs SET status=?, available_at=?, lease_owner=NULL, "
                "lease_expires_at=NULL, heartbeat_at=NULL, error=?, error_type=?, updated_at=? "
                "WHERE job_id=? AND status=?",
                (
                    status.value,
                    now,
                    "worker lease expired",
                    error_type,
                    now,
                    job.job_id,
                    JobStatus.RUNNING.value,
                ),
            )
            recovered.append(job.job_id)
        return recovered


def _required(value: str, name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{name} cannot be empty")
    return normalized


def _positive(value: float, name: str) -> None:
    if value <= 0:
        raise ValueError(f"{name} must be positive")


def _encode(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _from_row(row: Any) -> JobRecord:
    return JobRecord(
        job_id=str(row["job_id"]),
        queue_name=str(row["queue_name"]),
        status=JobStatus(row["status"]),
        payload=json.loads(row["payload_json"]),
        result=json.loads(row["result_json"]) if row["result_json"] else None,
        idempotency_key=row["idempotency_key"],
        concurrency_key=row["concurrency_key"],
        attempts=int(row["attempts"]),
        max_attempts=int(row["max_attempts"]),
        available_at=float(row["available_at"]),
        lease_owner=row["lease_owner"],
        lease_expires_at=(
            float(row["lease_expires_at"]) if row["lease_expires_at"] is not None else None
        ),
        heartbeat_at=(
            float(row["heartbeat_at"]) if row["heartbeat_at"] is not None else None
        ),
        cancel_requested=bool(row["cancel_requested"]),
        error=row["error"],
        error_type=row["error_type"],
        created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
    )
