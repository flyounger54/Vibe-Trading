"""Feature selection: six methods composable in a pipeline."""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

from src.ml.base_model import FeatureSelectionConfig

logger = logging.getLogger(__name__)


def select_features(
    features: pd.DataFrame,
    labels: pd.Series,
    config: FeatureSelectionConfig,
    panel: dict[str, pd.DataFrame] | None = None,
) -> tuple[list[str], dict[str, Any]]:
    """Run selection methods in order. Returns (retained_factor_ids, report)."""
    current_ids = list(features.columns)
    steps: list[dict[str, Any]] = []

    for method in config.methods:
        n_before = len(current_ids)
        sub_features = features[current_ids]

        if method == "ic_filter":
            if panel is None:
                logger.warning("ic_filter requires panel, skipping")
                continue
            current_ids, step_report = filter_by_ic(
                sub_features, labels, panel, min_icir=config.min_icir
            )
        elif method == "corr_dedup":
            current_ids, step_report = deduplicate_by_correlation(
                sub_features, current_ids, max_corr=config.max_corr
            )
        elif method == "mutual_info":
            current_ids, step_report = filter_by_mutual_info(
                sub_features, labels, top_n=config.mi_top_n, min_score=config.mi_min_score
            )
        elif method == "shap":
            current_ids, step_report = filter_by_shap(
                sub_features, labels, top_n=config.shap_top_n, min_pct=config.shap_min_pct
            )
        elif method == "boruta":
            current_ids, step_report = filter_by_boruta(
                sub_features, labels,
                max_iter=config.boruta_max_iter, alpha=config.boruta_alpha,
            )
        elif method == "importance":
            current_ids, step_report = filter_by_importance(
                sub_features, labels, min_pct=config.importance_min_pct
            )
        else:
            logger.warning("Unknown selection method: %s", method)
            continue

        step_report["method"] = method
        step_report["n_before"] = n_before
        step_report["n_after"] = len(current_ids)
        step_report["n_dropped"] = n_before - len(current_ids)
        steps.append(step_report)
        logger.info(
            "Feature selection [%s]: %d → %d (dropped %d)",
            method, n_before, len(current_ids), n_before - len(current_ids),
        )

    report = {
        "steps": steps,
        "n_initial": len(features.columns),
        "n_final": len(current_ids),
        "methods": config.methods,
    }
    return current_ids, report


def filter_by_ic(
    features: pd.DataFrame,
    labels: pd.Series,
    panel: dict[str, pd.DataFrame],
    min_icir: float = 0.3,
) -> tuple[list[str], dict]:
    """IC/ICIR pre-filter. Keep factors with |ICIR| >= min_icir."""
    from src.factors.factor_analysis_core import compute_ic_series

    close = panel.get("close")
    if close is None:
        return list(features.columns), {"error": "no close in panel"}

    fwd_ret = close.pct_change().shift(-1)
    kept = []
    ic_stats: dict[str, dict] = {}

    for col in features.columns:
        factor_wide = features[col].unstack(level="code")
        ic_series = compute_ic_series(factor_wide, fwd_ret)
        if ic_series.empty:
            continue
        ic_mean = float(ic_series.mean())
        ic_std = float(ic_series.std())
        icir = ic_mean / ic_std if ic_std > 0 else 0.0
        ic_stats[col] = {"ic_mean": round(ic_mean, 6), "icir": round(icir, 4)}
        if abs(icir) >= min_icir:
            kept.append(col)

    return kept, {"ic_stats": ic_stats, "threshold": min_icir}


def deduplicate_by_correlation(
    features: pd.DataFrame,
    priority_ids: list[str],
    max_corr: float = 0.85,
) -> tuple[list[str], dict]:
    """Remove highly correlated factors, keeping the one with higher priority (earlier in list)."""
    corr_matrix = features.corr().abs()
    dropped: list[str] = []
    kept = set(priority_ids)

    for i, col_i in enumerate(priority_ids):
        if col_i not in kept:
            continue
        for col_j in priority_ids[i + 1:]:
            if col_j not in kept:
                continue
            if col_i in corr_matrix.columns and col_j in corr_matrix.columns:
                if corr_matrix.loc[col_i, col_j] > max_corr:
                    kept.discard(col_j)
                    dropped.append(col_j)

    result = [c for c in priority_ids if c in kept]
    return result, {"dropped": dropped, "max_corr": max_corr}


def filter_by_mutual_info(
    features: pd.DataFrame,
    labels: pd.Series,
    top_n: int | None = None,
    min_score: float = 0.0,
) -> tuple[list[str], dict]:
    """Mutual information filter (captures non-linear dependencies)."""
    from sklearn.feature_selection import mutual_info_regression, mutual_info_classif

    aligned = features.loc[features.index.isin(labels.index)]
    aligned_labels = labels.loc[labels.index.isin(aligned.index)]

    X = aligned.fillna(0).values
    y = aligned_labels.values

    is_classification = len(np.unique(y[~np.isnan(y)])) <= 10
    mi_func = mutual_info_classif if is_classification else mutual_info_regression

    valid_mask = ~np.isnan(y)
    mi_scores = mi_func(X[valid_mask], y[valid_mask], random_state=42)
    scores = dict(zip(features.columns, mi_scores))

    sorted_features = sorted(scores.items(), key=lambda x: x[1], reverse=True)

    kept = []
    for name, score in sorted_features:
        if score < min_score:
            continue
        kept.append(name)
        if top_n is not None and len(kept) >= top_n:
            break

    return kept, {"scores": {k: round(v, 6) for k, v in scores.items()}}


