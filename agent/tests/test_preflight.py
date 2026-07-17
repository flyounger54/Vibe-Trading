"""Tests for startup preflight checks."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from src import preflight


def test_global_stock_check_reports_ready_without_parsing_payload() -> None:
    response = MagicMock(status_code=200)
    with patch("requests.get", return_value=response):
        result = preflight._check_global_stock()
    assert result.status == "ready"
    assert result.message == "Yahoo Finance reachable"


def test_global_stock_check_reports_network_failure() -> None:
    with patch("requests.get", side_effect=RuntimeError("offline")):
        result = preflight._check_global_stock()
    assert result.status == "error"
    assert "offline" in result.message
