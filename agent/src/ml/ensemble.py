"""Model ensemble: combine multiple trained models into a single Predictor."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

_MODELS_DIR = Path.home() / ".vibe-trading" / "models"


@dataclass(frozen=True)
class EnsembleConfig:
    model_ids: list[str]
    method: str = "average"  # "average" | "ic_weighted" | "stacking"
    weights: list[float] | None = None


class EnsemblePredictor:
    """Combine multiple trained models. Itself satisfies the Predictor protocol."""

    name = "ensemble"

    def __init__(self, config: EnsembleConfig, models_dir: Path | None = None) -> None:
        self._config = config
        self._models_dir = models_dir or _MODELS_DIR
        self._sub_models: list[Any] = []
        self._sub_metadata: list[dict] = []
        self._weights: np.ndarray | None = None
        self._meta_learner: Any = None
        self._feature_names: list[str] = []

    def _ensure_loaded(self) -> None:
        if self._sub_models:
            return
        from src.ml.storage import load_model

        for mid in self._config.model_ids:
            model, meta = load_model(mid, self._models_dir)
            self._sub_models.append(model)
            self._sub_metadata.append(meta)

        if not self._sub_models:
            raise RuntimeError("No sub-models loaded for ensemble")

        self._feature_names = self._sub_metadata[0].get("factor_ids", [])
        self._weights = self._compute_weights()

    def _compute_weights(self) -> np.ndarray:
        n = len(self._sub_models)

        if self._config.weights is not None:
            w = np.array(self._config.weights[:n], dtype=float)
            return w / w.sum()

        if self._config.method == "ic_weighted":
            ics = []
            for meta in self._sub_metadata:
                cv = meta.get("cv_summary", {})
                ic = abs(cv.get("test_ic_mean", 0.0))
                ics.append(max(ic, 1e-8))
            w = np.array(ics)
            return w / w.sum()

        return np.ones(n) / n

    def fit(self, X: np.ndarray, y: np.ndarray, **kwargs: Any) -> None:
        """For stacking: train meta-learner on sub-model predictions."""
        self._ensure_loaded()
        if self._config.method != "stacking":
            return

        from sklearn.linear_model import Ridge

        sub_preds = np.column_stack([m.predict(X) for m in self._sub_models])
        self._meta_learner = Ridge(alpha=1.0)
        self._meta_learner.fit(sub_preds, y)

    def predict(self, X: np.ndarray) -> np.ndarray:
        self._ensure_loaded()

        if self._config.method == "stacking" and self._meta_learner is not None:
            sub_preds = np.column_stack([m.predict(X) for m in self._sub_models])
            return self._meta_learner.predict(sub_preds)

        predictions = np.zeros(len(X))
        for i, model in enumerate(self._sub_models):
            predictions += self._weights[i] * model.predict(X)
        return predictions

    def predict_proba(self, X: np.ndarray) -> np.ndarray | None:
        self._ensure_loaded()
        probas = []
        for model in self._sub_models:
            p = model.predict_proba(X)
            if p is not None:
                probas.append(p)

        if not probas:
            return None

        if self._config.method == "stacking" and self._meta_learner is not None:
            return None

        result = np.zeros(len(X))
        total_w = 0.0
        for i, p in enumerate(probas):
            w = self._weights[i] if self._weights is not None else 1.0 / len(probas)
            result += w * p
            total_w += w
        return result / total_w if total_w > 0 else result

    def calibrate(self, X_val: np.ndarray, y_val: np.ndarray) -> None:
        pass

    def get_feature_importance(self) -> dict[str, float] | None:
        self._ensure_loaded()
        combined: dict[str, float] = {}
        for i, model in enumerate(self._sub_models):
            imp = model.get_feature_importance()
            if imp is None:
                continue
            w = self._weights[i] if self._weights is not None else 1.0 / len(self._sub_models)
            for k, v in imp.items():
                combined[k] = combined.get(k, 0.0) + w * v
        return combined or None

    def get_params(self) -> dict[str, Any]:
        return {
            "method": self._config.method,
            "model_ids": self._config.model_ids,
            "weights": self._weights.tolist() if self._weights is not None else None,
        }

    def save(self, path: Path) -> None:
        import joblib

        path.mkdir(parents=True, exist_ok=True)
        meta = {
            "name": self.name,
            "config": {
                "model_ids": self._config.model_ids,
                "method": self._config.method,
                "weights": self._config.weights,
            },
            "computed_weights": self._weights.tolist() if self._weights is not None else None,
            "feature_names": self._feature_names,
        }
        (path / "model_meta.json").write_text(
            json.dumps(meta, default=str), encoding="utf-8"
        )
        if self._meta_learner is not None:
            joblib.dump(self._meta_learner, path / "meta_learner.joblib")

    @classmethod
    def load(cls, path: Path) -> EnsemblePredictor:
        meta = json.loads((path / "model_meta.json").read_text(encoding="utf-8"))
        config_data = meta["config"]
        config = EnsembleConfig(
            model_ids=config_data["model_ids"],
            method=config_data["method"],
            weights=config_data.get("weights"),
        )
        obj = cls(config)
        obj._feature_names = meta.get("feature_names", [])

        if meta.get("computed_weights"):
            obj._weights = np.array(meta["computed_weights"])

        meta_learner_path = path / "meta_learner.joblib"
        if meta_learner_path.exists():
            import joblib
            obj._meta_learner = joblib.load(meta_learner_path)

        return obj


def create_ensemble(
    config: EnsembleConfig,
    ensemble_id: str | None = None,
    models_dir: Path | None = None,
) -> str:
    """Create and save an ensemble. Returns the ensemble model_id."""
    from src.ml.storage import save_model

    base = models_dir or _MODELS_DIR
    if ensemble_id is None:
        method_tag = config.method
        now = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        ensemble_id = f"ensemble_{method_tag}_{now}"

    predictor = EnsemblePredictor(config, models_dir)
    predictor._ensure_loaded()

    if config.method == "stacking":
        first_meta = predictor._sub_metadata[0]
        factor_ids = first_meta.get("factor_ids", [])
        logger.info("Stacking ensemble: fitting meta-learner requires training data. Skipping auto-fit.")

    metadata = {
        "model_type": "ensemble",
        "ensemble_method": config.method,
        "sub_model_ids": config.model_ids,
        "weights": predictor._weights.tolist() if predictor._weights is not None else None,
        "factor_ids": predictor._feature_names,
        "n_sub_models": len(config.model_ids),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    sub_cv_ics = []
    for meta in predictor._sub_metadata:
        cv = meta.get("cv_summary", {})
        sub_cv_ics.append(cv.get("test_ic_mean", 0.0))
    metadata["sub_model_ics"] = sub_cv_ics

    save_model(predictor, ensemble_id, metadata, models_dir)
    logger.info("Created ensemble %s with %d sub-models (%s)", ensemble_id, len(config.model_ids), config.method)
    return ensemble_id
