"""Runner module for executing generated backtest code and collecting artifacts."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from rich.console import Console

from src.observability import record_backtest_duration


console = Console(stderr=True)


@dataclass
class RunResult:
    """Container for runner execution outputs.

    Attributes:
        success: Whether subprocess exited with code 0.
        exit_code: Subprocess return code.
        stdout: Captured stdout text.
        stderr: Captured stderr text.
        artifacts: Existing artifact file paths keyed by artifact name.
    """

    success: bool
    exit_code: int
    stdout: str
    stderr: str
    artifacts: dict[str, Path]


_ARTIFACTS_SPEC = {
    "defaults": {"required": ["equity", "metrics", "trades"]},
    "schemas": {
        "equity_csv": {
            "columns": [
                {"name": "timestamp", "type": "string"},
                {"name": "ret", "type": "float"},
                {"name": "equity", "type": "float"},
                {"name": "drawdown", "type": "float"},
            ],
        },
        "metrics_csv": {
            "columns": [
                {"name": "final_value", "type": "float"},
                {"name": "total_return", "type": "float"},
                {"name": "annual_return", "type": "float"},
                {"name": "max_drawdown", "type": "float"},
                {"name": "sharpe", "type": "float"},
                {"name": "win_rate", "type": "float"},
                {"name": "trade_count", "type": "integer"},
            ],
        },
        "trade_log": {
            "columns": [
                {"name": "timestamp", "type": "string"},
                {"name": "code", "type": "string"},
                {"name": "side", "type": "string"},
                {"name": "price", "type": "float"},
                {"name": "qty", "type": "float"},
                {"name": "reason", "type": "string"},
            ],
        },
    },
    "artifacts": {
        "equity": {"schema": "equity_csv", "path": "artifacts/equity.csv"},
        "metrics": {"schema": "metrics_csv", "path": "artifacts/metrics.csv"},
        "trades": {"schema": "trade_log", "path": "artifacts/trades.csv"},
        "positions": {"schema": "positions_csv", "path": "artifacts/positions.csv"},
        "run_card_json": {"schema": "json", "path": "run_card.json"},
        "run_card_md": {"schema": "markdown", "path": "run_card.md"},
    },
}


def _expand_artifacts_spec(spec: Dict[str, Any] | None) -> Dict[str, Dict[str, Any]]:
    """Expand artifacts_spec into a name -> metadata dict.

    Args:
        spec: Raw artifact spec.

    Returns:
        Expanded artifact metadata mapping.
    """
    if not isinstance(spec, dict):
        return {}
    schemas = spec.get("schemas") or {}
    artifacts = spec.get("artifacts") or {}
    defaults = spec.get("defaults") or {}
    required = set(defaults.get("required") or [])
    expanded: Dict[str, Dict[str, Any]] = {}
    for name, meta in artifacts.items():
        if not isinstance(meta, dict):
            continue
        schema_name = meta.get("schema")
        schema = schemas.get(schema_name, {}) if isinstance(schemas, dict) else {}
        expanded[name] = {
            "path": meta.get("path"),
            "required": bool(meta.get("required", name in required)),
            "columns": meta.get("columns") or schema.get("columns"),
        }
    return expanded


class Runner:
    """Execute entry scripts inside a run directory and collect outputs."""

    def __init__(self, timeout: int = 300, artifacts_spec: Optional[Dict[str, Any]] = None) -> None:
        """Initialize runner.

        Args:
            timeout: Max subprocess runtime in seconds.
            artifacts_spec: Artifact spec from config.
        """

        self.timeout = timeout
        self.artifacts_spec = artifacts_spec or _ARTIFACTS_SPEC
        self.artifact_entries = _expand_artifacts_spec(self.artifacts_spec)
        self.max_output_bytes = max(
            1024,
            min(int(os.getenv("VIBE_TRADING_MAX_RUN_OUTPUT_BYTES", "1048576")), 16 * 1024 * 1024),
        )

    @staticmethod
    def _execution_mode() -> str:
        mode = os.getenv("VIBE_TRADING_EXECUTION_MODE", "container").strip().lower()
        if mode not in {"container", "dangerous-local"}:
            return "container"
        return mode

    @staticmethod
    def _sandbox_image() -> str:
        return os.getenv("VIBE_TRADING_SANDBOX_IMAGE", "vibe-trading-sandbox:local").strip() or "vibe-trading-sandbox:local"

    def _container_command(
        self,
        entry_script: Path,
        run_dir: Path,
        cli_args: list[str] | None,
    ) -> list[str]:
        docker = shutil.which("docker")
        if not docker:
            raise RuntimeError(
                "Container sandbox is unavailable; generated strategies were not executed. "
                "Install Docker or explicitly set VIBE_TRADING_EXECUTION_MODE=dangerous-local."
            )
        repo_root = Path(__file__).resolve().parents[3]
        try:
            entry_rel = entry_script.resolve().relative_to(repo_root)
        except ValueError as exc:
            raise RuntimeError("Sandbox entry script must belong to the Vibe-Trading project") from exc
        uid = os.getuid() if hasattr(os, "getuid") else 65534
        gid = os.getgid() if hasattr(os, "getgid") else 65534
        if uid == 0:
            uid = gid = 65534
        resolved_run = run_dir.resolve()
        mapped_args: list[str] = []
        for arg in cli_args or []:
            try:
                rel = Path(arg).resolve().relative_to(resolved_run)
            except (OSError, ValueError):
                mapped_args.append(arg)
            else:
                mapped_args.append(str(Path("/workspace/run") / rel))
        return [
            docker,
            "run",
            "--rm",
            "--network=none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--pids-limit=128",
            "--memory=1g",
            "--memory-swap=1g",
            "--cpus=1.0",
            "--ulimit=nofile=256:256",
            "--ulimit=nproc=128:128",
            f"--user={uid}:{gid}",
            "--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=64m",
            f"--mount=type=bind,src={resolved_run},dst=/workspace/run",
            "--workdir=/app/agent",
            self._sandbox_image(),
            "python",
            str(Path("/app") / entry_rel),
            *mapped_args,
        ]

    def _run_capped(
        self,
        cmd: list[str],
        *,
        cwd: Path | None,
        env: dict[str, str] | None,
    ) -> tuple[int, str, str]:
        """Run a worker while continuously draining and capping both outputs."""
        process = subprocess.Popen(
            cmd,
            cwd=str(cwd) if cwd is not None else None,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        captured: dict[str, bytearray] = {"stdout": bytearray(), "stderr": bytearray()}

        def drain(name: str, stream) -> None:
            while True:
                chunk = stream.read(8192)
                if not chunk:
                    return
                remaining = self.max_output_bytes - len(captured[name])
                if remaining > 0:
                    captured[name].extend(chunk[:remaining])

        threads = [
            threading.Thread(target=drain, args=("stdout", process.stdout), daemon=True),
            threading.Thread(target=drain, args=("stderr", process.stderr), daemon=True),
        ]
        for thread in threads:
            thread.start()
        try:
            exit_code = process.wait(timeout=self.timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            exit_code = 124
            captured["stderr"].extend(b"\nExecution timed out and was terminated.")
        for thread in threads:
            thread.join(timeout=5)
        return (
            exit_code,
            captured["stdout"].decode("utf-8", errors="replace"),
            captured["stderr"].decode("utf-8", errors="replace"),
        )

    def _python_ready(self, python_cmd: str) -> bool:
        """Check whether a Python interpreter can import runtime dependencies.

        Args:
            python_cmd: Interpreter executable path.

        Returns:
            True if required imports succeed, otherwise False.
        """

        try:
            probe = subprocess.run(
                [python_cmd, "-c", "import pandas,numpy; print('ok')"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=20,
            )
            return probe.returncode == 0
        except Exception:
            return False

    def _pick_python_interpreter(self) -> str:
        """Pick the first usable interpreter for backtest execution.

        Returns:
            Interpreter command path.
        """

        project_root = Path(__file__).resolve().parents[2]
        candidates = [
            project_root / ".venv" / "Scripts" / "python.exe",
            project_root / ".venv" / "bin" / "python",
            Path(sys.executable),
        ]
        for path in candidates:
            if not path.exists():
                continue
            cmd = str(path)
            if self._python_ready(cmd):
                return cmd
        return sys.executable

    def _build_runtime_env(self, run_dir: Path, *, pythonpath_extra: Path | None = None) -> dict[str, str]:
        """Build subprocess env and enforce no-proxy execution.

        Args:
            run_dir: Current run directory.
            pythonpath_extra: Additional path to prepend to PYTHONPATH.

        Returns:
            Environment mapping for subprocess.
        """

        env = os.environ.copy()
        env.update(
            {
                "PYTHONUNBUFFERED": "1",
                "PYTHONIOENCODING": "utf-8",
                "PYTHONUTF8": "1",
            }
        )

        if pythonpath_extra:
            existing = env.get("PYTHONPATH", "")
            env["PYTHONPATH"] = str(pythonpath_extra) + (os.pathsep + existing if existing else "")

        # Preserve system proxy settings; data sources (OKX/yfinance) need network access
        # NOTE: do NOT override HOME/USERPROFILE — data libraries (yfinance, akshare)
        # cache downloads under ~/; overriding HOME causes full re-download every run.

        return env

    def execute(
        self,
        entry_script: Path,
        run_dir: Path,
        *,
        cwd: Path | None = None,
        cli_args: list[str] | None = None,
    ) -> RunResult:
        """Run entry script and collect logs and artifacts.

        Args:
            entry_script: Entry script path.
            run_dir: Current run directory.
            cwd: Working directory for subprocess (default: entry_script.parent).
            cli_args: Additional CLI arguments appended to subprocess command.

        Returns:
            RunResult object with process output and discovered artifacts.
        """

        console.print(f"[blue]Runner: executing {entry_script}[/blue]")
        stdout_path = run_dir / "logs" / "runner_stdout.txt"
        stderr_path = run_dir / "logs" / "runner_stderr.txt"
        stdout_path.parent.mkdir(parents=True, exist_ok=True)

        start_time = time.time()
        console.print("[dim]Runner: starting backtest subprocess...[/dim]")

        effective_cwd = cwd or entry_script.parent
        if self._execution_mode() == "dangerous-local":
            pythonpath_extra = cwd if cwd else None
            env = self._build_runtime_env(run_dir, pythonpath_extra=pythonpath_extra)
            python_cmd = self._pick_python_interpreter()
            console.print(f"[yellow]Runner: DANGEROUS local execution: {python_cmd}[/yellow]")
            cmd = [python_cmd, str(entry_script), *(cli_args or [])]
            process_cwd: Path | None = effective_cwd
        else:
            try:
                cmd = self._container_command(entry_script, run_dir, cli_args)
            except RuntimeError as exc:
                return RunResult(False, 126, "", str(exc), {})
            env = {
                "PATH": os.environ.get("PATH", ""),
                "DOCKER_HOST": os.environ.get("DOCKER_HOST", ""),
            }
            process_cwd = None
            console.print(f"[dim]Runner: using isolated container image {self._sandbox_image()}[/dim]")

        exit_code, stdout, stderr = self._run_capped(cmd, cwd=process_cwd, env=env)

        elapsed = time.time() - start_time
        record_backtest_duration("strategy_subprocess", "success" if exit_code == 0 else "failure", elapsed)
        console.print(f"[blue]Runner: subprocess finished in {elapsed:.2f}s[/blue]")

        stdout_path.write_text(stdout, encoding="utf-8")
        stderr_path.write_text(stderr, encoding="utf-8")

        if stdout:
            console.print(f"[dim]Runner stdout:[/dim]\n{stdout}")
        if stderr:
            console.print(f"[red]Runner stderr:[/red]\n{stderr}")

        artifacts: dict[str, Path] = {}
        for name, info in self.artifact_entries.items():
            rel_path = info.get("path")
            if not isinstance(rel_path, str) or not rel_path.strip():
                continue
            target = run_dir / Path(rel_path)
            if target.exists():
                artifacts[name] = target

        success = exit_code == 0
        return RunResult(
            success=success,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            artifacts=artifacts,
        )
