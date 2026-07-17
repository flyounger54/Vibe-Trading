"""Node 3 regression tests for identifier, root, SSRF, and upload boundaries."""

from __future__ import annotations

import socket

import pytest
from fastapi.testclient import TestClient

import api_server
from src.industry_chain.store import IndustryChainStore
from src.security.boundaries import resolve_within_root, validate_identifier, validate_outbound_url


@pytest.mark.parametrize("kind", ["model_id", "chain_id", "run_id", "artifact_id", "strategy_id"])
@pytest.mark.parametrize("value", ["../escape", "..", "a/b", "a\\b", "name%2fescape", "x\x00y"])
def test_all_persisted_identifiers_reject_path_shapes(kind: str, value: str) -> None:
    with pytest.raises(ValueError):
        validate_identifier(value, kind)


def test_resolve_within_root_rejects_symlink_escape(tmp_path) -> None:
    root = tmp_path / "models"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "evil_model").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="escapes"):
        resolve_within_root(root, "evil_model", kind="model_id")


def test_industry_chain_store_rejects_traversal_before_io(tmp_path) -> None:
    store = IndustryChainStore(root=tmp_path / "chains")
    with pytest.raises(ValueError, match="chain_id"):
        store.get_chain("../outside")


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8080/v1",
        "http://169.254.169.254/latest/meta-data",
        "https://10.0.0.8/v1",
        "file:///etc/passwd",
        "https://user:pass@example.com/v1",
    ],
)
def test_provider_url_blocks_ssrf_and_credentials(url: str) -> None:
    with pytest.raises(ValueError):
        validate_outbound_url(url, resolve_dns=False)


def test_provider_url_blocks_dns_rebinding_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    def rebound(*args, **kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", rebound)
    with pytest.raises(ValueError, match="public"):
        validate_outbound_url("https://rebind.example/v1")


def test_upload_rejects_executable_magic_disguised_as_text(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(api_server, "UPLOADS_DIR", tmp_path)
    monkeypatch.setenv("API_AUTH_KEY", "upload-test-key")
    with TestClient(api_server.app) as client:
        response = client.post(
            "/upload",
            headers={"Authorization": "Bearer upload-test-key"},
            files={"file": ("report.txt", b"MZ" + b"\x00" * 40, "text/plain")},
        )
    assert response.status_code == 400
    assert not list(tmp_path.iterdir())
