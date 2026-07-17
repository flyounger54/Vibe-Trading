"""Shared fixtures and sys.path setup for all tests."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Ensure agent/ is on sys.path so imports like `backtest.*` and `src.*` work.
AGENT_DIR = Path(__file__).resolve().parent.parent
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))


@pytest.fixture(scope="session", autouse=True)
def isolate_unified_state_database(tmp_path_factory: pytest.TempPathFactory):
    """Prevent tests using production defaults from touching user state."""
    root = tmp_path_factory.mktemp("vibe-state")
    configured = {
        "VIBE_TRADING_STATE_DB_PATH": root / "vibe.db",
        "VIBE_TRADING_API_KEY_PATH": root / "security" / "api.key",
        "VIBE_TRADING_AUDIT_LOG_PATH": root / "security" / "audit.jsonl",
    }
    previous = {key: os.environ.get(key) for key in configured}
    for key, value in configured.items():
        os.environ[key] = str(value)
    try:
        yield configured["VIBE_TRADING_STATE_DB_PATH"]
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@pytest.fixture(autouse=True)
def authenticated_test_client_defaults(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch):
    """Authenticate ordinary API tests while leaving auth-boundary tests explicit."""
    filename = Path(str(request.node.fspath)).name
    if filename in {"test_security_auth_api.py", "test_node3_api_security.py"}:
        yield
        return

    test_key = "vibe-test-suite-api-key"
    monkeypatch.setenv("API_AUTH_KEY", test_key)
    original_init = TestClient.__init__

    def authenticated_init(self, *args, **kwargs):
        headers = dict(kwargs.pop("headers", {}) or {})
        headers.setdefault("Authorization", f"Bearer {test_key}")
        kwargs["headers"] = headers
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(TestClient, "__init__", authenticated_init)
    yield
