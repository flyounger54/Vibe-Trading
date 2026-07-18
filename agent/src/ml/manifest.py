"""Immutable provenance manifest for a trained ML model artifact."""

from __future__ import annotations

import importlib.metadata
import json
import platform
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ModelManifest:
    """All inputs required to reproduce, audit and qualify a model run."""

    model_id: str
    data: dict[str, Any]
    features: dict[str, Any]
    preprocessing: dict[str, Any]
    split: dict[str, Any]
    random_seed: int
    dependencies: dict[str, str]
    metrics: dict[str, Any]
    qualification: dict[str, Any]
    created_at: str
    schema_version: str = "model-manifest-v1"

    @classmethod
    def runtime_dependencies(cls) -> dict[str, str]:
        packages = ("numpy", "pandas", "scipy", "scikit-learn", "duckdb", "lightgbm", "xgboost")
        versions = {"python": sys.version.split()[0], "platform": platform.platform()}
        for package in packages:
            try:
                versions[package] = importlib.metadata.version(package)
            except importlib.metadata.PackageNotFoundError:
                versions[package] = "not-installed"
        return versions

    @classmethod
    def create(
        cls,
        *,
        model_id: str,
        data: dict[str, Any],
        features: dict[str, Any],
        preprocessing: dict[str, Any],
        split: dict[str, Any],
        random_seed: int,
        metrics: dict[str, Any],
        qualification: dict[str, Any],
    ) -> ModelManifest:
        return cls(
            model_id=model_id,
            data=data,
            features=features,
            preprocessing=preprocessing,
            split=split,
            random_seed=random_seed,
            dependencies=cls.runtime_dependencies(),
            metrics=metrics,
            qualification=qualification,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def save(self, model_dir: Path) -> Path:
        path = model_dir / "model_manifest.json"
        path.write_text(
            json.dumps(self.to_dict(), indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        return path
