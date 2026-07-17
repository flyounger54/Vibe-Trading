"""Read-only environment diagnostics for Vibe-Trading.

The doctor intentionally avoids network requests.  Provider checks only verify
that their modules, optional dependencies, credentials, and local configuration
are present.  This keeps ``vibe-trading doctor`` deterministic enough for local
and CI smoke checks.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


_PROJECT_NAME = "vibe-trading-ai"
_SUPPORTED_PYTHON = {(3, 11), (3, 12)}
_SUPPORTED_NODE_MAJOR = 20
_WORKSPACE_ENV = "VIBE_TRADING_WORKSPACE"


@dataclass(frozen=True)
class Check:
    """One machine-readable doctor result."""

    name: str
    category: str
    status: str
    summary: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _read_project_version(root: Path) -> str | None:
    try:
        payload = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        project = payload.get("project", {})
        if project.get("name") != _PROJECT_NAME:
            return None
        version = project.get("version")
        return str(version) if version else None
    except (OSError, tomllib.TOMLDecodeError):
        return None


def discover_workspace(start: Path | None = None) -> Path | None:
    """Find the checkout that should supply imports for this invocation."""

    override = os.getenv(_WORKSPACE_ENV, "").strip()
    if override:
        candidates = [Path(override).expanduser().resolve()]
    else:
        current = (start or Path.cwd()).resolve()
        candidates = [current, *current.parents]

    for candidate in candidates:
        if (
            _read_project_version(candidate) is not None
            and (candidate / "agent" / "cli").is_dir()
            and (candidate / "agent" / "src").is_dir()
            and (candidate / "agent" / "backtest").is_dir()
        ):
            return candidate
    return None


def evaluate_module_origin(
    module_name: str,
    origin: str | os.PathLike[str] | None,
    workspace_root: Path | None,
) -> Check:
    """Classify a module path, explicitly detecting workspace shadowing."""

    if origin is None:
        return Check(
            name=f"module_origin.{module_name}",
            category="imports",
            status="fail",
            summary=f"{module_name} has no filesystem origin",
        )

    resolved = Path(origin).resolve()
    details = {"origin": str(resolved)}
    if workspace_root is None:
        return Check(
            name=f"module_origin.{module_name}",
            category="imports",
            status="warn",
            summary="No workspace checkout detected; installed-package mode cannot be compared",
            details=details,
        )

    expected_root = (workspace_root / "agent").resolve()
    details["expected_root"] = str(expected_root)
    if _is_relative_to(resolved, expected_root):
        return Check(
            name=f"module_origin.{module_name}",
            category="imports",
            status="pass",
            summary=f"{module_name} is loaded from the current workspace",
            details=details,
        )

    installed_path = any(part in {"site-packages", "dist-packages"} for part in resolved.parts)
    if installed_path:
        summary = (
            f"{module_name} is loaded from site-packages; an old installed package "
            "may be overriding the current workspace"
        )
        risk = "old-site-packages-overrides-workspace"
    else:
        summary = f"{module_name} is loaded outside the current workspace"
        risk = "module-outside-workspace"
    details["risk"] = risk
    return Check(
        name=f"module_origin.{module_name}",
        category="imports",
        status="fail",
        summary=summary,
        details=details,
    )


def _python_check() -> Check:
    version = sys.version_info[:2]
    supported = version in _SUPPORTED_PYTHON
    return Check(
        name="python",
        category="runtime",
        status="pass" if supported else "fail",
        summary=(
            f"Python {platform.python_version()} is supported"
            if supported
            else f"Python {platform.python_version()} is unsupported; use Python 3.11 or 3.12"
        ),
        details={
            "executable": str(Path(sys.executable).resolve()),
            "implementation": platform.python_implementation(),
            "supported_minors": ["3.11", "3.12"],
        },
    )


def _node_check() -> Check:
    executable = shutil.which("node")
    if executable is None:
        return Check(
            name="node",
            category="runtime",
            status="fail",
            summary="Node.js is not installed; Node 20 is required",
        )
    try:
        proc = subprocess.run(
            [executable, "--version"],
            check=True,
            capture_output=True,
            text=True,
            timeout=3,
        )
        version = proc.stdout.strip().lstrip("v")
        major = int(version.split(".", 1)[0])
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        return Check(
            name="node",
            category="runtime",
            status="fail",
            summary=f"Node.js version probe failed: {type(exc).__name__}",
            details={"executable": executable},
        )
    supported = major == _SUPPORTED_NODE_MAJOR
    return Check(
        name="node",
        category="runtime",
        status="pass" if supported else "fail",
        summary=(
            f"Node.js {version} is supported"
            if supported
            else f"Node.js {version} is unsupported; use Node 20"
        ),
        details={"executable": str(Path(executable).resolve()), "required_major": 20},
    )


def _package_version_check(workspace_root: Path | None) -> Check:
    workspace_version = _read_project_version(workspace_root) if workspace_root else None
    try:
        installed_version = importlib.metadata.version(_PROJECT_NAME)
    except importlib.metadata.PackageNotFoundError:
        installed_version = None

    details = {
        "distribution": _PROJECT_NAME,
        "installed_version": installed_version,
        "workspace_version": workspace_version,
    }
    if workspace_version and installed_version and workspace_version != installed_version:
        return Check(
            name="package_version",
            category="package",
            status="fail",
            summary="Installed distribution version does not match the current workspace",
            details=details,
        )
    if installed_version:
        return Check(
            name="package_version",
            category="package",
            status="pass",
            summary=f"{_PROJECT_NAME} {installed_version}",
            details=details,
        )
    return Check(
        name="package_version",
        category="package",
        status="warn",
        summary="Distribution metadata is not installed; running from source only",
        details=details,
    )


def _module_origin_checks(workspace_root: Path | None) -> list[Check]:
    checks: list[Check] = []
    for module_name in ("cli", "src", "backtest"):
        try:
            module = importlib.import_module(module_name)
        except Exception as exc:  # noqa: BLE001 - diagnostics must capture broken imports
            checks.append(Check(
                name=f"module_origin.{module_name}",
                category="imports",
                status="fail",
                summary=f"Cannot import {module_name}: {type(exc).__name__}: {exc}",
            ))
            continue
        checks.append(evaluate_module_origin(module_name, getattr(module, "__file__", None), workspace_root))
    return checks


_OPTIONAL_DEPENDENCIES = (
    ("mootdx", "mootdx", "A-share TCP fallback", "pip install mootdx"),
    ("baostock", "baostock", "A-share BaoStock source", "pip install 'vibe-trading-ai[ashare]'"),
    ("ib_async", "ib_async", "IBKR connector", "pip install 'vibe-trading-ai[ibkr]'"),
    ("langchain_deepseek", "langchain-deepseek", "native DeepSeek adapter", "pip install 'vibe-trading-ai[deepseek]'"),
    ("lightgbm", "lightgbm", "ML training", "pip install 'vibe-trading-ai[ml]'"),
    ("xgboost", "xgboost", "ML training", "pip install 'vibe-trading-ai[ml]'"),
    ("shap", "shap", "ML explainability", "pip install 'vibe-trading-ai[ml]'"),
    ("torch", "torch", "deep-learning models", "pip install 'vibe-trading-ai[ml-deep]'"),
)


def _module_available(module_name: str) -> bool:
    if module_name in sys.modules:
        return True
    try:
        return importlib.util.find_spec(module_name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def _distribution_version(distribution_name: str) -> str | None:
    try:
        return importlib.metadata.version(distribution_name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _optional_dependency_check() -> Check:
    rows: list[dict[str, Any]] = []
    available_count = 0
    for module_name, distribution, purpose, hint in _OPTIONAL_DEPENDENCIES:
        available = _module_available(module_name)
        available_count += int(available)
        rows.append({
            "module": module_name,
            "available": available,
            "version": _distribution_version(distribution) if available else None,
            "purpose": purpose,
            "missing_reason": None if available else f"optional module {module_name} is not installed",
            "install_hint": None if available else hint,
        })
    missing_count = len(rows) - available_count
    return Check(
        name="optional_dependencies",
        category="dependencies",
        status="pass" if missing_count == 0 else "warn",
        summary=f"{available_count}/{len(rows)} key optional dependencies are installed",
        details={"dependencies": rows},
    )


_PROVIDER_MODULES = (
    ("astock", "backtest.loaders.astock_loader"),
    ("global", "backtest.loaders.global_loader"),
    ("tushare", "backtest.loaders.tushare"),
    ("okx", "backtest.loaders.okx"),
    ("ccxt", "backtest.loaders.ccxt_loader"),
    ("local", "backtest.loaders.local_loader"),
)


def _provider_missing_reason(name: str) -> str:
    if name == "tushare":
        if not _module_available("tushare"):
            return "tushare module is not installed"
        return "TUSHARE_TOKEN is not configured"
    if name == "ccxt":
        return "ccxt module is not installed"
    if name == "local":
        path = Path.home() / ".vibe-trading" / "data-bridge" / "config.yaml"
        return f"local Data Bridge config is missing or has no sources: {path}"
    return "provider prerequisites are unavailable"


def _provider_checks() -> list[Check]:
    checks: list[Check] = []
    for name, module_name in _PROVIDER_MODULES:
        try:
            module = importlib.import_module(module_name)
            loader_cls = module.DataLoader
            # All loader availability methods are configuration-only.  Bypass
            # constructors so SDK clients are never created and no provider can
            # perform network I/O during diagnostics.
            loader = object.__new__(loader_cls)
            available = bool(loader_cls.is_available(loader))
        except Exception as exc:  # noqa: BLE001 - report broken optional providers
            checks.append(Check(
                name=f"provider.{name}",
                category="providers",
                status="warn",
                summary=f"Provider cannot be loaded: {type(exc).__name__}: {exc}",
                details={"module": module_name, "network_checked": False},
            ))
            continue
        checks.append(Check(
            name=f"provider.{name}",
            category="providers",
            status="pass" if available else "warn",
            summary=(
                "Provider prerequisites are available (network not contacted)"
                if available
                else _provider_missing_reason(name)
            ),
            details={"module": module_name, "available": available, "network_checked": False},
        ))
    return checks


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _nearest_existing_parent(path: Path) -> Path:
    candidate = path
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate


def _directory_check(name: str, path: Path) -> Check:
    existing_parent = _nearest_existing_parent(path)
    writable = os.access(existing_parent, os.W_OK | os.X_OK)
    details = {
        "path": str(path),
        "exists": path.is_dir(),
        "writable": writable,
        "checked_parent": str(existing_parent),
    }
    if existing_parent.exists():
        details["mode"] = oct(_mode(existing_parent))
    return Check(
        name=name,
        category="filesystem",
        status="pass" if writable else "fail",
        summary=(
            f"{path} is writable"
            if path.is_dir() and writable
            else f"{path} can be created under writable parent {existing_parent}"
            if writable
            else f"{path} is not writable and cannot be created"
        ),
        details=details,
    )


def _config_permissions_check(workspace_root: Path | None) -> Check:
    runtime_root = Path.home() / ".vibe-trading"
    candidates = [
        runtime_root / ".env",
        runtime_root / "agent.json",
        runtime_root / "agent.yaml",
        runtime_root / "agent.yml",
        runtime_root / "data-bridge" / "config.yaml",
    ]
    if workspace_root is not None:
        candidates.extend([workspace_root / ".env", workspace_root / "agent" / ".env"])

    existing: list[dict[str, Any]] = []
    insecure: list[str] = []
    unreadable: list[str] = []
    for path in candidates:
        if not path.is_file():
            continue
        mode = _mode(path)
        readable = os.access(path, os.R_OK)
        secure = mode & 0o077 == 0
        existing.append({"path": str(path), "mode": oct(mode), "readable": readable, "private": secure})
        if not readable:
            unreadable.append(str(path))
        if not secure:
            insecure.append(str(path))

    details = {
        "files": existing,
        "checked_paths": [str(path) for path in candidates],
        "required_private_mode": "no group/world permissions (for example 0o600)",
    }
    if unreadable:
        return Check(
            name="config_permissions",
            category="filesystem",
            status="fail",
            summary=f"{len(unreadable)} configuration file(s) are not readable",
            details=details,
        )
    if insecure:
        return Check(
            name="config_permissions",
            category="filesystem",
            status="warn",
            summary=f"{len(insecure)} configuration file(s) expose group/world permissions",
            details=details,
        )
    if not existing:
        return Check(
            name="config_permissions",
            category="filesystem",
            status="warn",
            summary="No configuration files were found",
            details=details,
        )
    return Check(
        name="config_permissions",
        category="filesystem",
        status="pass",
        summary=f"{len(existing)} configuration file(s) are readable and private",
        details=details,
    )


def build_report(*, cwd: Path | None = None) -> dict[str, Any]:
    """Run every deterministic doctor check and return a JSON-safe report."""

    workspace_root = discover_workspace(cwd)
    runtime_root = Path.home() / ".vibe-trading"
    checks: list[Check] = [
        _python_check(),
        _node_check(),
        _package_version_check(workspace_root),
        *_module_origin_checks(workspace_root),
        _optional_dependency_check(),
        *_provider_checks(),
        _directory_check("runtime_directory", runtime_root),
        _directory_check("loader_cache", runtime_root / "cache" / "loaders"),
        _config_permissions_check(workspace_root),
    ]
    failed = sum(check.status == "fail" for check in checks)
    warned = sum(check.status == "warn" for check in checks)
    return {
        "ok": failed == 0,
        "workspace": str(workspace_root) if workspace_root else None,
        "summary": {"passed": len(checks) - failed - warned, "warned": warned, "failed": failed},
        "checks": [check.to_dict() for check in checks],
    }


def _render_text(report: dict[str, Any]) -> str:
    lines = ["Vibe-Trading doctor", f"Workspace: {report['workspace'] or 'not detected'}"]
    for check in report["checks"]:
        lines.append(f"[{check['status'].upper():4}] {check['name']}: {check['summary']}")
        details = check.get("details", {})
        if check["name"].startswith("module_origin.") and details.get("origin"):
            lines.append(f"       origin: {details['origin']}")
        if check["name"].startswith("provider.") and not details.get("available", True):
            lines.append(f"       module: {details.get('module', 'unknown')}")
    summary = report["summary"]
    lines.append(
        f"Result: {'PASS' if report['ok'] else 'FAIL'} "
        f"({summary['passed']} passed, {summary['warned']} warnings, {summary['failed']} failed)"
    )
    return "\n".join(lines)


def main(*, json_output: bool = False) -> int:
    """Print the doctor report and return zero only when required checks pass."""

    report = build_report()
    if json_output:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(_render_text(report))
    return 0 if report["ok"] else 1


__all__ = ["Check", "build_report", "discover_workspace", "evaluate_module_origin", "main"]
