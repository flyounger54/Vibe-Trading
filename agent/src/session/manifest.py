"""Auditable run manifests and deterministic completion quality gates."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Mapping


_URL_RE = re.compile(r"https?://[^\s<>\]\[\)\}\"']+", re.IGNORECASE)
_TERMINAL_RESULTS = {"success", "failed", "cancelled"}


def evaluate_quality_gate(result: Mapping[str, Any]) -> dict[str, Any]:
    """Run non-LLM checks that decide whether a claimed success is accepted.

    A model-provided ``quality_score`` is recorded as a reference only. It is
    deliberately excluded from the pass/fail decision.
    """
    checks: list[dict[str, Any]] = []
    status = result.get("status")
    checks.append(
        {
            "name": "result_schema",
            "passed": isinstance(status, str) and status in _TERMINAL_RESULTS,
            "detail": "status must be one of success, failed, cancelled",
        }
    )
    if status == "success":
        content = result.get("content", "")
        run_dir = result.get("run_dir")
        has_deliverable = bool(str(content).strip())
        if run_dir:
            metrics = Path(str(run_dir)) / "artifacts" / "metrics.csv"
            has_deliverable = has_deliverable or metrics.is_file()
        checks.append(
            {
                "name": "deliverable_present",
                "passed": has_deliverable,
                "detail": "successful result needs final text or metrics.csv",
            }
        )

    run_dir_value = result.get("run_dir")
    if run_dir_value:
        run_dir = Path(str(run_dir_value))
        checks.append(
            {
                "name": "run_directory_exists",
                "passed": run_dir.is_dir(),
                "detail": str(run_dir),
            }
        )

    declared_artifacts = result.get("artifact_paths", result.get("artifacts", []))
    if declared_artifacts is None:
        declared_artifacts = []
    artifact_list_valid = isinstance(declared_artifacts, list) and all(
        isinstance(item, str) and item.strip() for item in declared_artifacts
    )
    checks.append(
        {
            "name": "artifact_declaration_schema",
            "passed": artifact_list_valid,
            "detail": "artifact_paths must be a list of non-empty paths",
        }
    )
    if artifact_list_valid:
        missing = [path for path in declared_artifacts if not Path(path).is_file()]
        checks.append(
            {
                "name": "declared_artifacts_exist",
                "passed": not missing,
                "detail": missing,
            }
        )

    accepted = all(bool(check["passed"]) for check in checks)
    return {
        "version": "deterministic-quality-gate.v1",
        "accepted": accepted,
        "checks": checks,
        "llm_self_score": result.get("quality_score"),
        "llm_self_score_is_decisive": False,
    }


def write_run_manifest(
    *,
    root_dir: Path,
    session_id: str,
    attempt_id: str,
    job_id: str,
    prompt: str,
    result: Mapping[str, Any],
    error_type: str | None = None,
) -> tuple[Path, dict[str, Any]]:
    """Write a stable manifest beside a run, with a safe fallback directory."""
    run_dir_value = result.get("run_dir")
    run_dir = Path(str(run_dir_value)) if run_dir_value else None
    destination = run_dir if run_dir is not None and run_dir.is_dir() else root_dir / "attempts" / attempt_id
    destination.mkdir(parents=True, exist_ok=True)

    trace = _trace_summary(result.get("react_trace"))
    output = str(result.get("content", ""))
    citations = sorted(set(_URL_RE.findall(output + "\n" + json.dumps(trace, ensure_ascii=False))))
    gate = evaluate_quality_gate(result)
    payload: dict[str, Any] = {
        "schema_version": "vibe-trading.run-manifest.v1",
        "session_id": session_id,
        "attempt_id": attempt_id,
        "job_id": job_id,
        "status": result.get("status"),
        "input": _summary(prompt),
        "output": _summary(output),
        "tool_trace": trace,
        "evidence": {
            "citations": citations,
            "declared_artifacts": result.get("artifact_paths", result.get("artifacts", [])),
            "run_dir": str(run_dir) if run_dir else None,
        },
        "error": {
            "type": error_type or result.get("error_code"),
            "message": result.get("reason"),
        },
        "quality_gate": gate,
    }
    payload["manifest_sha256"] = _sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )
    path = destination / "run_manifest.json"
    _atomic_json(path, payload)
    return path, payload


def _trace_summary(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    summary: list[dict[str, Any]] = []
    for entry in value:
        if not isinstance(entry, dict):
            continue
        summary.append(
            {
                key: entry[key]
                for key in ("type", "tool", "status", "result_preview", "elapsed_ms", "call_id")
                if key in entry
            }
        )
    return summary


def _summary(text: str) -> dict[str, Any]:
    return {"chars": len(text), "sha256": _sha256(text), "preview": text[:500]}


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
