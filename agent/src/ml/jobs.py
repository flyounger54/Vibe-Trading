"""Crash-safe persistence and cooperative cancellation for ML training jobs."""

from __future__ import annotations

import copy
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_DEFAULT_JOB_STORE = Path.home() / ".vibe-trading" / "ml_training_jobs.json"
_TERMINAL_STATUSES = {"done", "error", "cancelled", "interrupted"}


class TrainingJobStore:
    """Thread-safe JSON job store with atomic writes and restart recovery."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or _DEFAULT_JOB_STORE
        self._lock = threading.RLock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._load()
        self._mark_unfinished_jobs_interrupted()

    def create(self, job_id: str, request: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            if job_id in self._jobs:
                raise ValueError(f"Training job already exists: {job_id}")
            now = _now()
            self._jobs[job_id] = {
                "job_id": job_id,
                "status": "running",
                "created_at": now,
                "updated_at": now,
                "events": [{"stage": "queued", "ts": now, "message": "Training job accepted"}],
                "result": None,
                "error": None,
                "request": copy.deepcopy(request),
                "cancel_requested": False,
            }
            self._persist()
            return copy.deepcopy(self._jobs[job_id])

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return copy.deepcopy(job) if job is not None else None

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return [copy.deepcopy(job) for job in self._jobs.values()]

    def append_event(self, job_id: str, stage: str, **payload: Any) -> None:
        with self._lock:
            job = self._require(job_id)
            job["events"].append({"stage": stage, "ts": _now(), **copy.deepcopy(payload)})
            job["updated_at"] = _now()
            self._persist()

    def finish(self, job_id: str, result: dict[str, Any]) -> None:
        with self._lock:
            job = self._require(job_id)
            if job["cancel_requested"]:
                self._set_terminal(job, "cancelled", error="Training cancelled by user")
            else:
                self._set_terminal(job, "done", result=copy.deepcopy(result))
            self._persist()

    def fail(self, job_id: str, error: str) -> None:
        with self._lock:
            job = self._require(job_id)
            status = "cancelled" if job["cancel_requested"] else "error"
            self._set_terminal(job, status, error=error)
            self._persist()

    def request_cancel(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._require(job_id)
            if job["status"] in _TERMINAL_STATUSES:
                return copy.deepcopy(job)
            job["cancel_requested"] = True
            job["status"] = "cancelling"
            job["events"].append({"stage": "cancelling", "ts": _now(), "message": "Cancellation requested"})
            job["updated_at"] = _now()
            self._persist()
            return copy.deepcopy(job)

    def is_cancel_requested(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            return bool(job and job.get("cancel_requested"))

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            loaded = json.loads(self._path.read_text(encoding="utf-8"))
            jobs = loaded.get("jobs", {})
            if not isinstance(jobs, dict):
                raise ValueError("jobs must be an object")
            self._jobs = jobs
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            # A corrupt job store must not look like an empty, successful job
            # history. Preserve it for inspection and start a clean store.
            corrupt = self._path.with_suffix(self._path.suffix + ".corrupt")
            try:
                os.replace(self._path, corrupt)
            except OSError:
                pass
            self._jobs = {}
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._persist()

    def _mark_unfinished_jobs_interrupted(self) -> None:
        changed = False
        with self._lock:
            for job in self._jobs.values():
                if job.get("status") not in _TERMINAL_STATUSES:
                    self._set_terminal(
                        job,
                        "interrupted",
                        error="Service restarted before the training worker completed",
                    )
                    changed = True
            if changed:
                self._persist()

    @staticmethod
    def _set_terminal(
        job: dict[str, Any],
        status: str,
        *,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        job["status"] = status
        job["result"] = result
        job["error"] = error
        job["updated_at"] = _now()
        job["events"].append({"stage": status, "ts": _now(), "error": error})

    def _require(self, job_id: str) -> dict[str, Any]:
        job = self._jobs.get(job_id)
        if job is None:
            raise KeyError(f"Training job not found: {job_id}")
        return job

    def _persist(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temp = self._path.with_suffix(self._path.suffix + ".tmp")
        temp.write_text(json.dumps({"jobs": self._jobs}, indent=2, default=str), encoding="utf-8")
        os.replace(temp, self._path)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
