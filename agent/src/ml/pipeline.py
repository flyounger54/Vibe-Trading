"""End-to-end training pipeline with overfit detection and structured logging."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from src.ml.base_model import (
    LabelConfig,
    PreprocessConfig,
    TrainConfig,
    TrainResult,
)

logger = logging.getLogger(__name__)

_OVERFIT_THRESHOLD = 3.0


class _TrainLogger:
    """Append-only JSONL logger for pipeline stages."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self._path, "a", encoding="utf-8")

    def log(self, stage: str, **kwargs: Any) -> None:
        entry = {"stage": stage, "ts": datetime.now(timezone.utc).isoformat(), **kwargs}
        self._fh.write(json.dumps(entry, default=str) + "\n")
        self._fh.flush()

    def close(self) -> None:
        self._fh.close()


def run_training_pipeline(config: TrainConfig) -> TrainResult:
    """End-to-end: load → features → preprocess → labels → split → audit → train → save."""
    wall_start = time.monotonic()
    now_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    model_id = config.model_id or f"{config.model_type}_{config.universe}_{config.label_config.key}_{now_str}"
    models_dir = Path.home() / ".vibe-trading" / "models"
    log_dir = models_dir / model_id
    log_dir.mkdir(parents=True, exist_ok=True)
    tlog = _TrainLogger(log_dir / "train_log.jsonl")

    try:
        return _run_pipeline(config, model_id, models_dir, tlog)
    finally:
        tlog.log("total_wall_seconds", seconds=round(time.monotonic() - wall_start, 2))
        tlog.close()