def filter_by_shap(
    features: pd.DataFrame,
    labels: pd.Series,
    model_type: str = "lightgbm",
    top_n: int | None = None,
    min_pct: float = 0.01,
) -> tuple[list[str], dict]:
    """SHAP value filter. Trains a quick model, computes SHAP importance."""
    try:
        import shap
    except ImportError:
        logger.warning("shap not installed, skipping SHAP filter")
        return list(features.columns), {"error": "shap not installed"}

    aligned = features.loc[features.index.isin(labels.index)]
    aligned_labels = labels.loc[labels.index.isin(aligned.index)]
    X = aligned.fillna(0).values
    y = aligned_labels.values
    valid = ~np.isnan(y)
    X, y = X[valid], y[valid]

    n_sample = min(len(X), 5000)
    rng = np.random.default_rng(42)
    idx = rng.choice(len(X), n_sample, replace=False)
    X_sample, y_sample = X[idx], y[idx]

    try:
        if model_type == "lightgbm":
            import lightgbm as lgb
            model = lgb.LGBMRegressor(n_estimators=100, num_leaves=31, verbose=-1)
        else:
            from sklearn.ensemble import GradientBoostingRegressor
            model = GradientBoostingRegressor(n_estimators=100, max_depth=4)
        model.fit(X_sample, y_sample)
    except ImportError:
        from sklearn.ensemble import GradientBoostingRegressor
        model = GradientBoostingRegressor(n_estimators=100, max_depth=4)
        model.fit(X_sample, y_sample)

    explainer = shap.TreeExplainer(model)
    shap_values = explainer.shap_values(X_sample[:1000])
    importance = np.abs(shap_values).mean(axis=0)
    total = importance.sum()
    pct = importance / total if total > 0 else importance

    scores = dict(zip(features.columns, pct))
    sorted_features = sorted(scores.items(), key=lambda x: x[1], reverse=True)

    kept = []
    for name, score in sorted_features:
        if score < min_pct:
            continue
        kept.append(name)
        if top_n is not None and len(kept) >= top_n:
            break

    return kept, {"shap_pct": {k: round(float(v), 6) for k, v in scores.items()}}


def filter_by_boruta(
    features: pd.DataFrame,
    labels: pd.Series,
    max_iter: int = 100,
    alpha: float = 0.05,
) -> tuple[list[str], dict]:
    """Boruta: shadow features + statistical test to identify genuinely relevant features."""
    from sklearn.ensemble import RandomForestRegressor, RandomForestClassifier

    aligned = features.loc[features.index.isin(labels.index)]
    aligned_labels = labels.loc[labels.index.isin(aligned.index)]
    X = aligned.fillna(0).values
    y = aligned_labels.values
    valid = ~np.isnan(y)
    X, y = X[valid], y[valid]

    n_sample = min(len(X), 10000)
    rng = np.random.default_rng(42)
    idx = rng.choice(len(X), n_sample, replace=False)
    X, y = X[idx], y[idx]

    n_features = X.shape[1]
    hit_counts = np.zeros(n_features, dtype=int)
    is_classification = len(np.unique(y)) <= 10

    for iteration in range(max_iter):
        shadow = rng.permutation(X, axis=0)
        X_combined = np.hstack([X, shadow])

        if is_classification:
            rf = RandomForestClassifier(n_estimators=50, max_depth=5, random_state=iteration, n_jobs=-1)
        else:
            rf = RandomForestRegressor(n_estimators=50, max_depth=5, random_state=iteration, n_jobs=-1)
        rf.fit(X_combined, y)

        importance = rf.feature_importances_
        real_imp = importance[:n_features]
        shadow_imp = importance[n_features:]
        shadow_max = shadow_imp.max()

        hit_counts += (real_imp > shadow_max).astype(int)

    from scipy import stats
    threshold = stats.binom.ppf(1 - alpha, max_iter, 0.5)

    confirmed = hit_counts >= threshold
    kept = [features.columns[i] for i in range(n_features) if confirmed[i]]

    scores = dict(zip(features.columns, hit_counts / max_iter))
    return kept, {
        "hit_ratios": {k: round(float(v), 4) for k, v in scores.items()},
        "threshold": float(threshold / max_iter),
    }


def filter_by_importance(
    features: pd.DataFrame,
    labels: pd.Series,
    model_type: str = "lightgbm",
    min_pct: float = 0.01,
) -> tuple[list[str], dict]:
    """Model gain-based importance filter."""
    aligned = features.loc[features.index.isin(labels.index)]
    aligned_labels = labels.loc[labels.index.isin(aligned.index)]
    X = aligned.fillna(0).values
    y = aligned_labels.values
    valid = ~np.isnan(y)
    X, y = X[valid], y[valid]

    try:
        if model_type == "lightgbm":
            import lightgbm as lgb
            model = lgb.LGBMRegressor(n_estimators=200, num_leaves=63, verbose=-1)
        else:
            raise ImportError("fallback")
        model.fit(X, y)
        importance = model.feature_importances_
    except ImportError:
        from sklearn.ensemble import GradientBoostingRegressor
        model = GradientBoostingRegressor(n_estimators=100, max_depth=4)
        model.fit(X, y)
        importance = model.feature_importances_

    total = importance.sum()
    pct = importance / total if total > 0 else importance
    scores = dict(zip(features.columns, pct))

    kept = [col for col, p in scores.items() if p >= min_pct]
    return kept, {"importance_pct": {k: round(float(v), 6) for k, v in scores.items()}}
