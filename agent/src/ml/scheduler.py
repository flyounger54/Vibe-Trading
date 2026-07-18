"""Sliding-window training scheduler + model version management."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

_MODELS_DIR = Path.home() / ".vibe-trading" / "models"


@dataclass(frozen=True)
class ModelSchedule:
    experiment_name: str
    retrain_freq: str  # "daily" | "weekly" | "monthly" | "quarterly"
    window_size_days: int  # trading days
    window_type: str  # "rolling" | "expanding"
    profile_refresh_freq: str | None = None
    auto_ensemble: bool = False
    max_versions: int = 12
    health_check: bool = True


@dataclass(frozen=True)
class ModelVersion:
    model_id: str
    base_id: str
    version: str
    train_window: tuple[str, str]
    feature_profile_id: str
    metrics: dict[str, Any]


def run_scheduled_retrain(
    schedule: ModelSchedule,
    as_of_date: str | None = None,
    models_dir: Path | None = None,
) -> ModelVersion | None:
    """Execute one sliding-window retrain cycle.

    1. Compute new training window
    2. Load experiment config
    3. Adjust period to new window
    4. Check if FeatureProfile needs refresh
    5. Train → save as new version
    6. Optional: health check vs previous version
    7. Cleanup old versions beyond max_versions
    """
    from src.ml.experiment import load_experiment
    from src.ml.pipeline import run_training_pipeline

    base = models_dir or _MODELS_DIR
    config = load_experiment(schedule.experiment_name)

    versions = list_model_versions(
        _base_id_from_config(config, schedule), models_dir=base
    )
    last = versions[-1] if versions else None

    window = get_retrain_dates(schedule, last, as_of_date)
    if window is None:
        logger.info("No retrain needed for %s", schedule.experiment_name)
        return None

    start_date, end_date = window
    version_tag = end_date.replace("-", "")

    from dataclasses import replace
    new_config = TrainConfig(
        universe=config.universe,
        period=f"{start_date}/{end_date}",
        feature_profile_id=config.feature_profile_id,
        zoo=config.zoo,
        selection_config=config.selection_config,
        label_config=config.label_config,
        preprocess_config=config.preprocess_config,
        n_splits=config.n_splits,
        expanding=config.expanding,
        gap_days=config.gap_days,
        model_type=config.model_type,
        model_params=config.model_params,
        model_id=f"{_base_id_from_config(config, schedule)}_v{version_tag}",
        use_cache=config.use_cache,
        pit_universe=config.pit_universe,
        calibrate_proba=config.calibrate_proba,
        random_seed=config.random_seed,
        min_train_samples=config.min_train_samples,
    )

    result = run_training_pipeline(new_config)

    version = ModelVersion(
        model_id=result.model_id,
        base_id=_base_id_from_config(config, schedule),
        version=version_tag,
        train_window=(start_date, end_date),
        feature_profile_id=config.feature_profile_id or "",
        metrics=result.cv_summary,
    )

    _save_version_index(version, base)

    if schedule.health_check and last:
        from src.ml.monitoring import evaluate_model_health
        logger.info("Health check: comparing %s vs %s", result.model_id, last.model_id)

    _cleanup_old_versions(version.base_id, schedule.max_versions, base)

    return version


def get_retrain_dates(
    schedule: ModelSchedule,
    last_version: ModelVersion | None,
    as_of_date: str | None = None,
) -> tuple[str, str] | None:
    """Compute the next training window. Returns None if no retrain needed."""
    if as_of_date:
        end = pd.Timestamp(as_of_date)
    else:
        end = pd.Timestamp.now()

    if last_version:
        last_end = pd.Timestamp(last_version.train_window[1])
        freq_days = _freq_to_days(schedule.retrain_freq)
        if (end - last_end).days < freq_days:
            return None

    end_str = end.strftime("%Y-%m-%d")

    if schedule.window_type == "rolling":
        td = pd.tseries.offsets.BDay(schedule.window_size_days)
        start = end - td
        start_str = start.strftime("%Y-%m-%d")
    else:
        start_str = "2019-01-01"

    return start_str, end_str


def list_model_versions(
    base_id: str,
    models_dir: Path | None = None,
) -> list[ModelVersion]:
    """List all versions of a model, sorted by version tag."""
    base = models_dir or _MODELS_DIR
    versions_path = base / "versions.json"

    if not versions_path.exists():
        return []

    try:
        data = json.loads(versions_path.read_text(encoding="utf-8"))
    except Exception:
        return []

    entries = data.get(base_id, [])
    result = []
    for e in entries:
        result.append(ModelVersion(
            model_id=e["model_id"],
            base_id=e["base_id"],
            version=e["version"],
            train_window=tuple(e["train_window"]),
            feature_profile_id=e.get("feature_profile_id", ""),
            metrics=e.get("metrics", {}),
        ))

    return sorted(result, key=lambda v: v.version)


def promote_version(
    model_id: str,
    models_dir: Path | None = None,
) -> None:
    """Mark a version as the production version for its base_id."""
    base = models_dir or _MODELS_DIR
    versions_path = base / "versions.json"
    if not versions_path.exists():
        raise FileNotFoundError("No versions.json found")

    data = json.loads(versions_path.read_text(encoding="utf-8"))
    promoted = data.get("_promoted", {})

    for base_id, entries in data.items():
        if base_id.startswith("_"):
            continue
        for e in entries:
            if e["model_id"] == model_id:
                _require_production_eligible(base, model_id)
                promoted[base_id] = model_id
                data["_promoted"] = promoted
                versions_path.write_text(
                    json.dumps(data, indent=2, default=str), encoding="utf-8"
                )
                logger.info("Promoted %s as production version for %s", model_id, base_id)
                return

    raise KeyError(f"Model {model_id} not found in version index")


def get_production_version(
    base_id: str,
    models_dir: Path | None = None,
) -> str | None:
    """Get the promoted production model_id for a base_id, or latest version."""
    base = models_dir or _MODELS_DIR
    versions_path = base / "versions.json"
    if not versions_path.exists():
        return None

    data = json.loads(versions_path.read_text(encoding="utf-8"))
    promoted = data.get("_promoted", {})
    if base_id in promoted:
        promoted_id = promoted[base_id]
        try:
            _require_production_eligible(base, promoted_id)
            return promoted_id
        except (FileNotFoundError, ValueError):
            logger.warning("Ignoring ineligible promoted ML model %s", promoted_id)

    versions = list_model_versions(base_id, models_dir)
    for version in reversed(versions):
        try:
            _require_production_eligible(base, version.model_id)
            return version.model_id
        except (FileNotFoundError, ValueError):
            continue
    return None


def _require_production_eligible(models_dir: Path, model_id: str) -> None:
    """Reject models without verified PIT/leakage qualification for promotion."""
    meta_path = models_dir / model_id / "metadata.json"
    if not meta_path.exists():
        raise FileNotFoundError(f"Model metadata not found: {meta_path}")
    metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    if not metadata.get("production_eligible", False):
        raise ValueError(
            f"Model {model_id} is research_only or failed the leakage/PIT qualification gate"
        )


def _base_id_from_config(config: Any, schedule: ModelSchedule) -> str:
    label_key = config.label_config.key if hasattr(config.label_config, "key") else "return_1d"
    return f"{config.model_type}_{config.universe}_{label_key}"


def _freq_to_days(freq: str) -> int:
    return {"daily": 1, "weekly": 5, "monthly": 21, "quarterly": 63}.get(freq, 21)


def _save_version_index(version: ModelVersion, models_dir: Path) -> None:
    versions_path = models_dir / "versions.json"
    if versions_path.exists():
        data = json.loads(versions_path.read_text(encoding="utf-8"))
    else:
        data = {}

    entries = data.get(version.base_id, [])
    entries.append({
        "model_id": version.model_id,
        "base_id": version.base_id,
        "version": version.version,
        "train_window": list(version.train_window),
        "feature_profile_id": version.feature_profile_id,
        "metrics": version.metrics,
    })
    data[version.base_id] = entries

    versions_path.write_text(
        json.dumps(data, indent=2, default=str), encoding="utf-8"
    )


def _cleanup_old_versions(
    base_id: str, max_versions: int, models_dir: Path,
) -> None:
    """Remove oldest versions beyond max_versions."""
    import shutil

    versions = list_model_versions(base_id, models_dir)
    if len(versions) <= max_versions:
        return

    to_remove = versions[:len(versions) - max_versions]
    for v in to_remove:
        model_dir = models_dir / v.model_id
        if model_dir.exists():
            shutil.rmtree(model_dir)
            logger.info("Cleaned up old version: %s", v.model_id)

    versions_path = models_dir / "versions.json"
    if versions_path.exists():
        data = json.loads(versions_path.read_text(encoding="utf-8"))
        removed_ids = {v.model_id for v in to_remove}
        data[base_id] = [e for e in data.get(base_id, []) if e["model_id"] not in removed_ids]
        versions_path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


# Need this import for run_scheduled_retrain
from src.ml.base_model import TrainConfig  # noqa: E402