def _run_pipeline(
    config: TrainConfig,
    model_id: str,
    models_dir: Path,
    tlog: _TrainLogger,
) -> TrainResult:
    from src.ml.anti_leakage import run_full_audit
    from src.ml.feature_cache import FeatureCache, LabelCache
    from src.ml.features import build_feature_matrix, load_pit_universe, preprocess_features
    from src.ml.labels import build_labels
    from src.ml.models import get_model
    from src.ml.splits import auto_purge_days, get_dates_for_split, walk_forward_split
    from src.ml.storage import save_model
    from src.tools.alpha_bench_tool import _load_universe_panel

    # --- Step 1: Load data ---
    t0 = time.monotonic()
    panel = _load_universe_panel(config.universe, config.period)
    tlog.log("load_data", seconds=round(time.monotonic() - t0, 2))

    # --- Step 2: PIT universe ---
    pit_members = None
    if config.pit_universe:
        pit_members = load_pit_universe(config.universe, config.period)
        tlog.log("pit_universe", available=pit_members is not None)

    # --- Step 3: Determine factor IDs ---
    if config.feature_profile_id:
        from src.ml.feature_profile import load_feature_profile
        profile = load_feature_profile(config.feature_profile_id)
        factor_ids = profile.selected_factor_ids
        preprocess_config = profile.preprocess_config
        tlog.log("load_profile", profile_id=config.feature_profile_id, n_factors=len(factor_ids))
    else:
        from src.factors.registry import get_default_registry
        registry = get_default_registry()
        factor_ids = registry.list(zoo=config.zoo)
        preprocess_config = config.preprocess_config
        tlog.log("factor_list", zoo=config.zoo, n_factors=len(factor_ids))

    # --- Step 4: Build features (with cache) ---
    t0 = time.monotonic()
    cache = FeatureCache() if config.use_cache else None
    if cache:
        features, cache_hit = cache.get_or_compute(
            panel, factor_ids, config.zoo, config.universe
        )
    else:
        features = build_feature_matrix(panel, factor_ids=factor_ids, pit_members=pit_members)
        cache_hit = False
    tlog.log("build_features", seconds=round(time.monotonic() - t0, 2),
             shape=list(features.shape), cache_hit=cache_hit)

    # --- Step 5: Feature selection (if no profile and config has selection) ---
    selection_report = None
    if config.feature_profile_id is None and config.selection_config is not None:
        from src.ml.feature_selection import select_features
        label_for_sel = build_labels(panel, LabelConfig(horizon=1, label_type="return"))
        factor_ids, selection_report = select_features(
            features, label_for_sel, config.selection_config, panel=panel
        )
        features = features[factor_ids]
        tlog.log("feature_selection", n_selected=len(factor_ids),
                 methods=config.selection_config.methods)

    actual_factor_ids = list(features.columns)

    # --- Step 6: Build labels ---
    t0 = time.monotonic()
    label_cache = LabelCache() if config.use_cache else None
    if label_cache:
        labels, _ = label_cache.get_or_compute(panel, config.label_config, config.universe)
    else:
        labels = build_labels(panel, config.label_config)
    tlog.log("build_labels", seconds=round(time.monotonic() - t0, 2), n_labels=len(labels))

    # --- Step 7: Align features and labels ---
    common_idx = features.index.intersection(labels.index)
    features = features.loc[common_idx]
    labels = labels.loc[common_idx]
    tlog.log("align", n_samples=len(common_idx))

    # --- Step 8: Walk-forward split ---
    unique_dates = features.index.get_level_values("date").unique().sort_values()
    purge_days = auto_purge_days(config.label_config)
    splits = walk_forward_split(
        unique_dates,
        n_splits=config.n_splits,
        expanding=config.expanding,
        purge_days=purge_days,
        gap_days=config.gap_days,
    )
    tlog.log("split", n_splits=len(splits), purge_days=purge_days, gap_days=config.gap_days)

    # --- Step 9: Cross-validation ---
    cv_scores: list[dict[str, float]] = []
    all_test_preds: list[tuple[np.ndarray, np.ndarray]] = []

    for fold_idx, split in enumerate(splits):
        train_dates, test_dates = get_dates_for_split(unique_dates.values, split)

        train_mask = features.index.get_level_values("date").isin(train_dates)
        test_mask = features.index.get_level_values("date").isin(test_dates)

        X_train = features.loc[train_mask].values
        y_train = labels.loc[train_mask].values
        X_test = features.loc[test_mask].values
        y_test = labels.loc[test_mask].values

        # Preprocess with fit only on train dates
        train_features, pp_params = preprocess_features(
            features.loc[train_mask], preprocess_config, fit_dates=train_dates
        )
        X_train = train_features.values

        # Apply same preprocess params to test
        test_features, _ = preprocess_features(
            features.loc[test_mask], preprocess_config, fit_dates=train_dates
        )
        X_test = test_features.values

        # Train
        model = get_model(config.model_type, **(config.model_params or {}))
        model.fit(X_train, y_train, feature_names=actual_factor_ids)

        # Evaluate
        train_pred = model.predict(X_train)
        test_pred = model.predict(X_test)
        all_test_preds.append((y_test, test_pred))

        fold_score = _compute_fold_metrics(
            y_train, train_pred, y_test, test_pred, config.label_config.label_type
        )
        fold_score["fold"] = fold_idx
        cv_scores.append(fold_score)

        tlog.log(f"cv_fold_{fold_idx}", **{k: round(v, 6) if isinstance(v, float) else v
                                            for k, v in fold_score.items()})

    # --- Step 10: Anti-leakage audit ---
    last_train_dates, last_test_dates = get_dates_for_split(unique_dates.values, splits[-1])
    audit = run_full_audit(
        feature_dates=unique_dates.values,
        label_dates=unique_dates.values,
        label_horizon=config.label_config.horizon,
        train_dates=last_train_dates,
        test_dates=last_test_dates,
        gap_days=config.gap_days,
        preprocess_fit_dates=last_train_dates,
        universe=config.universe,
        panel=panel,
        pit_members=pit_members,
    )
    tlog.log("anti_leakage_audit", passed=audit["passed"], n_checks=audit["n_checks"])

    # --- Step 11: Final model on all data ---
    all_features, pp_params = preprocess_features(features, preprocess_config)
    X_all = all_features.values
    y_all = labels.values

    final_model = get_model(config.model_type, **(config.model_params or {}))
    final_model.fit(X_all, y_all, feature_names=actual_factor_ids)

    # --- Step 12: Calibrate probability ---
    if config.calibrate_proba:
        last_test_mask = features.index.get_level_values("date").isin(last_test_dates)
        X_cal = all_features.loc[last_test_mask].values
        y_cal = labels.loc[last_test_mask].values
        final_model.calibrate(X_cal, y_cal)
        tlog.log("calibrate", n_cal_samples=len(y_cal))

    # --- Step 13: Compute summary ---
    cv_summary = _summarize_cv(cv_scores)
    overfit_ratios = [s.get("overfit_ratio", 1.0) for s in cv_scores]
    overfit_warning = any(r > _OVERFIT_THRESHOLD for r in overfit_ratios)
    if overfit_warning:
        logger.warning("Overfit detected! Max overfit_ratio=%.2f", max(overfit_ratios))

    feature_importance = final_model.get_feature_importance() or {}
    top_20 = dict(sorted(feature_importance.items(), key=lambda x: x[1], reverse=True)[:20])

    # --- Step 14: Save ---
    metadata = {
        "model_type": config.model_type,
        "model_params": config.model_params or final_model.get_params(),
        "feature_profile_id": config.feature_profile_id,
        "factor_ids": actual_factor_ids,
        "preprocess_config": {
            "winsorize": preprocess_config.winsorize,
            "winsorize_limits": preprocess_config.winsorize_limits,
            "zscore": preprocess_config.zscore,
            "fillna_strategy": preprocess_config.fillna_strategy,
        },
        "label_config": {
            "horizon": config.label_config.horizon,
            "label_type": config.label_config.label_type,
            "threshold": config.label_config.threshold,
            "benchmark": config.label_config.benchmark,
            "cost_bps": config.label_config.cost_bps,
        },
        "universe": config.universe,
        "period": config.period,
        "zoo": config.zoo,
        "n_features": len(actual_factor_ids),
        "n_features_selected": len(actual_factor_ids),
        "n_train_samples": len(y_all),
        "cv_scores": cv_scores,
        "cv_summary": cv_summary,
        "feature_importance": top_20,
        "anti_leakage_audit": audit,
        "overfit_warning": overfit_warning,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "pit_universe": config.pit_universe,
    }

    model_path, meta_path = save_model(final_model, model_id, metadata)
    tlog.log("save", model_id=model_id)

    return TrainResult(
        model_id=model_id,
        model_path=model_path,
        meta_path=meta_path,
        n_features=len(actual_factor_ids),
        n_features_selected=len(actual_factor_ids),
        n_train_samples=len(y_all),
        cv_scores=cv_scores,
        cv_summary=cv_summary,
        feature_importance=top_20,
        selection_report=selection_report,
        anti_leakage_audit=audit,
        overfit_warning=overfit_warning,
        wall_seconds=0.0,  # filled by caller
    )


