"""Contract parity checks across HTTP, CLI and MCP goal adapters."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

import api_server
import mcp_server
from cli.commands import goal as goal_cli
from src.goal import GoalStore
from src.session.events import EventBus
from src.session.service import SessionService
from src.session.store import SessionStore


def _api_client(tmp_path: Path, monkeypatch) -> TestClient:
    session_store = SessionStore(tmp_path / "api-sessions")
    service = SessionService(
        store=session_store,
        event_bus=EventBus(event_store=session_store),
        runs_dir=tmp_path / "runs",
    )
    monkeypatch.setattr(api_server, "_session_service", service)
    monkeypatch.setattr(api_server, "_goal_store", GoalStore(tmp_path / "api-goals.db"))
    return TestClient(api_server.app)


def _semantic_snapshot(snapshot: dict) -> dict:
    goal = snapshot["goal"]
    return {
        "objective": goal["objective"],
        "status": goal["status"],
        "protocol": goal["protocol"],
        "risk_tier": goal["risk_tier"],
        "criteria": [item["text"] for item in snapshot["criteria"]],
        "evidence_count": snapshot["evidence_count"],
    }


def test_api_cli_mcp_create_goal_have_identical_semantics(tmp_path: Path, monkeypatch) -> None:
    objective = "Evaluate NVDA momentum as a research-only thesis."

    client = _api_client(tmp_path, monkeypatch)
    created_session = client.post("/api/v1/sessions", json={"title": "parity"})
    assert created_session.status_code == 201
    session_id = created_session.json()["session_id"]
    api_result = client.post(
        f"/api/v1/sessions/{session_id}/goal",
        json={"objective": objective},
    )
    assert api_result.status_code == 201

    mcp_store = GoalStore(tmp_path / "mcp-goals.db")
    monkeypatch.setattr(mcp_server, "_goal_store", mcp_store)
    mcp_result = json.loads(mcp_server.start_research_goal("mcp-session", objective))
    assert mcp_result["status"] == "ok"

    cli_store = GoalStore(tmp_path / "cli-goals.db")
    monkeypatch.setattr(goal_cli, "_goal_store", cli_store)
    assert goal_cli.cmd_start(SimpleNamespace(session_id="cli-session"), *objective.split()) == 0
    cli_result = cli_store.get_current_snapshot("cli-session")
    assert cli_result is not None

    assert _semantic_snapshot(api_result.json()) == _semantic_snapshot(mcp_result["snapshot"])
    assert _semantic_snapshot(api_result.json()) == _semantic_snapshot(cli_result)


def test_api_cli_mcp_expose_same_invalid_argument_code(tmp_path: Path, monkeypatch, capsys) -> None:
    objective = "Buy 1 BTC now."

    client = _api_client(tmp_path, monkeypatch)
    created_session = client.post("/api/v1/sessions", json={"title": "parity"})
    session_id = created_session.json()["session_id"]
    api_result = client.post(
        f"/api/v1/sessions/{session_id}/goal",
        json={"objective": objective},
    )
    assert api_result.status_code == 400
    assert api_result.json()["code"] == "invalid_argument"

    monkeypatch.setattr(mcp_server, "_goal_store", GoalStore(tmp_path / "mcp-goals.db"))
    mcp_result = json.loads(mcp_server.start_research_goal("mcp-session", objective))
    assert mcp_result["error_type"] == "invalid_argument"

    monkeypatch.setattr(goal_cli, "_goal_store", GoalStore(tmp_path / "cli-goals.db"))
    assert goal_cli.cmd_start(SimpleNamespace(session_id="cli-session"), *objective.split()) == 1
    assert "invalid_argument" in capsys.readouterr().out
