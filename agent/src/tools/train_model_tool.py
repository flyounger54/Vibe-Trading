"""ML model training agent tools: select_features, train_model, list_models, compare_models."""

from __future__ import annotations

import json
from typing import Any

from src.agent.tools import BaseTool


class SelectFeaturesTool(BaseTool):
    """Create a reusable FeatureProfile via independent feature selection."""

    name = "select_features"
    description = (
        "Run feature selection on alpha-zoo factors to create a reusable FeatureProfile. "
        "The profile stores which factors survived screening and can be reused by multiple "
        "train_model calls. Methods: ic_filter, corr_dedup, mutual_info, shap, boruta, importance."
    )
    parameters = {
        "type": "object",
        "properties": {
            "universe": {
                "type": "string",
                "description": "Stock universe: csi300 or sp500",
                "enum": ["csi300", "sp500"],
            },
            "period": {
                "type": "string",
                "description": "Selection period (YYYY-YYYY or YYYY-MM-DD/YYYY-MM-DD). "
                               "Should precede training period to avoid leakage.",
            },
            "zoo": {
                "type": "string",
                "description": "Factor zoo (default: qlib158)",
                "default": "qlib158",
            },
            "methods": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Selection methods in order. Default: [ic_filter, corr_dedup]",
            },
            "profile_id": {
                "type": "string",
                "description": "Custom profile ID (auto-generated if omitted)",
            },
        },
        "required": ["universe", "period"],
    }
    repeatable = True
    is_readonly = False

    def execute(self, **kwargs: Any) -> str:
        from src.ml.base_model import FeatureSelectionConfig
        from src.ml.feature_profile import create_feature_profile

        methods = kwargs.get("methods", ["ic_filter", "corr_dedup"])
        sel_config = FeatureSelectionConfig(methods=methods)

        try:
            profile = create_feature_profile(
                universe=kwargs["universe"],
                period=kwargs["period"],
                zoo=kwargs.get("zoo", "qlib158"),
                selection_config=sel_config,
                profile_id=kwargs.get("profile_id"),
            )
            return json.dumps({
                "status": "ok",
                "profile_id": profile.profile_id,
                "n_factors_total": len(profile.selection_report.get("steps", [{}])[0].get("n_before", 0)) if profile.selection_report.get("steps") else 0,
                "n_factors_selected": len(profile.selected_factor_ids),
                "methods": profile.methods,
                "top_factors": profile.selected_factor_ids[:15],
                "next_step": f"Use profile_id={profile.profile_id!r} in train_model.",
            }, ensure_ascii=False, indent=2)
        except Exception as exc:
            return json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False)


class TrainModelTool(BaseTool):
    """Train an ML model using alpha-zoo factors with walk-forward CV."""

    name = "train_model"
    description = (
        "Train an ML model (lightgbm/xgboost/ridge) using alpha-zoo factors as features "
        "and forward returns as labels. Walk-forward cross-validation with Purge+Embargo "
        "prevents look-ahead bias. Supports excess returns over benchmark, transaction "
        "cost deduction, and overfit detection."
    )
    parameters = {
        "type": "object",
        "properties": {
            "universe": {
                "type": "string",
                "description": "Stock universe",
                "enum": ["csi300", "sp500"],
            },
            "period": {
                "type": "string",
                "description": "Training period (YYYY-YYYY or YYYY-MM-DD/YYYY-MM-DD)",
            },
            "feature_profile_id": {
                "type": "string",
                "description": "Reuse a FeatureProfile. Omit to use all factors from the zoo.",
            },
            "zoo": {
                "type": "string",
                "description": "Factor zoo (default: qlib158)",
                "default": "qlib158",
            },
            "model_type": {
                "type": "string",
                "description": "Model type",
                "enum": ["lightgbm", "xgboost", "ridge"],
                "default": "lightgbm",
            },
            "model_params": {
                "type": "object",
                "description": "Custom model hyperparameters (e.g. {num_leaves: 127})",
            },
            "label_horizon": {
                "type": "integer",
                "description": "Forward return horizon in bars (default: 5)",
                "default": 5,
            },
            "label_type": {
                "type": "string",
                "description": "Label type",
                "enum": ["return", "rank", "binary", "top_bottom"],
                "default": "binary",
            },
            "benchmark": {
                "type": "string",
                "description": "Excess return benchmark (e.g. 000300.SH, SPY). Omit for absolute returns.",
            },
            "cost_bps": {
                "type": "number",
                "description": "Round-trip transaction cost in bps (default: 0)",
                "default": 0,
            },
            "n_splits": {
                "type": "integer",
                "description": "Walk-forward CV folds (default: 5)",
                "default": 5,
            },
            "model_id": {
                "type": "string",
                "description": "Custom model ID (auto-generated if omitted)",
            },
        },
        "required": ["universe", "period"],
    }
    repeatable = True
    is_readonly = False

    def execute(self, **kwargs: Any) -> str:
        from src.ml.base_model import LabelConfig, TrainConfig
        from src.ml.pipeline import run_training_pipeline

        label_config = LabelConfig(
            horizon=kwargs.get("label_horizon", 5),
            label_type=kwargs.get("label_type", "binary"),
            benchmark=kwargs.get("benchmark"),
            cost_bps=kwargs.get("cost_bps", 0),
        )

        config = TrainConfig(
            universe=kwargs["universe"],
            period=kwargs["period"],
            feature_profile_id=kwargs.get("feature_profile_id"),
            zoo=kwargs.get("zoo", "qlib158"),
            label_config=label_config,
            n_splits=kwargs.get("n_splits", 5),
            model_type=kwargs.get("model_type", "lightgbm"),
            model_params=kwargs.get("model_params"),
            model_id=kwargs.get("model_id"),
        )

        try:
            result = run_training_pipeline(config)
            return json.dumps({
                "status": "ok",
                "model_id": result.model_id,
                "model_path": str(result.model_path),
                "n_features": result.n_features,
                "n_train_samples": result.n_train_samples,
                "cv_summary": result.cv_summary,
                "overfit_warning": result.overfit_warning,
                "top_features": dict(list(result.feature_importance.items())[:10]),
                "anti_leakage": "PASSED" if result.anti_leakage_audit.get("passed") else "FAILED",
                "next_step": (
                    f"Use model_id={result.model_id!r} in a backtest with "
                    f"strategy mf_ml_predictor, or compare with compare_models."
                ),
            }, ensure_ascii=False, indent=2)
        except Exception as exc:
            return json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False)


