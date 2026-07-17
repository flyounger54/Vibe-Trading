"""Model health monitoring: alpha decay detection + version drift analysis."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

_MODELS_DIR = Path.home() / ".vibe-trading" / "models"


@dataclass(frozen=True)
class HealthReport:
    model_id: str
    train_ic_mean: float
    recent_ic_mean: float
    drift_score: float
    retrain_recommended: bool
    last_check: str
    details: dict[str, Any]


def evaluate_model_health(
    model_id: str,
    recent_period: str,
    drift_threshold: float = 0.5,
    models_dir: Path | None = None,
) -> HealthReport:
    """Evaluate model health by comparing recent OOS IC with training CV IC.

    drift_score = (train_ic - recent_ic) / train_ic
    When drift_score > drift_threshold → retrain_recommended = True
    """
    from src.ml.storage import load_model
    from src.ml.features import build_feature_matrix, preprocess_features
    from src.ml.labels import build_labels
    from src.ml.base_model import LabelConfig, PreprocessConfig
    from src.tools.alpha_bench_tool import _load_universe_panel
    from scipy.stats import spearmanr

    base = models_dir or _MODELS_DIR
    model, metadata = load_model(model_id, base)

    cv_summary = metadata.get("cv_summary", {})
    train_ic = cv_summary.get("test_ic_mean", 0.0)

    universe = metadata.get("universe", "csi300")
    factor_ids = metadata.get("factor_ids", [])
    label_cfg_raw = metadata.get("label_config", {})
    label_config = LabelConfig(
        horizon=label_cfg_raw.get("horizon", 1),
        label_type=label_cfg_raw.get("label_type", "return"),
        benchmark=label_cfg_raw.get("benchmark"),
        cost_bps=label_cfg_raw.get("cost_bps", 0),
    )

    pp_raw = metadata.get("preprocess_config", {})
    pp_config = PreprocessConfig(
        winsorize=pp_raw.get("winsorize", True),
        zscore=pp_raw.get("zscore", True),
        fillna_strategy=pp_raw.get("fillna_strategy", "median"),
    )

    try:
        panel = _load_universe_panel(universe, recent_period)
        features = build_feature_matrix(panel, factor_ids=factor_ids)
        features, _ = preprocess_features(features, pp_config)
        labels = build_labels(panel, label_config)

        common_idx = features.index.intersection(labels.index)
        X = features.loc[common_idx].values
        y = labels.loc[common_idx].values

        valid = ~(np.isnan(y))
        if valid.sum() < 10:
            raise ValueError("Too few valid samples for health check")

        preds = model.predict(X[valid])
        corr, _ = spearmanr(y[valid], preds)
        recent_ic = float(corr) if np.isfinite(corr) else 0.0

    except Exception as exc:
        logger.warning("Health check failed for %s: %s", model_id, exc)
        return HealthReport(
            model_id=model_id,
            train_ic_mean=train_ic,
            recent_ic_mean=0.0,
            drift_score=1.0,
            retrain_recommended=True,
            last_check=datetime.now(timezone.utc).isoformat(),
            details={"error": str(exc)},
        )

    if abs(train_ic) > 1e-8:
        drift_score = (abs(train_ic) - abs(recent_ic)) / abs(train_ic)
    else:
        drift_score = 1.0 if abs(recent_ic) < 1e-8 else 0.0

    drift_score = max(0.0, drift_score)
    retrain = drift_score > drift_threshold

    if retrain:
        logger.warning(
            "Model %s: alpha decay detected (train_ic=%.4f, recent_ic=%.4f, drift=%.2f)",
            model_id, train_ic, recent_ic, drift_score,
        )

    return HealthReport(
        model_id=model_id,
        train_ic_mean=train_ic,
        recent_ic_mean=recent_ic,
        drift_score=round(drift_score, 4),
        retrain_recommended=retrain,
        last_check=datetime.now(timezone.utc).isoformat(),
        details={
            "recent_period": recent_period,
            "n_samples": int(valid.sum()),
            "drift_threshold": drift_threshold,
        },
    )


def check_all_models_health(
    recent_period: str,
    drift_threshold: float = 0.5,
    models_dir: Path | None = None,
) -> list[HealthReport]:
    """Run health check on all saved models."""
    from src.ml.storage import list_models

    models = list_models(models_dir)
    reports = []

    for m in models:
        mid = m["model_id"]
        if m.get("model_type") == "ensemble":
            continue
        try:
            report = evaluate_model_health(mid, recent_period, drift_threshold, models_dir)
            reports.append(report)
        except Exception as exc:
            logger.warning("Skipped health check for %s: %s", mid, exc)

    reports.sort(key=lambda r: r.drift_score, reverse=True)
    return reports


def compare_version_drift(
    base_id: str,
    models_dir: Path | None = None,
) -> dict[str, Any]:
    """Analyze IC trends across versions of the same model.

    Detects:
    - Sustained decline: last 3 versions IC all declining → retrain ineffective
    - Sudden drop: latest version IC drops > 50% from previous
    """
    from src.ml.compare import compare_versions

    df = compare_versions(base_id, models_dir)
    if df.empty or len(df) < 2:
        return {"base_id": base_id, "status": "insufficient_versions", "n_versions": len(df)}

    ics = df["test_ic_mean"].dropna().values

    sustained_decline = False
    if len(ics) >= 3:
        last3 = ics[-3:]
        sustained_decline = all(last3[i] > last3[i + 1] for i in range(2))

    sudden_drop = False
    if len(ics) >= 2 and abs(ics[-2]) > 1e-8:
        drop_pct = (abs(ics[-2]) - abs(ics[-1])) / abs(ics[-2])
        sudden_drop = drop_pct > 0.5

    status = "healthy"
    recommendation = None
    if sustained_decline:
        status = "declining"
        recommendation = "Retraining is ineffective. Consider changing feature set, model type, or label config."
    elif sudden_drop:
        status = "sudden_drop"
        recommendation = "Latest version shows sharp IC decline. Investigate recent market regime change."

    return {
        "base_id": base_id,
        "n_versions": len(df),
        "ic_trend": ics.tolist(),
        "status": status,
        "sustained_decline": sustained_decline,
        "sudden_drop": sudden_drop,
        "recommendation": recommendation,
    }
