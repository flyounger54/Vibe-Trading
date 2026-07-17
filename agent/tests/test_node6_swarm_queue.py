"""Node 6 contracts for durable Swarm outer-run scheduling."""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import src.swarm.runtime as runtime_module
from src.state.jobs import JobStatus
from src.swarm.models import RunStatus, SwarmAgentSpec, SwarmRun, SwarmTask
from src.swarm.store import SwarmStore


def _run(run_id: str) -> SwarmRun:
    return SwarmRun(
        id=run_id,
        preset_name="node6",
        created_at=datetime.now(timezone.utc).isoformat(),
        agents=[SwarmAgentSpec(id="analyst", role="Analyst", system_prompt="x")],
        tasks=[SwarmTask(id="report", agent_id="analyst", prompt_template="report")],
    )


def _wait_for(predicate, timeout: float = 2) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise TimeoutError("condition did not become true")
        time.sleep(0.01)


def test_start_run_is_claimed_from_persistent_queue(tmp_path: Path, monkeypatch) -> None:
    store = SwarmStore(tmp_path / "swarm")
    runtime = runtime_module.SwarmRuntime(store, max_workers=1)
    started = threading.Event()

    monkeypatch.setattr(runtime_module, "build_run_from_preset", lambda *args: _run("queued-run"))

    def execute(run, cancel_event, include_shell_tools=False):
        del cancel_event, include_shell_tools
        started.set()
        run.status = RunStatus.completed
        run.completed_at = datetime.now(timezone.utc).isoformat()
        store.update_run(run)

    monkeypatch.setattr(runtime, "_execute_run", execute)
    try:
        run = runtime.start_run("node6", {})
        assert started.wait(timeout=2)
        _wait_for(
            lambda: runtime._job_queue.list("swarm_runs")[0].status == JobStatus.COMPLETED
        )
        job = runtime._job_queue.list("swarm_runs")[0]
        assert job.payload["run_id"] == run.id
        assert job.result == {"run_id": run.id, "status": "completed"}
    finally:
        runtime.shutdown()


def test_cancelled_swarm_job_cannot_commit_late_completion(tmp_path: Path, monkeypatch) -> None:
    store = SwarmStore(tmp_path / "swarm")
    runtime = runtime_module.SwarmRuntime(store, max_workers=1)
    started = threading.Event()
    released = threading.Event()
    monkeypatch.setattr(runtime_module, "build_run_from_preset", lambda *args: _run("cancel-run"))

    def execute(run, cancel_event, include_shell_tools=False):
        del include_shell_tools
        started.set()
        assert cancel_event.wait(timeout=2)
        released.wait(timeout=2)
        # This simulates a worker attempting to publish a late successful
        # result after the queue cancellation fence has already won.
        run.status = RunStatus.completed
        run.completed_at = datetime.now(timezone.utc).isoformat()
        store.update_run(run)

    monkeypatch.setattr(runtime, "_execute_run", execute)
    try:
        run = runtime.start_run("node6", {})
        assert started.wait(timeout=2)
        assert runtime.cancel_run(run.id) is True
        released.set()
        _wait_for(
            lambda: runtime._job_queue.list("swarm_runs")[0].lease_owner is None
        )
        job = runtime._job_queue.list("swarm_runs")[0]
        assert job.status == JobStatus.CANCELLED
        assert job.result is None
        assert store.load_run(run.id).status == RunStatus.cancelled
    finally:
        runtime.shutdown()