class ListModelsTool(BaseTool):
    """List all trained ML models."""

    name = "list_models"
    description = "List all trained ML models with their CV metrics, sorted by IC or AUC."
    parameters = {
        "type": "object",
        "properties": {
            "sort_by": {
                "type": "string",
                "description": "Sort field (default: created_at)",
                "default": "created_at",
            },
        },
    }
    repeatable = True

    def execute(self, **kwargs: Any) -> str:
        from src.ml.storage import list_models

        models = list_models(sort_by=kwargs.get("sort_by", "created_at"))
        return json.dumps({
            "status": "ok",
            "n_models": len(models),
            "models": models,
        }, ensure_ascii=False, indent=2)


class CompareModelsTool(BaseTool):
    """Compare multiple trained models by CV metrics."""

    name = "compare_models"
    description = (
        "Compare multiple trained models side-by-side on CV metrics "
        "(IC, AUC, accuracy, overfit ratio). Optionally run backtest comparison."
    )
    parameters = {
        "type": "object",
        "properties": {
            "model_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Model IDs to compare",
            },
        },
        "required": ["model_ids"],
    }
    repeatable = True

    def execute(self, **kwargs: Any) -> str:
        from src.ml.storage import list_models

        model_ids = kwargs["model_ids"]
        all_models = {m["model_id"]: m for m in list_models()}

        comparison = []
        for mid in model_ids:
            info = all_models.get(mid)
            if info:
                comparison.append(info)
            else:
                comparison.append({"model_id": mid, "error": "not found"})

        return json.dumps({
            "status": "ok",
            "comparison": comparison,
        }, ensure_ascii=False, indent=2)


class CreateEnsembleTool(BaseTool):
    """Combine multiple trained models into an ensemble."""

    name = "create_ensemble"
    description = (
        "Create an ensemble that combines multiple trained ML models. "
        "Methods: average (equal/custom weights), ic_weighted (by CV IC), "
        "stacking (Ridge meta-learner). The ensemble is itself a model and "
        "can be used directly in mf_ml_predictor for backtesting."
    )
    parameters = {
        "type": "object",
        "properties": {
            "model_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Model IDs to combine",
            },
            "method": {
                "type": "string",
                "description": "Ensemble method",
                "enum": ["average", "ic_weighted", "stacking"],
                "default": "ic_weighted",
            },
            "weights": {
                "type": "array",
                "items": {"type": "number"},
                "description": "Custom weights for average method (optional)",
            },
            "ensemble_id": {
                "type": "string",
                "description": "Custom ensemble ID (auto-generated if omitted)",
            },
        },
        "required": ["model_ids"],
    }
    repeatable = True
    is_readonly = False

    def execute(self, **kwargs: Any) -> str:
        from src.ml.ensemble import EnsembleConfig, create_ensemble

        config = EnsembleConfig(
            model_ids=kwargs["model_ids"],
            method=kwargs.get("method", "ic_weighted"),
            weights=kwargs.get("weights"),
        )

        try:
            eid = create_ensemble(config, kwargs.get("ensemble_id"))
            return json.dumps({
                "status": "ok",
                "ensemble_id": eid,
                "method": config.method,
                "n_sub_models": len(config.model_ids),
                "next_step": f"Use model_id={eid!r} in backtest with mf_ml_predictor.",
            }, ensure_ascii=False, indent=2)
        except Exception as exc:
            return json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False)


