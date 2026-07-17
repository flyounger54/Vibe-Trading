"""Node 3 acceptance tests for generated-strategy execution isolation."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from src.core.runner import Runner


ENTRY_SCRIPT = Path(__file__).resolve().parents[1] / "backtest" / "runner.py"


def test_container_command_has_mandatory_isolation_controls(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr("src.core.runner.shutil.which", lambda name: "/usr/bin/docker")
    runner = Runner(timeout=10)

    command = runner._container_command(ENTRY_SCRIPT, tmp_path, [str(tmp_path)])
    joined = " ".join(command)

    assert "--network=none" in command
    assert "--read-only" in command
    assert "--cap-drop=ALL" in command
    assert "--security-opt=no-new-privileges" in command
    assert "--pids-limit=128" in command
    assert "--memory=1g" in command
    assert "--cpus=1.0" in command
    assert "--user=" in joined
    assert "--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=64m" in command
    assert f"src={tmp_path.resolve()},dst=/workspace/run" in joined
    assert ",rw" not in joined
    assert str(tmp_path.resolve()) not in command[-1]


def test_missing_container_fails_closed_without_starting_process(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.delenv("VIBE_TRADING_EXECUTION_MODE", raising=False)
    monkeypatch.setattr("src.core.runner.shutil.which", lambda name: None)
    monkeypatch.setattr(
        "src.core.runner.subprocess.Popen",
        lambda *args, **kwargs: pytest.fail("host process must not start"),
    )

    result = Runner(timeout=1).execute(ENTRY_SCRIPT, tmp_path, cli_args=[str(tmp_path)])

    assert not result.success
    assert result.exit_code == 126
    assert "not executed" in result.stderr


def test_malicious_generated_strategy_cannot_run_on_host_without_sandbox(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    run_dir = tmp_path / "malicious"
    (run_dir / "code").mkdir(parents=True)
    marker = tmp_path / "host-compromised"
    (run_dir / "code" / "signal_engine.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('owned')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("src.core.runner.shutil.which", lambda name: None)
    monkeypatch.delenv("VIBE_TRADING_EXECUTION_MODE", raising=False)

    result = Runner(timeout=1).execute(ENTRY_SCRIPT, run_dir, cli_args=[str(run_dir)])

    assert result.exit_code == 126
    assert not marker.exists()


def test_dangerous_local_mode_requires_explicit_value(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setenv("VIBE_TRADING_EXECUTION_MODE", "dangerous-local")
    monkeypatch.setattr(Runner, "_pick_python_interpreter", lambda self: sys.executable)

    def fake_run(self, cmd, *, cwd, env):
        captured.update(cmd=cmd, cwd=cwd)
        return 0, "ok", ""

    monkeypatch.setattr(Runner, "_run_capped", fake_run)
    result = Runner(timeout=1).execute(ENTRY_SCRIPT, tmp_path, cli_args=[str(tmp_path)])

    assert result.success
    assert captured["cmd"][0] == sys.executable
    assert captured["cwd"] == ENTRY_SCRIPT.parent


def test_output_is_continuously_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VIBE_TRADING_MAX_RUN_OUTPUT_BYTES", "1024")
    runner = Runner(timeout=5)

    code, stdout, stderr = runner._run_capped(
        [sys.executable, "-c", "import sys; print('x'*100000); print('y'*100000, file=sys.stderr)"],
        cwd=None,
        env=None,
    )

    assert code == 0
    assert len(stdout.encode()) <= 1024
    assert len(stderr.encode()) <= 1024
