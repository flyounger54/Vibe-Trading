from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path

from src.observability import JsonFormatter, metrics_payload, observe_http_request
from src.session.events import EventBus
from src.session.service import SessionService
from src.session.store import SessionStore
from src.state.database import StateDatabase
from src.state.jobs import JobStatus, SQLiteJobQueue
from src.state.operations import backup_database, restore_database, verify_database


def test_json_formatter_preserves_correlation_fields() -> None:
    record = logging.LogRecord(
        "vibe.test",
        logging.INFO,
        __file__,
        1,
        "http_request",
        (),
        None,
    )
    record.request_id = "req-123"
    record.job_id = "job-456"
    record.elapsed_ms = 12.5

    payload = json.loads(JsonFormatter().format(record))

    assert payload["message"] == "http_request"
    assert payload["request_id"] == "req-123"
    assert payload["job_id"] == "job-456"
    assert payload["elapsed_ms"] == 12.5
    assert payload["level"] == "INFO"


def test_metrics_payload_exports_http_and_runtime_metrics() -> None:
    observe_http_request("GET", "/healthz", 200, 0.012)

    payload, content_type = metrics_payload(
        queue_counts={JobStatus.PENDING.value: 2, JobStatus.RUNNING.value: 1},
        configured_workers=4,
        live_workers=1,
    )
    text = payload.decode("utf-8")

    assert "vibe_http_requests_total" in text
    assert 'route="/healthz"' in text
    assert 'status="pending"} 2.0' in text
    assert "vibe_session_workers_configured 4.0" in text
    assert content_type.startswith("text/plain")


def test_state_database_integrity_and_queue_counts(tmp_path: Path) -> None:
    database = StateDatabase(tmp_path / "state.db")
    queue = SQLiteJobQueue(database)
    queue.enqueue("research", {"symbol": "AAPL"})

    assert database.integrity_check() == "ok"
    assert database.schema_version() == database.expected_schema_version
    assert queue.status_counts("research") == {JobStatus.PENDING.value: 1}


def test_session_service_readiness_snapshot(tmp_path: Path) -> None:
    store = SessionStore(base_dir=tmp_path / "sessions", db_path=tmp_path / "state.db")
    service = SessionService(store, EventBus(event_store=store), tmp_path / "runs")

    snapshot = service.readiness_snapshot()

    assert snapshot["ready"] is True
    assert snapshot["database"]["integrity"] == "ok"
    assert snapshot["database"]["schema_version"] == snapshot["database"]["expected_schema_version"]
    assert snapshot["workers"]["configured"] == 4
    assert snapshot["queue"]["name"] == service.QUEUE_NAME


def test_state_backup_and_atomic_restore_drill(tmp_path: Path) -> None:
    source = tmp_path / "state.db"
    database = StateDatabase(source)
    database.upsert_record("job", "before", {"status": "saved"})
    backup = tmp_path / "backups" / "state.db"

    manifest = backup_database(source, backup)
    database.delete_record("job", "before")
    result = restore_database(backup, source)

    assert manifest["backup"]["integrity"] == "ok"
    assert result["status"] == "restored"
    assert verify_database(source)["integrity"] == "ok"
    assert StateDatabase(source).get_record("job", "before") is not None


def _run_rc_evidence(tmp_path: Path, *, critical: int = 0) -> subprocess.CompletedProcess[str]:
    readiness = tmp_path / "readiness.json"
    performance = tmp_path / "performance.json"
    trivy = tmp_path / "trivy.json"
    output = tmp_path / "evidence.json"
    readiness.write_text(
        json.dumps(
            {
                "samples": 60,
                "successes": 60,
                "uptime_pct": 100.0,
                "longest_failure_streak": 0,
                "accepted": True,
            }
        ),
        encoding="utf-8",
    )
    performance.write_text(
        json.dumps({"api": {"p95_ms": 120.0}, "queue": {"jobs": 500, "lost": 0}, "accepted": True}),
        encoding="utf-8",
    )
    vulnerabilities = [
        {"VulnerabilityID": f"CVE-TEST-{index}", "Severity": "CRITICAL"} for index in range(critical)
    ]
    trivy.write_text(json.dumps({"Results": [{"Vulnerabilities": vulnerabilities}]}), encoding="utf-8")
    return subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parents[2] / "scripts" / "node11-rc-evidence"),
            "--day",
            "1",
            "--candidate-sha",
            "b" * 40,
            "--image-ref",
            f"ghcr.io/example/vibe@sha256:{'a' * 64}",
            "--observed-digest",
            f"sha256:{'a' * 64}",
            "--run-url",
            "https://github.com/example/vibe/actions/runs/123",
            "--container-smoke",
            "passed",
            "--restore-drill",
            "passed",
            "--readiness",
            str(readiness),
            "--performance",
            str(performance),
            "--trivy",
            str(trivy),
            "--output",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def test_rc_evidence_accepts_complete_clean_day(tmp_path: Path) -> None:
    result = _run_rc_evidence(tmp_path)

    assert result.returncode == 0, result.stderr
    evidence = json.loads((tmp_path / "evidence.json").read_text(encoding="utf-8"))
    assert evidence["accepted"] is True
    assert evidence["readiness"]["uptime_pct"] == 100.0
    assert evidence["queue"] == {"maximum_depth": 500, "lost": 0}
    assert all(evidence["checks"].values())


def test_rc_evidence_rejects_security_finding_but_keeps_record(tmp_path: Path) -> None:
    result = _run_rc_evidence(tmp_path, critical=1)

    assert result.returncode == 1
    evidence = json.loads((tmp_path / "evidence.json").read_text(encoding="utf-8"))
    assert evidence["accepted"] is False
    assert evidence["security"] == {"critical": 1, "high": 0}
    assert evidence["checks"]["critical_zero"] is False


def test_rc_workflow_binds_daily_evidence_to_commit_and_digest() -> None:
    workflow = (Path(__file__).resolve().parents[2] / ".github" / "workflows" / "node11-rc.yml").read_text(
        encoding="utf-8"
    )

    assert "candidate_sha:" in workflow
    assert "image_ref:" in workflow
    assert "ref: ${{ inputs.candidate_sha }}" in workflow
    assert "@sha256:" in workflow
    assert "--duration-seconds 300" in workflow
    assert "test_state_backup_and_atomic_restore_drill" in workflow
    assert "ignore-unfixed: false" in workflow
    assert "scripts/node11-rc-evidence" in workflow
    assert "if: always()" in workflow


def test_release_workflow_scans_before_signing_and_exports_verification() -> None:
    workflow = (Path(__file__).resolve().parents[2] / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )

    scan = workflow.index("Block Critical or High image vulnerabilities")
    signing = workflow.index("Keyless-sign immutable image digest")
    assert scan < signing
    assert "Verify keyless signature" in workflow
    assert "Verify GitHub artifact attestation" in workflow
    assert "node11-release-evidence.json" in workflow
    assert "if-no-files-found: error" in workflow
