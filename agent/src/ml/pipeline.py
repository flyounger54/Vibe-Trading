"""Leakage-safe, reproducible end-to-end ML research training pipeline."""

from __future__ import annotations

import json
import logging
import random
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from src.ml.base_model import LabelConfig, TrainConfig, TrainResult

logger = logging.getLogger(__name__)

_OVERFIT_THRESHOLD = 3.0
ProgressCallback = Callable[[str, dict[str, Any]], None]
CancelCallback = Callable[[], bool]


class TrainingDataError(ValueError):
    """The requested training run cannot produce a valid research artifact."""


class TrainingCancelled(RuntimeError):
    """Raised only at cooperative, auditable cancellation checkpoints."""


class _TrainLogger:
    """Append-only JSONL logger for actual pipeline stages."""

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


def run_training_pipeline(
    config: TrainConfig,
    *,
    progress_callback: ProgressCallback | None = None,
    should_cancel: CancelCallback | None = None,
    models_dir: Path | None = None,
) -> TrainResult:
    """Train a model without fitting any learned step on calibration/OOS rows.

    Every walk-forward fold follows this strict temporal order:
    ``train -> optional calibration -> real OOS``.  Feature selection and
    preprocessing are fitted inside the fold, never on the concatenated data.
    The persisted model is the final fold's train/calibration model rather than
    a refit on all data (which would contaminate its reported real OOS result).
    """
    _validate_config(config)
    _seed_everything(config.random_seed)
    wall_start = time.monotonic()
    now_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    model_id = config.model_id or f"{config.model_type}_{config.universe}_{config.label_config.key}_{now_str}"
    artifact_root = models_dir or Path.home() / ".vibe-trading" / "models"
    log_dir = artifact_root / model_id
    log_dir.mkdir(parents=True, exist_ok=True)
    tlog = _TrainLogger(log_dir / "train_log.jsonl")

    def emit(stage: str, **payload: Any) -> None:
        tlog.log(stage, **payload)
        if progress_callback is not None:
            progress_callback(stage, payload)

    try:
        result = _run_pipeline(config, model_id, artifact_root, emit, should_cancel)
        return replace(result, wall_seconds=round(time.monotonic() - wall_start, 3))
    finally:
        elapsed = round(time.monotonic() - wall_start, 3)
        tlog.log("total_wall_seconds", seconds=elapsed)
        tlog.close()


