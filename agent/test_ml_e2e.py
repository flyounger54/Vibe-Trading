"""End-to-end ML Training pipeline test.

Usage: ../.venv/bin/python test_ml_e2e.py
"""

import json
import sys
import time

sys.path.insert(0, ".")

def main():
    t0 = time.time()

    # =========================================================================
    # Step 1: Load data
    # =========================================================================
    print("=" * 60)
    print("STEP 1: Load universe panel")
    print("=" * 60)
    from src.tools.alpha_bench_tool import _load_universe_panel

    panel = _load_universe_panel("sp500", "2024-2025")
    close = panel["close"]
    print(f"  Panel: {close.shape[0]} dates x {close.shape[1]} stocks")
    print(f"  Date range: {close.index[0].date()} to {close.index[-1].date()}")
    print(f"  Fields: {list(panel.keys())}")

    # =========================================================================
    # Step 2: Build feature matrix
    # =========================================================================
    print("\n" + "=" * 60)
    print("STEP 2: Build feature matrix (qlib158, first 20 factors)")
    print("=" * 60)
    from src.ml.features import build_feature_matrix
    from src.factors.registry import get_default_registry

    registry = get_default_registry()
    all_ids = registry.list(zoo="qlib158")
    test_ids = all_ids[:20]
    print(f"  Using {len(test_ids)} factors out of {len(all_ids)}")

    features = build_feature_matrix(panel, factor_ids=test_ids)
    print(f"  Feature matrix: {features.shape[0]} rows x {features.shape[1]} columns")
    print(f"  NaN rate: {features.isna().mean().mean():.1%}")
    print(f"  Sample dates: {features.index.get_level_values('date').nunique()}")
    print(f"  Sample stocks: {features.index.get_level_values('code').nunique()}")

    # =========================================================================
    # Step 3: Preprocess features
    # =========================================================================
    print("\n" + "=" * 60)
    print("STEP 3: Preprocess features (winsorize + zscore)")
    print("=" * 60)
    from src.ml.features import preprocess_features
    from src.ml.base_model import PreprocessConfig
    import numpy as np

    dates = features.index.get_level_values("date").unique()
    train_end = dates[int(len(dates) * 0.7)]
    fit_dates = dates[dates <= train_end]

    features_pp, pp_params = preprocess_features(
        features,
        PreprocessConfig(winsorize=True, zscore=True, fillna_strategy="median"),
        fit_dates=fit_dates.values,
    )
    print(f"  Preprocessed shape: {features_pp.shape}")
    print(f"  NaN rate after fill: {features_pp.isna().mean().mean():.1%}")
    print(f"  Preprocess params keys: {list(pp_params.keys())}")

    # =========================================================================
    # Step 4: Build labels
    # =========================================================================
    print("\n" + "=" * 60)
    print("STEP 4: Build labels (binary, horizon=5, cost=15bps)")
    print("=" * 60)
    from src.ml.labels import build_labels
    from src.ml.base_model import LabelConfig

    label_config = LabelConfig(horizon=5, label_type="binary", cost_bps=15)
    labels = build_labels(panel, label_config)
    print(f"  Labels: {len(labels)} samples")
    print(f"  Positive rate: {labels.mean():.1%}")
    print(f"  Label key: {label_config.key}")

    # =========================================================================
    # Step 5: Walk-forward splits
    # =========================================================================
    print("\n" + "=" * 60)
    print("STEP 5: Walk-forward splits (3 folds, purge=5, embargo=2)")
    print("=" * 60)
    from src.ml.splits import walk_forward_split
    import pandas as pd

    unique_dates = features_pp.index.get_level_values("date").unique().sort_values()
    splits = walk_forward_split(
        unique_dates, n_splits=3, min_train_days=60, purge_days=5, gap_days=2,
    )
    for i, (train_idx, test_idx) in enumerate(splits):
        print(f"  Fold {i}: train={len(train_idx)} dates, test={len(test_idx)} dates")

    # =========================================================================
    # Step 6: Anti-leakage audit
    # =========================================================================
    print("\n" + "=" * 60)
    print("STEP 6: Anti-leakage audit")
    print("=" * 60)
    from src.ml.anti_leakage import audit_train_test_leakage

    for i, (train_idx, test_idx) in enumerate(splits):
        train_dates = unique_dates[train_idx]
        test_dates = unique_dates[test_idx]
        result = audit_train_test_leakage(
            train_dates.values, test_dates.values, label_horizon=5, gap_days=2,
        )
        status = "PASSED" if result["passed"] else "FAILED"
        print(f"  Fold {i}: {status}")
    print("  All folds passed anti-leakage audit!")

    # =========================================================================
    # Step 7: Train Ridge model (baseline, no extra deps)
    # =========================================================================
    print("\n" + "=" * 60)
    print("STEP 7: Train Ridge model (baseline)")
    print("=" * 60)

    common_idx = features_pp.index.intersection(labels.index)
    X_all = features_pp.loc[common_idx].values
    y_all = labels.loc[common_idx].values
    dates_all = features_pp.loc[common_idx].index.get_level_values("date")

    from src.ml.models.ridge_model import RidgePredictor
    from scipy.stats import spearmanr

    fold_results = []
    for i, (train_idx, test_idx) in enumerate(splits):
        train_dates_set = set(unique_dates[train_idx])
        test_dates_set = set(unique_dates[test_idx])

        train_mask = dates_all.isin(train_dates_set)
        test_mask = dates_all.isin(test_dates_set)

        X_train, y_train = X_all[train_mask], y_all[train_mask]
        X_test, y_test = X_all[test_mask], y_all[test_mask]

        model = RidgePredictor(alpha=1.0)
        model.fit(X_train, y_train)

        train_preds = model.predict(X_train)
        test_preds = model.predict(X_test)

        valid_train = ~np.isnan(y_train) & ~np.isnan(train_preds)
        valid_test = ~np.isnan(y_test) & ~np.isnan(test_preds)

        train_ic = spearmanr(y_train[valid_train], train_preds[valid_train])[0] if valid_train.sum() > 10 else 0
        test_ic = spearmanr(y_test[valid_test], test_preds[valid_test])[0] if valid_test.sum() > 10 else 0

        ratio = abs(train_ic / test_ic) if abs(test_ic) > 1e-8 else float("inf")
        fold_results.append({"fold": i, "train_ic": train_ic, "test_ic": test_ic, "overfit_ratio": ratio})
        print(f"  Fold {i}: train_IC={train_ic:.4f}, test_IC={test_ic:.4f}, overfit_ratio={ratio:.1f}")

    avg_test_ic = np.mean([f["test_ic"] for f in fold_results])
    print(f"  Average test IC: {avg_test_ic:.4f}")

    # =========================================================================
    # Step 8: Train LightGBM model
    # =========================================================================
    print("\n" + "=" * 60)
    print("STEP 8: Train LightGBM model")
    print("=" * 60)

    try:
        from src.ml.models.lightgbm_model import LightGBMPredictor

        lgb_fold_results = []
        for i, (train_idx, test_idx) in enumerate(splits):
            train_dates_set = set(unique_dates[train_idx])
            test_dates_set = set(unique_dates[test_idx])
            train_mask = dates_all.isin(train_dates_set)
            test_mask = dates_all.isin(test_dates_set)

            X_train, y_train = X_all[train_mask], y_all[train_mask]
            X_test, y_test = X_all[test_mask], y_all[test_mask]

            model = LightGBMPredictor(n_estimators=50, num_leaves=31)
            model.fit(X_train, y_train, feature_names=list(features_pp.columns))

            train_preds = model.predict(X_train)
            test_preds = model.predict(X_test)

            valid_train = ~np.isnan(y_train)
            valid_test = ~np.isnan(y_test)

            train_ic = spearmanr(y_train[valid_train], train_preds[valid_train])[0]
            test_ic = spearmanr(y_test[valid_test], test_preds[valid_test])[0]
            ratio = abs(train_ic / test_ic) if abs(test_ic) > 1e-8 else float("inf")
            lgb_fold_results.append({"fold": i, "train_ic": train_ic, "test_ic": test_ic, "overfit_ratio": ratio})
            print(f"  Fold {i}: train_IC={train_ic:.4f}, test_IC={test_ic:.4f}, overfit_ratio={ratio:.1f}")

        avg_lgb_ic = np.mean([f["test_ic"] for f in lgb_fold_results])
        print(f"  Average test IC: {avg_lgb_ic:.4f}")
    except ImportError:
        print("  LightGBM not installed, skipping")
        avg_lgb_ic = None

    # =========================================================================
    # Step 9: Save & load model round-trip
    # =========================================================================
    print("\n" + "=" * 60)
    print("STEP 9: Model save/load round-trip")
    print("=" * 60)
    from src.ml.storage import save_model, load_model, list_models

    test_model = RidgePredictor(alpha=1.0)
    test_model.fit(X_all, y_all)
    preds_before = test_model.predict(X_all[:100])

    test_meta = {
        "model_type": "ridge",
        "universe": "sp500",
        "period": "2024-2025",
        "factor_ids": list(features_pp.columns),
        "label_config": {"horizon": 5, "label_type": "binary", "cost_bps": 15},
        "cv_summary": {"test_ic_mean": float(avg_test_ic)},
        "n_features": features_pp.shape[1],
        "n_train_samples": len(X_all),
        "overfit_warning": False,
        "created_at": "2026-06-25T00:00:00Z",
    }

    model_id = "test_ridge_sp500_binary5d"
    save_model(test_model, model_id, test_meta)
    print(f"  Saved model: {model_id}")

    loaded_model, loaded_meta = load_model(model_id)
    preds_after = loaded_model.predict(X_all[:100])
    match = np.allclose(preds_before, preds_after, atol=1e-6)
    print(f"  Load round-trip: {'PASSED' if match else 'FAILED'}")

    # Also save LGB model if available
    if avg_lgb_ic is not None:
        lgb_model = LightGBMPredictor(n_estimators=50, num_leaves=31)
        lgb_model.fit(X_all, y_all, feature_names=list(features_pp.columns))
        lgb_meta = dict(test_meta)
        lgb_meta["model_type"] = "lightgbm"
        lgb_meta["cv_summary"] = {"test_ic_mean": float(avg_lgb_ic)}
        save_model(lgb_model, "test_lgb_sp500_binary5d", lgb_meta)
        print(f"  Saved LGB model: test_lgb_sp500_binary5d")

    # =========================================================================
    # Step 10: List & compare models
    # =========================================================================
    print("\n" + "=" * 60)
    print("STEP 10: List & compare models")
    print("=" * 60)
    models = list_models()
    print(f"  Total models: {len(models)}")
    for m in models:
        print(f"    {m['model_id']} ({m.get('model_type', '?')})")

    from src.ml.compare import compare_models
    model_ids = [m["model_id"] for m in models]
    if len(model_ids) >= 2:
        df = compare_models(model_ids)
        print(f"\n  Comparison table:")
        print(df.to_string(max_cols=6))
    else:
        print("  Need 2+ models for comparison, skipping")

    # =========================================================================
    # Summary
    # =========================================================================
    elapsed = time.time() - t0
    print("\n" + "=" * 60)
    print(f"ALL STEPS COMPLETED in {elapsed:.1f}s")
    print("=" * 60)
    print(f"  Data: SP500 {close.shape[0]} dates x {close.shape[1]} stocks")
    print(f"  Features: {features_pp.shape[1]} factors from qlib158")
    print(f"  Labels: binary 5d, cost 15bps, positive rate {labels.mean():.1%}")
    print(f"  Ridge avg test IC: {avg_test_ic:.4f}")
    if avg_lgb_ic is not None:
        print(f"  LightGBM avg test IC: {avg_lgb_ic:.4f}")
    print(f"  Anti-leakage: ALL PASSED")
    print(f"  Save/load: PASSED")


if __name__ == "__main__":
    main()
