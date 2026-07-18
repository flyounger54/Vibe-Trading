"""Durable, restart-safe scheduling bridge for industry-chain research.

The Swarm runtime already owns execution/retry of a run.  This module owns the
separate question of *when* to launch a refresh, using the same SQLite job
queue and a persisted schedule definition.  A schedule therefore survives a
web-server restart and never depends on an in-memory timer.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections.abc import Callable
from typing import Any

from src.industry_chain.store import STATUS_ANALYZING, Chain, IndustryChainStore
from src.state.database import ConcurrentUpdateError
from src.state.jobs import JobRecord, JobStatus, SQLiteJobQueue

logger = logging.getLogger(__name__)

QUEUE_NAME = "industry_chain_refresh"
_SCHEDULE_SECONDS = {"weekly": 7 * 24 * 60 * 60, "monthly": 30 * 24 * 60 * 60}


class IndustryChainRefreshService:
    """Persist, dispatch, retry, and cancel industry-chain refresh requests."""

    def __init__(
        self,
        store: IndustryChainStore,
        get_runtime: Callable[[], Any],
        *,
        now_fn: Callable[[], float] = time.time,
        poll_interval_seconds: float = 30.0,
    ) -> None:
        self.store = store
        self.get_runtime = get_runtime
        self.queue = SQLiteJobQueue(store.database, now_fn=now_fn)
        self.now_fn = now_fn
        self.poll_interval_seconds = poll_interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._worker_id = f"industry-refresh-{uuid.uuid4().hex[:10]}"

    def start(self) -> None:
        """Start a daemon dispatcher. Idempotent and safe at app startup."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self.queue.recover_expired(QUEUE_NAME)
        self._thread = threading.Thread(target=self._run, name="industry-chain-refresh", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Ask the daemon dispatcher to stop; unfinished leases recover later."""
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=min(2.0, self.poll_interval_seconds + 0.1))
        self._thread = None

    def configure_schedule(
        self,
        chain: Chain,
        schedule: str,
        *,
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        """Persist a weekly/monthly refresh schedule and its next due time."""
        if schedule not in {"", *tuple(_SCHEDULE_SECONDS)}:
            raise ValueError("schedule must be '', 'weekly', or 'monthly'")
        chain.refresh_schedule = schedule
        self.store.save_chain(chain, expected_version=expected_version)
        next_due = self.now_fn() + _SCHEDULE_SECONDS[schedule] if schedule else None
        return self.store.save_schedule(chain.chain_id, schedule, next_due_at=next_due)

    def request_analysis(
        self,
        chain_id: str,
        *,
        market: str | None = None,
        idempotency_key: str | None = None,
        trigger: str = "manual",
    ) -> JobRecord:
        """Persist a launch request, then promptly dispatch its short setup work."""
        chain = self.store.get_chain(chain_id)
        if chain is None:
            raise KeyError(chain_id)
        if chain.status == STATUS_ANALYZING and chain.refresh_job_id:
            current = self.queue.get(chain.refresh_job_id)
            if current is not None:
                return current
        active = self.queue.active_for_concurrency(QUEUE_NAME, chain_id)
        if active is not None and trigger == "manual":
            return active
        key = idempotency_key or f"{trigger}:{chain_id}:{chain.row_version}"
        job = self.queue.enqueue(
            QUEUE_NAME,
            {"chain_id": chain_id, "market": market or chain.market, "trigger": trigger},
            idempotency_key=key,
            concurrency_key=chain_id,
            max_attempts=3,
        )
        self.dispatch_available()
        return self.queue.get(job.job_id) or job

    def retry(self, job_id: str) -> JobRecord:
        """Create a new persisted attempt for a failed/cancelled launch."""
        job = self.queue.get(job_id)
        if job is None:
            raise KeyError(job_id)
        if job.status not in {JobStatus.FAILED, JobStatus.CANCELLED}:
            raise ValueError("only failed or cancelled refresh jobs can be retried")
        chain_id = str(job.payload.get("chain_id", ""))
        if not chain_id:
            raise ValueError("refresh job has no chain_id")
        retried = self.queue.enqueue(
            QUEUE_NAME,
            dict(job.payload),
            idempotency_key=f"retry:{job_id}:{uuid.uuid4().hex}",
            concurrency_key=chain_id,
            max_attempts=3,
        )
        self.dispatch_available()
        return self.queue.get(retried.job_id) or retried

    def cancel(self, job_id: str) -> bool:
        """Cancel the durable launch request and its downstream Swarm if begun."""
        job = self.queue.get(job_id)
        if job is None:
            return False
        changed = self.queue.cancel(job_id, reason="user_cancelled")
        run_id = (job.result or {}).get("run_id")
        if run_id:
            try:
                changed = bool(self.get_runtime().cancel_run(str(run_id))) or changed
            except Exception:
                logger.warning("failed to cancel downstream swarm %s", run_id, exc_info=True)
        return changed

    def get_job(self, job_id: str) -> JobRecord | None:
        return self.queue.get(job_id)

    def tick(self) -> None:
        """Enqueue due schedules and dispatch pending launch jobs once."""
        self.queue.recover_expired(QUEUE_NAME)
        now = self.now_fn()
        for schedule in self.store.list_schedules():
            cadence = str(schedule.get("schedule", ""))
            due = schedule.get("next_due_at")
            if cadence not in _SCHEDULE_SECONDS or due is None or float(due) > now:
                continue
            chain_id = str(schedule["chain_id"])
            slot = int(float(due))
            chain = self.store.get_chain(chain_id)
            if chain is not None and chain.status != STATUS_ANALYZING:
                self.queue.enqueue(
                    QUEUE_NAME,
                    {"chain_id": chain_id, "market": "", "trigger": "schedule", "scheduled_for": slot},
                    idempotency_key=f"schedule:{chain_id}:{slot}",
                    concurrency_key=chain_id,
                    max_attempts=3,
                )
            # Advance before dispatch; the slot idempotency key prevents an
            # at-least-once restart from launching it twice.
            self.store.save_schedule(
                chain_id,
                cadence,
                next_due_at=float(due) + _SCHEDULE_SECONDS[cadence],
                expected_version=int(schedule["row_version"]),
            )
        self.dispatch_available()

    def dispatch_available(self) -> None:
        """Claim and launch each ready job; Swarm performs the long-running work."""
        while True:
            job = self.queue.claim(QUEUE_NAME, self._worker_id, lease_seconds=30.0)
            if job is None:
                return
            try:
                self._dispatch(job)
            except Exception as exc:  # noqa: BLE001 - durable queue records the failure/retry
                status = self.queue.fail(
                    job.job_id,
                    self._worker_id,
                    str(exc),
                    error_type="industry_refresh_launch_error",
                    retry_delay=1.0,
                )
                logger.warning("industry refresh launch %s ended as %s: %s", job.job_id, status, exc)
            else:
                self.queue.complete(job.job_id, self._worker_id, {"run_id": self._run_id_for(job)})

    def _dispatch(self, job: JobRecord) -> None:
        chain_id = str(job.payload.get("chain_id", ""))
        chain = self.store.get_chain(chain_id)
        if chain is None:
            raise KeyError(f"chain {chain_id} no longer exists")
        market = str(job.payload.get("market") or chain.market)
        variables = {
            "topic": chain.name,
            "market": market,
            "segments": ",".join(segment.name for segment in chain.segments),
        }
        run = self.get_runtime().start_run("industry_chain_dashboard", variables)
        chain.swarm_run_id = run.id
        chain.refresh_job_id = job.job_id
        chain.status = STATUS_ANALYZING
        try:
            self.store.save_chain(chain)
        except ConcurrentUpdateError:
            # Re-read once.  We never overwrite a user edit but still attach
            # the launched run to the newest object if no second writer races.
            latest = self.store.get_chain(chain_id)
            if latest is None:
                raise
            latest.swarm_run_id = run.id
            latest.refresh_job_id = job.job_id
            latest.status = STATUS_ANALYZING
            self.store.save_chain(latest)
        job.payload["run_id"] = run.id

    @staticmethod
    def _run_id_for(job: JobRecord) -> str:
        return str(job.payload.get("run_id", ""))

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                logger.exception("industry-chain refresh tick failed")
            self._stop.wait(self.poll_interval_seconds)