def _run_pipeline(
    config: TrainConfig,
    model_id: str,
    models_dir: Path,
    emit: Callable[..., None],
    should_cancel: CancelCallback | None,
) -> TrainResult:
    from src.ml.anti_leakage import run_full_audit
    from src.ml.feature_cache import FeatureCache, LabelCache, fingerprint_panel, fingerprint_pit_members
    from src.ml.features import Preprocessor, build_feature_matrix, load_pit_universe
    from src.ml.labels import build_labels
    from src.ml.manifest import ModelManifest
    from src.ml.models import get_model
    from src.ml.splits import auto_purge_days, get_dates_for_split, walk_forward_split
    from src.ml.storage import save_model
    from src.tools.alpha_bench_tool import _load_universe_panel

    _check_cancel(should_cancel)
    emit("load_data", message="Loading panel")
    panel = _load_universe_panel(config.universe, config.period)
    _validate_panel(panel)
    data_fingerprint = fingerprint_panel(panel)
    _check_cancel(should_cancel)

    emit("pit_universe", message="Loading point-in-time universe")
    pit_members = load_pit_universe(config.universe, config.period) if config.pit_universe else None
    research_only = pit_members is None
    if research_only:
        emit(
            "qualification",
            status="research_only",
            reason="point-in-time constituents unavailable or disabled",
        )
    else:
        emit("qualification", status="pit_verified", pit_dates=len(pit_members))

    _check_cancel(should_cancel)
    if config.feature_profile_id:
        from src.ml.feature_profile import load_feature_profile

        profile = load_feature_profile(config.feature_profile_id)
        candidate_factor_ids = profile.selected_factor_ids
        preprocess_config = profile.preprocess_config
        profile_id = profile.profile_id
        emit("load_profile", profile_id=profile_id, n_factors=len(candidate_factor_ids))
    else:
        from src.factors.registry import get_default_registry

        candidate_factor_ids = get_default_registry().list(zoo=config.zoo)
        preprocess_config = config.preprocess_config
        profile_id = None
        emit("factor_list", zoo=config.zoo, n_factors=len(candidate_factor_ids))
    if not candidate_factor_ids:
        raise TrainingDataError("No candidate factors are available for training")

    _check_cancel(should_cancel)
    emit("build_features", message="Building or loading versioned features")
    cache = FeatureCache() if config.use_cache else None
    if cache is not None:
        features, cache_hit = cache.get_or_compute(
            panel,
            candidate_factor_ids,
            config.zoo,
            config.universe,
            pit_members=pit_members,
        )
    else:
        features = build_feature_matrix(
            panel, factor_ids=candidate_factor_ids, zoo=config.zoo, pit_members=pit_members
        )
        cache_hit = False
    if features.empty:
        raise TrainingDataError("Feature construction produced no rows")
    emit("build_features", cache_hit=cache_hit, n_rows=len(features), n_features=len(features.columns))

    _check_cancel(should_cancel)
    emit("build_labels", message="Building labels")
    label_cache = LabelCache() if config.use_cache else None
    if label_cache is not None:
        labels, label_cache_hit = label_cache.get_or_compute(panel, config.label_config, config.universe)
    else:
        labels = build_labels(panel, config.label_config)
        label_cache_hit = False
    if labels.empty:
        raise TrainingDataError("Label construction produced no samples")
    emit("build_labels", cache_hit=label_cache_hit, n_labels=len(labels))

    common_idx = features.index.intersection(labels.index)
    features = features.loc[common_idx].sort_index()
    labels = labels.loc[common_idx].sort_index()
    if len(features) < config.min_train_samples:
        raise TrainingDataError(
            f"Insufficient aligned samples ({len(features)} < {config.min_train_samples})"
        )
    unique_dates = pd.DatetimeIndex(features.index.get_level_values("date").unique()).sort_values()
    if len(unique_dates) < 3:
        raise TrainingDataError("Insufficient distinct dates for time-series training")
    emit("align", n_samples=len(common_idx), n_dates=len(unique_dates))

    purge_days = auto_purge_days(config.label_config)
    splits = walk_forward_split(
        unique_dates,
        n_splits=config.n_splits,
        expanding=config.expanding,
        purge_days=purge_days,
        gap_days=config.gap_days,
    )
    if not splits:
        raise TrainingDataError("Walk-forward splitter produced no folds")
    emit("split", n_splits=len(splits), purge_days=purge_days, gap_days=config.gap_days)

    cv_scores: list[dict[str, float]] = []
    fold_provenance: list[dict[str, Any]] = []
    last_model: Any = None
    last_preprocessor: Preprocessor | None = None
    last_factor_ids: list[str] = []
    last_selection_report: dict[str, Any] | None = None
    last_partition: dict[str, list[str]] = {}
    last_train_dates: np.ndarray | None = None
    last_calibration_dates: np.ndarray | None = None
    last_oos_dates: np.ndarray | None = None

    for fold_idx, split in enumerate(splits):
        _check_cancel(should_cancel)
        train_dates, fold_test_dates = get_dates_for_split(unique_dates.values, split)
        calibration_dates, oos_dates = _split_calibration_and_oos(
            fold_test_dates, config.calibrate_proba and _is_classification(config.label_config)
        )
        train_mask = features.index.get_level_values("date").isin(train_dates)
        cal_mask = features.index.get_level_values("date").isin(calibration_dates)
        oos_mask = features.index.get_level_values("date").isin(oos_dates)
        raw_train = features.loc[train_mask]
        raw_cal = features.loc[cal_mask]
        raw_oos = features.loc[oos_mask]
        y_train = labels.loc[train_mask].values
        y_cal = labels.loc[cal_mask].values
        y_oos = labels.loc[oos_mask].values
        _validate_fold_samples(y_train, "train", fold_idx, config)
        _validate_fold_samples(y_oos, "OOS", fold_idx, config)
        if len(calibration_dates):
            _validate_fold_samples(y_cal, "calibration", fold_idx, config, allow_small=True)

        # Crucially, selection has access only to this fold's pre-OOS training
        # data and labels.  Passing the full panel here was the previous leak.
        if config.feature_profile_id is None and config.selection_config is not None:
            from src.ml.feature_selection import select_features

            train_panel = _slice_panel_by_dates(panel, train_dates)
            selected_ids, selection_report = select_features(
                raw_train,
                labels.loc[train_mask],
                config.selection_config,
                panel=train_panel,
            )
        else:
            selected_ids = list(raw_train.columns)
            selection_report = None
        if not selected_ids:
            raise TrainingDataError(f"Feature selection retained zero factors in fold {fold_idx}")

        preprocessor = Preprocessor(preprocess_config).fit(raw_train[selected_ids])
        X_train = preprocessor.transform(raw_train[selected_ids]).values
        X_cal = preprocessor.transform(raw_cal[selected_ids]).values if len(raw_cal) else np.empty((0, len(selected_ids)))
        X_oos = preprocessor.transform(raw_oos[selected_ids]).values
        _reject_nonfinite(X_train, "train", fold_idx)
        _reject_nonfinite(X_cal, "calibration", fold_idx)
        _reject_nonfinite(X_oos, "OOS", fold_idx)

        model = get_model(config.model_type, **_seeded_model_params(config))
        model.fit(X_train, y_train, feature_names=selected_ids)
        if len(calibration_dates):
            _validate_calibration_labels(y_cal, fold_idx)
            model.calibrate(X_cal, y_cal)

        train_pred = model.predict(X_train)
        oos_pred = model.predict(X_oos)
        fold_score = _compute_fold_metrics(
            y_train, train_pred, y_oos, oos_pred, config.label_config.label_type
        )
        fold_score["fold"] = fold_idx
        cv_scores.append(fold_score)
        partition = {
            "train_dates": _as_dates(train_dates),
            "calibration_dates": _as_dates(calibration_dates),
            "oos_dates": _as_dates(oos_dates),
        }
        fold_provenance.append(
            {
                "fold": fold_idx,
                "partition": partition,
                "selected_factor_ids": selected_ids,
                "selection_report": selection_report,
                "preprocessing": preprocessor.manifest(),
            }
        )
        emit(
            "cv_fold",
            fold=fold_idx,
            n_train=len(y_train),
            n_calibration=len(y_cal),
            n_oos=len(y_oos),
            n_features=len(selected_ids),
            metrics=fold_score,
        )

        last_model = model
        last_preprocessor = preprocessor
        last_factor_ids = selected_ids
        last_selection_report = selection_report
        last_partition = partition
        last_train_dates = train_dates
        last_calibration_dates = calibration_dates
        last_oos_dates = oos_dates

    assert last_model is not None and last_preprocessor is not None
    assert last_train_dates is not None and last_oos_dates is not None
    assert last_calibration_dates is not None

    audit = run_full_audit(
        feature_dates=unique_dates.values,
        label_dates=unique_dates.values,
        label_horizon=config.label_config.horizon,
        train_dates=last_train_dates,
        test_dates=last_oos_dates,
        gap_days=config.gap_days,
        selection_dates=last_train_dates,
        preprocess_fit_dates=last_train_dates,
        calibration_dates=last_calibration_dates,
        universe=config.universe,
        panel=panel,
        pit_members=pit_members,
    )
    emit("anti_leakage_audit", passed=audit["passed"], failed_checks=audit["failed_checks"])

    cv_summary = _summarize_cv(cv_scores)
    overfit_warning = any(score.get("overfit_ratio", 1.0) > _OVERFIT_THRESHOLD for score in cv_scores)
    feature_importance = last_model.get_feature_importance() or {}
    top_20 = dict(sorted(feature_importance.items(), key=lambda item: item[1], reverse=True)[:20])
    production_eligible = bool(audit["passed"] and not research_only)

    manifest = ModelManifest.create(
        model_id=model_id,
        data={
            "universe": config.universe,
            "period": config.period,
            "data_fingerprint": data_fingerprint,
            "pit_requested": config.pit_universe,
            "pit_available": pit_members is not None,
            "pit_fingerprint": fingerprint_pit_members(pit_members),
        },
        features={
            "zoo": config.zoo,
            "candidate_factor_ids": candidate_factor_ids,
            "selected_factor_ids": last_factor_ids,
            "feature_profile_id": profile_id,
            "fold_selection": [
                {"fold": item["fold"], "selected_factor_ids": item["selected_factor_ids"]}
                for item in fold_provenance
            ],
        },
        preprocessing=last_preprocessor.manifest(),
        split={
            "n_splits": len(splits),
            "purge_days": purge_days,
            "gap_days": config.gap_days,
            "final_partition": last_partition,
            "folds": fold_provenance,
        },
        random_seed=config.random_seed,
        metrics={"cv_scores": cv_scores, "cv_summary": cv_summary, "anti_leakage_audit": audit},
        qualification={
            "research_only": research_only,
            "production_eligible": production_eligible,
            "reason": None if production_eligible else "PIT data or leakage audit prevents production qualification",
        },
    )
    metadata = {
        "model_type": config.model_type,
        "model_params": config.model_params or last_model.get_params(),
        "feature_profile_id": profile_id,
        "factor_ids": last_factor_ids,
        "preprocessing": last_preprocessor.manifest(),
        "label_config": {
            "horizon": config.label_config.horizon,
            "label_type": config.label_config.label_type,
            "threshold": config.label_config.threshold,
            "quantile_pct": config.label_config.quantile_pct,
            "benchmark": config.label_config.benchmark,
            "cost_bps": config.label_config.cost_bps,
        },
        "universe": config.universe,
        "period": config.period,
        "zoo": config.zoo,
        "n_features": len(last_factor_ids),
        "n_features_selected": len(last_factor_ids),
        "n_train_samples": int(cv_scores[-1]["n_train"]),
        "cv_scores": cv_scores,
        "cv_summary": cv_summary,
        "feature_importance": top_20,
        "anti_leakage_audit": audit,
        "overfit_warning": overfit_warning,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "pit_universe": config.pit_universe,
        "research_only": research_only,
        "production_eligible": production_eligible,
        "random_seed": config.random_seed,
    }
    emit("save", model_id=model_id, research_only=research_only, production_eligible=production_eligible)
    model_path, meta_path = save_model(
        last_model, model_id, metadata, models_dir=models_dir, model_manifest=manifest
    )

    return TrainResult(
        model_id=model_id,
        model_path=model_path,
        meta_path=meta_path,
        n_features=len(last_factor_ids),
        n_features_selected=len(last_factor_ids),
        n_train_samples=int(cv_scores[-1]["n_train"]),
        cv_scores=cv_scores,
        cv_summary=cv_summary,
        feature_importance=top_20,
        selection_report=last_selection_report,
        anti_leakage_audit=audit,
        overfit_warning=overfit_warning,
        wall_seconds=0.0,
        research_only=research_only,
        production_eligible=production_eligible,
    )


