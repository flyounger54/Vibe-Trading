"""Deterministic run manifest for reproducible backtest evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from backtest.data_bundle import DataBundle


SCHEMA_VERSION = "run-manifest.v1"
_FEE_TERMS = (
    "commission", "fee", "slippage", "spread", "stamp", "levy",
    "funding", "swap", "margin", "leverage", "multiplier", "borrow",
)


def _bytes_hash(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _json_hash(value: object) -> str:
    return _bytes_hash(
        json.dumps(
            value, sort_keys=True, default=str, separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    )


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tree_hash(paths: list[Path], root: Path) -> str:
    entries = {
        path.relative_to(root).as_posix(): _file_hash(path)
        for path in sorted(paths)
        if path.is_file()
    }
    return _json_hash(entries)


def _public_config(config: Mapping[str, Any]) -> dict[str, Any]:
    return {
        str(key): value
        for key, value in config.items()
        if not str(key).startswith("_")
    }


def _fee_model_config(config: Mapping[str, Any]) -> dict[str, Any]:
    return {
        str(key): value
        for key, value in config.items()
        if any(term in str(key).lower() for term in _FEE_TERMS)
    }


def write_run_manifest(
    run_dir: Path,
    config: Mapping[str, Any],
    data_bundle: DataBundle,
    *,
    strategy_path: Path | None = None,
) -> dict[str, Any]:
    """Write a stable, content-addressed manifest and return its payload.

    Volatile values such as timestamps and absolute run paths are deliberately
    excluded. Two runs using identical inputs and code therefore produce the
    same ``run_hash`` even when written to different directories.
    """
    run_dir = Path(run_dir)
    repo_root = Path(__file__).resolve().parents[2]

    strategy_hash = ""
    if strategy_path is not None and Path(strategy_path).is_file():
        strategy_hash = _file_hash(Path(strategy_path))

    dependency_files = [
        path for path in (
            repo_root / "pyproject.toml",
            repo_root / "requirements.lock",
        )
        if path.is_file()
    ]
    execution_files = list((repo_root / "agent" / "backtest" / "engines").glob("*.py"))
    execution_files.extend([
        repo_root / "agent" / "backtest" / "models.py",
        repo_root / "agent" / "backtest" / "metrics.py",
    ])

    artifacts_dir = run_dir / "artifacts"
    artifact_hashes = {
        path.relative_to(run_dir).as_posix(): _file_hash(path)
        for path in sorted(artifacts_dir.rglob("*"))
        if path.is_file()
    } if artifacts_dir.is_dir() else {}

    components = {
        "data_hash": data_bundle.fingerprint,
        "config_hash": _json_hash(_public_config(config)),
        "strategy_hash": strategy_hash,
        "fee_model_hash": _json_hash(_fee_model_config(config)),
        "execution_model_hash": _tree_hash(execution_files, repo_root),
        "dependency_hash": _tree_hash(dependency_files, repo_root),
        "artifact_hash": _json_hash(artifact_hashes),
    }
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "base_currency": data_bundle.base_currency,
        "components": components,
        "artifacts": artifact_hashes,
    }
    payload["run_hash"] = _json_hash(payload)
    (run_dir / "run_manifest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload
