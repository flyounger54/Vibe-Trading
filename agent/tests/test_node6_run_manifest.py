"""Node 6 contracts for audit manifests and deterministic quality gates."""

from __future__ import annotations

import json
from pathlib import Path

from src.session.manifest import evaluate_quality_gate, write_run_manifest


def test_manifest_captures_io_trace_citations_and_non_decisive_llm_score(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    artifact = run_dir / "artifacts" / "metrics.csv"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("return\n0.12\n", encoding="utf-8")
    path, manifest = write_run_manifest(
        root_dir=tmp_path / "fallback",
        session_id="session-1",
        attempt_id="attempt-1",
        job_id="job-1",
        prompt="analyze NVDA",
        result={
            "status": "success",
            "content": "See https://example.test/source for the data.",
            "run_dir": str(run_dir),
            "artifact_paths": [str(artifact)],
            "quality_score": 0.99,
            "react_trace": [
                {"type": "tool_call", "tool": "market_data", "call_id": "call-1"},
                {"type": "tool_call", "tool": "market_data", "status": "ok", "result_preview": "bars"},
            ],
        },
    )

    assert path == run_dir / "run_manifest.json"
    assert manifest["quality_gate"]["accepted"] is True
    assert manifest["quality_gate"]["llm_self_score"] == 0.99
    assert manifest["quality_gate"]["llm_self_score_is_decisive"] is False
    assert manifest["evidence"]["citations"] == ["https://example.test/source"]
    assert manifest["tool_trace"][0]["tool"] == "market_data"
    assert json.loads(path.read_text(encoding="utf-8"))["manifest_sha256"] == manifest["manifest_sha256"]


def test_quality_gate_rejects_missing_declared_artifact_even_with_high_self_score(
    tmp_path: Path,
) -> None:
    gate = evaluate_quality_gate(
        {
            "status": "success",
            "content": "looks good",
            "artifact_paths": [str(tmp_path / "missing.csv")],
            "quality_score": 1.0,
        }
    )

    assert gate["accepted"] is False
    assert gate["llm_self_score"] == 1.0
    assert any(
        check["name"] == "declared_artifacts_exist" and not check["passed"]
        for check in gate["checks"]
    )
