"""Focused tests for the deterministic ``vibe-trading doctor`` checks."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from cli.doctor import evaluate_module_origin
from cli.main import main as cli_main


def test_module_origin_accepts_current_workspace(tmp_path: Path) -> None:
    origin = tmp_path / "agent" / "cli" / "__init__.py"

    check = evaluate_module_origin("cli", origin, tmp_path)

    assert check.status == "pass"
    assert check.details["expected_root"] == str((tmp_path / "agent").resolve())


def test_module_origin_rejects_old_site_packages_override(tmp_path: Path) -> None:
    origin = tmp_path / ".venv" / "lib" / "python3.12" / "site-packages" / "cli" / "__init__.py"
    workspace = tmp_path / "checkout"

    check = evaluate_module_origin("cli", origin, workspace)

    assert check.status == "fail"
    assert check.details["risk"] == "old-site-packages-overrides-workspace"
    assert "old installed package" in check.summary


def test_doctor_subcommand_dispatches_json_without_starting_chat(capsys) -> None:
    report = {
        "ok": True,
        "workspace": "/workspace/Vibe-Trading",
        "summary": {"passed": 1, "warned": 0, "failed": 0},
        "checks": [],
    }

    with patch("cli.doctor.build_report", return_value=report):
        result = cli_main(["doctor", "--json"])

    assert result == 0
    assert json.loads(capsys.readouterr().out) == report


def test_provider_probe_is_configuration_only() -> None:
    from cli.doctor import _provider_checks

    checks = _provider_checks()

    assert {check.name for check in checks} == {
        "provider.astock",
        "provider.global",
        "provider.tushare",
        "provider.okx",
        "provider.ccxt",
        "provider.local",
    }
    assert all(check.details["network_checked"] is False for check in checks)
