"""Experiment configuration: YAML-based persistent training + schedule configs."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from src.ml.base_model import (
    FeatureSelectionConfig,
    LabelConfig,
    PreprocessConfig,
    TrainConfig,
)

logger = logging.getLogger(__name__)

_EXPERIMENTS_DIR = Path.home() / ".vibe-trading" / "experiments"


def load_experiment(name: str, experiments_dir: Path | None = None) -> TrainConfig:
    """Load experiment config from YAML, return TrainConfig."""
    import yaml

    base = experiments_dir or _EXPERIMENTS_DIR
    path = base / f"{name}.yaml"
    if not path.exists():
        path = base / name
        if not path.exists():
            raise FileNotFoundError(f"Experiment not found: {name}")

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return _parse_experiment(data)


def save_experiment(
    config: TrainConfig,
    name: str,
    schedule: dict[str, Any] | None = None,
    experiments_dir: Path | None = None,
) -> Path:
    """Save TrainConfig as YAML."""
    import yaml

    base = experiments_dir or _EXPERIMENTS_DIR
    base.mkdir(parents=True, exist_ok=True)

    data: dict[str, Any] = {
        "name": name,
        "universe": config.universe,
        "period": config.period,
        "zoo": config.zoo,
    }

    if config.feature_profile_id:
        data["feature_profile_id"] = config.feature_profile_id

    data["label"] = {
        "horizon": config.label_config.horizon,
        "type": config.label_config.label_type,
    }
    if config.label_config.benchmark:
        data["label"]["benchmark"] = config.label_config.benchmark
    if config.label_config.cost_bps > 0:
        data["label"]["cost_bps"] = config.label_config.cost_bps
    if config.label_config.threshold != 0.0:
        data["label"]["threshold"] = config.label_config.threshold

    data["model"] = {
        "type": config.model_type,
    }
    if config.model_params:
        data["model"]["params"] = config.model_params

    data["walk_forward"] = {
        "n_splits": config.n_splits,
        "expanding": config.expanding,
        "random_seed": config.random_seed,
        "min_train_samples": config.min_train_samples,
    }
    if config.gap_days > 0:
        data["walk_forward"]["gap_days"] = config.gap_days

    if config.selection_config:
        data["feature_selection"] = {
            "methods": config.selection_config.methods,
            "min_icir": config.selection_config.min_icir,
            "max_corr": config.selection_config.max_corr,
        }

    data["pit_universe"] = config.pit_universe
    data["calibrate_proba"] = config.calibrate_proba

    if schedule:
        data["schedule"] = schedule

    path = base / f"{name}.yaml"
    path.write_text(
        yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    logger.info("Saved experiment config: %s", path)
    return path


def list_experiments(experiments_dir: Path | None = None) -> list[dict[str, Any]]:
    """List all saved experiment configs."""
    import yaml

    base = experiments_dir or _EXPERIMENTS_DIR
    if not base.exists():
        return []

    experiments = []
    for path in sorted(base.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            experiments.append({
                "name": data.get("name", path.stem),
                "universe": data.get("universe", ""),
                "model_type": data.get("model", {}).get("type", ""),
                "horizon": data.get("label", {}).get("horizon", ""),
                "label_type": data.get("label", {}).get("type", ""),
                "has_schedule": "schedule" in data,
                "path": str(path),
            })
        except Exception as exc:
            logger.warning("Failed to read experiment %s: %s", path.name, exc)

    return experiments


def _parse_experiment(data: dict[str, Any]) -> TrainConfig:
    """Parse YAML dict into TrainConfig."""
    label_data = data.get("label", {})
    label_config = LabelConfig(
        horizon=label_data.get("horizon", 1),
        label_type=label_data.get("type", "return"),
        threshold=label_data.get("threshold", 0.0),
        quantile_pct=label_data.get("quantile_pct", 0.2),
        benchmark=label_data.get("benchmark"),
        cost_bps=label_data.get("cost_bps", 0.0),
    )

    model_data = data.get("model", {})
    wf_data = data.get("walk_forward", {})

    selection_config = None
    sel_data = data.get("feature_selection")
    if sel_data:
        selection_config = FeatureSelectionConfig(
            methods=sel_data.get("methods", ["ic_filter", "corr_dedup"]),
            min_icir=sel_data.get("min_icir", 0.3),
            max_corr=sel_data.get("max_corr", 0.85),
        )

    preprocess_data = data.get("preprocess", {})
    preprocess_config = PreprocessConfig(
        winsorize=preprocess_data.get("winsorize", True),
        zscore=preprocess_data.get("zscore", True),
        fillna_strategy=preprocess_data.get("fillna_strategy", "median"),
    )

    return TrainConfig(
        universe=data.get("universe", "csi300"),
        period=data.get("period", ""),
        feature_profile_id=data.get("feature_profile_id"),
        zoo=data.get("zoo", "qlib158"),
        selection_config=selection_config,
        label_config=label_config,
        preprocess_config=preprocess_config,
        n_splits=wf_data.get("n_splits", 5),
        expanding=wf_data.get("expanding", True),
        gap_days=wf_data.get("gap_days", 0),
        model_type=model_data.get("type", "lightgbm"),
        model_params=model_data.get("params"),
        pit_universe=data.get("pit_universe", True),
        calibrate_proba=data.get("calibrate_proba", True),
        random_seed=wf_data.get("random_seed", 42),
        min_train_samples=wf_data.get("min_train_samples", 20),
    )
