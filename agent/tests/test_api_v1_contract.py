"""Node 2 versioned API, legacy compatibility and generated contract tests."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

import api_server
from src.session.events import EventBus
from src.session.service import SessionService
from src.session.store import SessionStore


class _DummyIndex:
    def index_session(self, session_id: str, title: str) -> None:
        del session_id, title

    def index_message(self, session_id: str, role: str, content: str) -> None:
        del session_id, role, content


def _client(monkeypatch, tmp_path: Path) -> TestClient:
    store = SessionStore(tmp_path / "sessions")
    service = SessionService(store, EventBus(event_store=store), tmp_path / "runs")
    service._search_index = _DummyIndex()
    monkeypatch.setattr(api_server, "_session_service", service)
    api_server.app.dependency_overrides[api_server.require_auth] = lambda: None
    return TestClient(api_server.app)


def test_v1_and_legacy_session_contracts_are_equivalent(monkeypatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, tmp_path)
    try:
        created = client.post("/api/v1/sessions", json={"title": "contract"})
        assert created.status_code == 201
        session_id = created.json()["session_id"]

        legacy = client.get(f"/sessions/{session_id}")
        versioned = client.get(f"/api/v1/sessions/{session_id}")

        assert legacy.status_code == versioned.status_code == 200
        assert legacy.json() == versioned.json() == created.json()
    finally:
        api_server.app.dependency_overrides.clear()


def test_v1_errors_use_stable_envelope_while_legacy_shape_remains(monkeypatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, tmp_path)
    try:
        versioned = client.get("/api/v1/sessions/deadbeefcafe")
        legacy = client.get("/sessions/deadbeefcafe")

        assert versioned.status_code == legacy.status_code == 404
        assert versioned.json()["code"] == "not_found"
        assert versioned.json()["retryable"] is False
        assert versioned.json()["request_id"] == versioned.headers["X-Request-ID"]
        assert legacy.json() == {"detail": "Session deadbeefcafe not found"}
    finally:
        api_server.app.dependency_overrides.clear()


def test_v1_validation_error_has_actionable_details(monkeypatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, tmp_path)
    try:
        response = client.post("/api/v1/sessions", json={"title": "x" * 501})

        assert response.status_code == 422
        assert response.json()["code"] == "invalid_argument"
        assert response.json()["message"] == "Request validation failed"
        assert response.json()["details"]
    finally:
        api_server.app.dependency_overrides.clear()


def test_session_message_idempotency_is_durable_for_legacy_and_v1_routes(
    monkeypatch,
    tmp_path: Path,
) -> None:
    client = _client(monkeypatch, tmp_path)

    async def run_agent(*args, **kwargs):
        del args, kwargs
        return {"status": "success", "content": "queued"}

    api_server._session_service._run_with_agent = run_agent
    try:
        for prefix in ("/sessions", "/api/v1/sessions"):
            created = client.post(prefix, json={"title": "idempotent"})
            assert created.status_code in {200, 201}
            session_id = created.json()["session_id"]
            headers = {"Idempotency-Key": f"message-key-{session_id}"}
            first = client.post(
                f"{prefix}/{session_id}/messages",
                headers=headers,
                json={"content": "research NVDA"},
            )
            duplicate = client.post(
                f"{prefix}/{session_id}/messages",
                headers=headers,
                json={"content": "replace this"},
            )

            assert first.status_code == duplicate.status_code == 200
            assert duplicate.json() == first.json()
            messages = api_server._session_service.get_messages(session_id)
            assert [message.content for message in messages if message.role == "user"] == [
                "research NVDA"
            ]
    finally:
        api_server.app.dependency_overrides.clear()


def test_generated_openapi_contains_legacy_and_v1_paths() -> None:
    schema = api_server.app.openapi()

    assert "/sessions" in schema["paths"]
    assert "/api/v1/sessions" in schema["paths"]
    assert "SessionResponse" in schema["components"]["schemas"]


def test_checked_in_openapi_matches_runtime_schema() -> None:
    root = Path(__file__).resolve().parents[2]
    checked_in = json.loads((root / "frontend/src/generated/openapi.json").read_text())

    assert checked_in == api_server.app.openapi()


def test_generated_client_contains_every_v1_operation() -> None:
    root = Path(__file__).resolve().parents[2]
    generated_client = (root / "frontend/src/generated/api-client.ts").read_text()
    schema = api_server.app.openapi()

    for path, path_item in schema["paths"].items():
        if not path.startswith("/api/v1/"):
            continue
        for operation in path_item.values():
            if isinstance(operation, dict) and operation.get("operationId"):
                assert operation["operationId"] in generated_client