class CheckModelHealthTool(BaseTool):
    """Check model health by comparing recent OOS performance with training CV."""

    name = "check_model_health"
    description = (
        "Evaluate alpha decay by computing recent OOS IC/AUC and comparing "
        "with the model's training-time CV scores. Reports drift_score and "
        "whether retraining is recommended. Can also analyze version drift "
        "trends to detect sustained decline."
    )
    parameters = {
        "type": "object",
        "properties": {
            "model_id": {
                "type": "string",
                "description": "Model ID to check (omit to check all models)",
            },
            "recent_period": {
                "type": "string",
                "description": "Period to evaluate on (e.g. '2026-04-01/2026-06-24')",
            },
            "base_id": {
                "type": "string",
                "description": "Base model ID for version drift analysis (e.g. 'lgb_csi300_binary_5d')",
            },
        },
    }
    repeatable = True

    def execute(self, **kwargs: Any) -> str:
        model_id = kwargs.get("model_id")
        recent_period = kwargs.get("recent_period", "")
        base_id = kwargs.get("base_id")

        if base_id:
            from src.ml.monitoring import compare_version_drift
            try:
                drift = compare_version_drift(base_id)
                return json.dumps({"status": "ok", **drift}, ensure_ascii=False, indent=2)
            except Exception as exc:
                return json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False)

        if not recent_period:
            return json.dumps({"status": "error", "error": "recent_period is required"}, ensure_ascii=False)

        if model_id:
            from src.ml.monitoring import evaluate_model_health
            try:
                report = evaluate_model_health(model_id, recent_period)
                return json.dumps({
                    "status": "ok",
                    "model_id": report.model_id,
                    "train_ic": report.train_ic_mean,
                    "recent_ic": report.recent_ic_mean,
                    "drift_score": report.drift_score,
                    "retrain_recommended": report.retrain_recommended,
                    "details": report.details,
                }, ensure_ascii=False, indent=2)
            except Exception as exc:
                return json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False)
        else:
            from src.ml.monitoring import check_all_models_health
            try:
                reports = check_all_models_health(recent_period)
                return json.dumps({
                    "status": "ok",
                    "n_models": len(reports),
                    "reports": [
                        {
                            "model_id": r.model_id,
                            "drift_score": r.drift_score,
                            "retrain_recommended": r.retrain_recommended,
                        }
                        for r in reports
                    ],
                }, ensure_ascii=False, indent=2)
            except Exception as exc:
                return json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False)


class ScheduleRetrainTool(BaseTool):
    """Configure or execute sliding-window model retraining."""

    name = "schedule_retrain"
    description = (
        "Set up or execute a sliding-window retraining schedule. "
        "Supports daily/weekly/monthly/quarterly retraining with rolling or "
        "expanding windows. Manages model versions and auto-cleans old ones."
    )
    parameters = {
        "type": "object",
        "properties": {
            "experiment_name": {
                "type": "string",
                "description": "Experiment config name (must exist in ~/.vibe-trading/experiments/)",
            },
            "retrain_freq": {
                "type": "string",
                "description": "How often to retrain",
                "enum": ["daily", "weekly", "monthly", "quarterly"],
                "default": "monthly",
            },
            "window_size_days": {
                "type": "integer",
                "description": "Training window in trading days (default: 504 = ~2 years)",
                "default": 504,
            },
            "window_type": {
                "type": "string",
                "description": "Window type",
                "enum": ["rolling", "expanding"],
                "default": "rolling",
            },
            "run_now": {
                "type": "boolean",
                "description": "Execute one retrain cycle immediately (default: false)",
                "default": False,
            },
            "as_of_date": {
                "type": "string",
                "description": "Simulate retrain as of this date (YYYY-MM-DD)",
            },
        },
        "required": ["experiment_name"],
    }
    repeatable = True
    is_readonly = False

    def execute(self, **kwargs: Any) -> str:
        from src.ml.scheduler import ModelSchedule, run_scheduled_retrain

        schedule = ModelSchedule(
            experiment_name=kwargs["experiment_name"],
            retrain_freq=kwargs.get("retrain_freq", "monthly"),
            window_size_days=kwargs.get("window_size_days", 504),
            window_type=kwargs.get("window_type", "rolling"),
        )

        if kwargs.get("run_now", False):
            try:
                version = run_scheduled_retrain(schedule, kwargs.get("as_of_date"))
                if version is None:
                    return json.dumps({"status": "ok", "message": "No retrain needed yet."}, ensure_ascii=False)
                return json.dumps({
                    "status": "ok",
                    "version": version.model_id,
                    "train_window": list(version.train_window),
                    "metrics": version.metrics,
                }, ensure_ascii=False, indent=2)
            except Exception as exc:
                return json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False)

        from src.ml.experiment import save_experiment, load_experiment
        try:
            config = load_experiment(kwargs["experiment_name"])
            save_experiment(config, kwargs["experiment_name"], schedule={
                "retrain_freq": schedule.retrain_freq,
                "window_size_days": schedule.window_size_days,
                "window_type": schedule.window_type,
                "max_versions": schedule.max_versions,
                "health_check": schedule.health_check,
            })
            return json.dumps({
                "status": "ok",
                "message": f"Schedule saved for {kwargs['experiment_name']}.",
                "schedule": {
                    "retrain_freq": schedule.retrain_freq,
                    "window_size_days": schedule.window_size_days,
                    "window_type": schedule.window_type,
                },
            }, ensure_ascii=False, indent=2)
        except Exception as exc:
            return json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False)