def _validate_config(config: TrainConfig) -> None:
    if config.n_splits < 1:
        raise ValueError("n_splits must be at least 1")
    if config.min_train_samples < 2:
        raise ValueError("min_train_samples must be at least 2")
    if config.label_config.horizon < 1:
        raise ValueError("label horizon must be at least 1")


def _seed_everything(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    try:
        import torch

        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)
    except ImportError:
        pass


def _seeded_model_params(config: TrainConfig) -> dict[str, Any]:
    params = dict(config.model_params or {})
    if config.model_type in {"lightgbm", "xgboost"}:
        params.setdefault("random_state", config.random_seed)
    if config.model_type == "deep_nn":
        params.setdefault("seed", config.random_seed)
    return params


def _validate_panel(panel: dict[str, pd.DataFrame]) -> None:
    close = panel.get("close")
    if close is None or close.empty:
        raise TrainingDataError("Training panel requires non-empty close prices")


def _check_cancel(should_cancel: CancelCallback | None) -> None:
    if should_cancel is not None and should_cancel():
        raise TrainingCancelled("Training cancelled by user")


def _split_calibration_and_oos(
    test_dates: np.ndarray,
    needs_calibration: bool,
) -> tuple[np.ndarray, np.ndarray]:
    dates = np.asarray(test_dates)
    if not needs_calibration:
        return np.asarray([], dtype=dates.dtype), dates
    if len(dates) < 2:
        raise TrainingDataError("Calibration requires at least two post-training dates")
    n_calibration = max(1, len(dates) // 4)
    return dates[:n_calibration], dates[n_calibration:]


def _validate_fold_samples(
    values: np.ndarray,
    partition: str,
    fold_idx: int,
    config: TrainConfig,
    *,
    allow_small: bool = False,
) -> None:
    valid = values[~np.isnan(values)]
    required = 2 if allow_small else config.min_train_samples
    if len(valid) < required:
        raise TrainingDataError(
            f"Fold {fold_idx} {partition} has insufficient labelled samples ({len(valid)} < {required})"
        )
    if partition == "train" and _is_classification(config.label_config) and len(np.unique(valid)) < 2:
        raise TrainingDataError(f"Fold {fold_idx} train labels contain a single class")


def _validate_calibration_labels(values: np.ndarray, fold_idx: int) -> None:
    valid = values[~np.isnan(values)]
    if len(np.unique(valid)) < 2:
        raise TrainingDataError(f"Fold {fold_idx} calibration labels contain a single class")


def _reject_nonfinite(values: np.ndarray, partition: str, fold_idx: int) -> None:
    if len(values) and not np.isfinite(values).all():
        raise TrainingDataError(f"Fold {fold_idx} {partition} features contain NaN or infinity after preprocessing")


def _slice_panel_by_dates(panel: dict[str, pd.DataFrame], dates: np.ndarray) -> dict[str, pd.DataFrame]:
    date_set = set(dates)
    return {
        name: frame.loc[frame.index.isin(date_set)]
        for name, frame in panel.items()
        if frame is not None
    }


def _as_dates(values: np.ndarray) -> list[str]:
    return [str(pd.Timestamp(value).date()) for value in values]


def _is_classification(config: LabelConfig) -> bool:
    return config.label_type in {"binary", "top_bottom"}


def _compute_fold_metrics(
    y_train: np.ndarray,
    train_pred: np.ndarray,
    y_test: np.ndarray,
    test_pred: np.ndarray,
    label_type: str,
) -> dict[str, float]:
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
    if label_type in {"binary", "top_bottom"}:
        try:
            from sklearn.metrics import accuracy_score, f1_score, roc_auc_score

            y_t, p_t = y_test[valid_test], test_pred[valid_test]
            if len(np.unique(y_t)) == 2:
                metrics["test_auc"] = float(roc_auc_score(y_t, p_t))
                pred_binary = (p_t > np.median(p_t)).astype(float)
                metrics["test_accuracy"] = float(accuracy_score(y_t, pred_binary))
                metrics["test_f1"] = float(f1_score(y_t, pred_binary, zero_division=0))
            y_tr, p_tr = y_train[valid_train], train_pred[valid_train]
            if len(np.unique(y_tr)) == 2:
                metrics["train_auc"] = float(roc_auc_score(y_tr, p_tr))
        except ValueError:
            pass
    return metrics


def _safe_spearman(a: np.ndarray, b: np.ndarray) -> float:
    if len(a) < 3:
        return 0.0
    corr, _ = spearmanr(a, b)
    return float(corr) if np.isfinite(corr) else 0.0


def _summarize_cv(cv_scores: list[dict[str, float]]) -> dict[str, float]:
    if not cv_scores:
        return {}
    summary: dict[str, list[float]] = {}
    for score in cv_scores:
        for key, value in score.items():
            if isinstance(value, (int, float)) and key != "fold":
                summary.setdefault(key, []).append(float(value))
    return {f"{key}_mean": round(float(np.mean(values)), 6) for key, values in summary.items()}


def run_param_search(
    base_config: TrainConfig,
    param_grid: dict[str, list],
    metric: str = "test_ic_mean",
    max_combinations: int | None = None,
) -> list[TrainResult]:
    """Grid/random search where every candidate goes through the same safe pipeline."""
    import itertools

    keys = sorted(param_grid.keys())
    combos = list(itertools.product(*(param_grid[key] for key in keys)))
    if max_combinations and len(combos) > max_combinations:
        rng = np.random.default_rng(base_config.random_seed)
        idx = rng.choice(len(combos), max_combinations, replace=False)
        combos = [combos[i] for i in idx]
    results = []
    for combo in combos:
        params = {**(base_config.model_params or {}), **dict(zip(keys, combo))}
        try:
            results.append(run_training_pipeline(replace(base_config, model_params=params)))
        except (TrainingDataError, TrainingCancelled, ValueError) as exc:
            logger.warning("Parameter candidate %s failed safely: %s", params, exc)
    return sorted(results, key=lambda result: result.cv_summary.get(metric, 0.0), reverse=True)
