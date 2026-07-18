"""Node 3 acceptance tests for the unified HTTP security boundary."""

from __future__ import annotations

import stat

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

import api_server
from src.security.api_security import idempotency_registry, rate_limiter, redact_log_text


@pytest.fixture()
def security_env(monkeypatch: pytest.MonkeyPatch, tmp_path):
    key_path = tmp_path / "security" / "api.key"
    audit_path = tmp_path / "security" / "audit.jsonl"
    monkeypatch.delenv("API_AUTH_KEY", raising=False)
    monkeypatch.setenv("VIBE_TRADING_API_KEY_PATH", str(key_path))
    monkeypatch.setenv("VIBE_TRADING_AUDIT_LOG_PATH", str(audit_path))
    monkeypatch.setenv("VIBE_TRADING_RATE_LIMIT_PER_MINUTE", "1000")
    monkeypatch.setattr(api_server, "_API_KEY", "")
    rate_limiter.clear()
    idempotency_registry.clear()
    yield key_path, audit_path


def _local_client() -> TestClient:
    return TestClient(api_server.app, client=("127.0.0.1", 50000))


def test_first_start_generates_private_256_bit_key(security_env) -> None:
    key_path, _ = security_env

    key = api_server._configured_api_key()

    assert len(key) >= 43
    assert key_path.read_text(encoding="utf-8").strip() == key
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(key_path.parent.stat().st_mode) == 0o700


def test_only_liveness_endpoints_are_anonymous(security_env) -> None:
    with _local_client() as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/readyz").status_code == 200
        for path in ("/health", "/runs", "/industry-chain/list", "/docs", "/openapi.json"):
            assert client.get(path).status_code == 401, path


def test_html_accept_header_cannot_bypass_api_auth(
    security_env, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    frontend_dist = tmp_path / "frontend-dist"
    frontend_dist.mkdir()
    (frontend_dist / "index.html").write_text(
        "<!doctype html><html><body>Vibe-Trading</body></html>",
        encoding="utf-8",
    )
    monkeypatch.setattr(api_server, "_FRONTEND_DIST", frontend_dist)

    with _local_client() as client:
        for path in ("/health", "/runs", "/industry-chain/list", "/api"):
            response = client.get(path, headers={"Accept": "text/html"})
            assert response.status_code == 401, path
        spa = client.get("/correlation", headers={"Accept": "text/html"})
    assert spa.status_code == 200
    assert "text/html" in spa.headers["content-type"]


def test_loopback_requires_bearer_and_correct_bearer_succeeds(security_env) -> None:
    key = api_server._configured_api_key()
    with _local_client() as client:
        assert client.get("/runs").status_code == 401
        response = client.get("/runs", headers={"Authorization": f"Bearer {key}"})
    assert response.status_code == 200


def test_versioned_security_rejections_use_stable_error_envelope(security_env) -> None:
    with _local_client() as client:
        response = client.get("/api/v1/runs")

    assert response.status_code == 401
    assert response.json() == {
        "code": "unauthorized",
        "message": "Invalid or missing API key",
        "request_id": response.headers["x-request-id"],
        "retryable": False,
        "details": None,
    }
    assert response.headers["www-authenticate"] == "Bearer"


def test_query_api_key_is_rejected_and_never_authenticates_sse(security_env) -> None:
    key = api_server._configured_api_key()
    with _local_client() as client:
        response = client.get(f"/sessions/missing/events?api_key={key}")
    assert response.status_code == 400
    assert key not in response.text


def test_security_headers_and_request_id_are_applied(security_env) -> None:
    with _local_client() as client:
        response = client.get("/healthz")
    assert response.headers["x-request-id"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "default-src" in response.headers["content-security-policy"]


def test_permission_matrix_classifies_every_api_route(security_env) -> None:
    routes = [route for route in api_server.app.routes if isinstance(route, APIRoute)]
    matrix = api_server.build_api_permission_matrix()

    assert len(matrix) == len(routes)
    anonymous = {
        (method, row["path"])
        for row in matrix
        for method in row["methods"]
        if row["access"] == "anonymous"
    }
    assert anonymous == {("GET", "/healthz"), ("HEAD", "/healthz"), ("GET", "/readyz"), ("HEAD", "/readyz")}
    assert all(row["access"] in {"anonymous", "bearer"} for row in matrix)


def test_audit_log_does_not_persist_api_key(security_env) -> None:
    key_path, audit_path = security_env
    key = api_server._configured_api_key()
    with _local_client() as client:
        client.get("/runs", headers={"Authorization": f"Bearer {key}"})

    content = audit_path.read_text(encoding="utf-8")
    assert key_path.exists()
    assert key not in content
    assert "authorization" not in content.lower()


def test_rate_limit_returns_429_with_retry_after(security_env, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VIBE_TRADING_RATE_LIMIT_PER_MINUTE", "2")
    rate_limiter.clear()
    with _local_client() as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/healthz").status_code == 200
        response = client.get("/healthz")
    assert response.status_code == 429
    assert int(response.headers["retry-after"]) >= 1


def test_duplicate_post_with_same_idempotency_key_is_blocked(security_env) -> None:
    key = api_server._configured_api_key()
    headers = {
        "Authorization": f"Bearer {key}",
        "Idempotency-Key": "create-session-0001",
    }
    with _local_client() as client:
        first = client.post("/sessions", headers=headers, json={})
        duplicate = client.post("/sessions", headers=headers, json={})
    assert first.status_code in {201, 501}
    assert duplicate.status_code == 409
    assert duplicate.headers["idempotency-replayed"] == "true"


def test_log_redaction_removes_query_and_bearer_secrets() -> None:
    rendered = redact_log_text(
        "GET /events?api_key=super-secret&x=1 Authorization: Bearer another-secret"
    )
    assert "super-secret" not in rendered
    assert "another-secret" not in rendered
    assert rendered.count("[redacted]") == 2