def _compute_fold_metrics(
    y_train: np.ndarray,
    train_pred: np.ndarray,
    y_test: np.ndarray,
    test_pred: np.ndarray,
    label_type: str,
) -> dict[str, float]:
    """Compute train and test metrics for a single fold."""
    valid_train = ~(np.isnan(y_train) | np.isnan(train_pred))
    valid_test = ~(np.isnan(y_test) | np.isnan(test_pred))

    train_ic = _safe_spearman(y_train[valid_train], train_pred[valid_train])
    test_ic = _safe_spearman(y_test[valid_test], test_pred[valid_test])
    overfit_ratio = abs(train_ic / test_ic) if abs(test_ic) > 1e-8 else 0.0

    metrics: dict[str, float] = {
        "train_ic": train_ic,
        "test_ic": test_ic,
        "overfit_ratio": overfit_ratio,
        "n_train": int(valid_train.sum()),
        "n_test": int(valid_test.sum()),
    }

    if label_type in ("binary", "top_bottom"):
        try:
            from sklearn.metrics import accuracy_score, f1_score, roc_auc_score

            y_t = y_test[valid_test]
            p_t = test_pred[valid_test]

            if len(np.unique(y_t)) == 2:
                metrics["test_auc"] = float(roc_auc_score(y_t, p_t))
                pred_binary = (p_t > np.median(p_t)).astype(float)
                metrics["test_accuracy"] = float(accuracy_score(y_t, pred_binary))
                metrics["test_f1"] = float(f1_score(y_t, pred_binary, zero_division=0))

            y_tr = y_train[valid_train]
            p_tr = train_pred[valid_train]
            if len(np.unique(y_tr)) == 2:
                metrics["train_auc"] = float(roc_auc_score(y_tr, p_tr))
        except Exception:
            pass

    return metrics


def _safe_spearman(a: np.ndarray, b: np.ndarray) -> float:
    if len(a) < 3:
        return 0.0
    corr, _ = spearmanr(a, b)
    return float(corr) if np.isfinite(corr) else 0.0


def _summarize_cv(cv_scores: list[dict[str, float]]) -> dict[str, float]:
    """Average all numeric CV metrics across folds."""
    if not cv_scores:
        return {}
    summary: dict[str, list[float]] = {}
    for score in cv_scores:
        for k, v in score.items():
            if isinstance(v, (int, float)) and k != "fold":
                summary.setdefault(k, []).append(float(v))

    return {
        f"{k}_mean": round(float(np.mean(vals)), 6)
        for k, vals in summary.items()
    }


def run_param_search(
    base_config: TrainConfig,
    param_grid: dict[str, list],
    metric: str = "test_ic_mean",
    max_combinations: int | None = None,
) -> list[TrainResult]:
    """Grid/random search over model params. Each combo runs the full pipeline."""
    import itertools

    keys = sorted(param_grid.keys())
    combos = list(itertools.product(*(param_grid[k] for k in keys)))

    if max_combinations and len(combos) > max_combinations:
        rng = np.random.default_rng(42)
        idx = rng.choice(len(combos), max_combinations, replace=False)
        combos = [combos[i] for i in idx]

    results = []
    for combo in combos:
        params = dict(zip(keys, combo))
        merged = {**(base_config.model_params or {}), **params}
        combo_config = TrainConfig(
            universe=base_config.universe,
            period=base_config.period,
            feature_profile_id=base_config.feature_profile_id,
            zoo=base_config.zoo,
            selection_config=base_config.selection_config,
            label_config=base_config.label_config,
            preprocess_config=base_config.preprocess_config,
            n_splits=base_config.n_splits,
            expanding=base_config.expanding,
            gap_days=base_config.gap_days,
            model_type=base_config.model_type,
            model_params=merged,
            use_cache=base_config.use_cache,
            pit_universe=base_config.pit_universe,
            calibrate_proba=base_config.calibrate_proba,
        )
        try:
            result = run_training_pipeline(combo_config)
            results.append(result)
        except Exception as exc:
            logger.warning("Param search failed for %s: %s", params, exc)

    results.sort(key=lambda r: r.cv_summary.get(metric, 0.0), reverse=True)
    return results
