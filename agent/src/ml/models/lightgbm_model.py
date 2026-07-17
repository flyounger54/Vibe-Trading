"""LightGBM model — optional dependency."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


class LightGBMPredictor:
    """LightGBM with auto objective detection and native NaN handling."""

    name = "lightgbm"

    _DEFAULT_PARAMS = {
        "num_leaves": 63,
        "learning_rate": 0.05,
        "n_estimators": 500,
        "min_child_samples": 50,
        "subsample": 0.8,
        "colsample_bytree": 0.8,
        "verbose": -1,
    }

    def __init__(self, **params: Any) -> None:
        self._params = {**self._DEFAULT_PARAMS, **params}
        self._model: Any = None
        self._feature_names: list[str] = []
        self._is_classifier = False
        self._calibrator: Any = None

    def fit(self, X: np.ndarray, y: np.ndarray, **kwargs: Any) -> None:
        import lightgbm as lgb

        self._feature_names = kwargs.get("feature_names", [])
        unique_y = np.unique(y[~np.isnan(y)])
        self._is_classifier = len(unique_y) <= 10

        params = dict(self._params)
        if self._is_classifier:
            params.setdefault("objective", "binary")
            params.setdefault("metric", "auc")
            self._model = lgb.LGBMClassifier(**params)
        else:
            params.setdefault("objective", "regression")
            params.setdefault("metric", "mse")
            self._model = lgb.LGBMRegressor(**params)

        self._model.fit(X, y)

    def _predict_raw(self, X: np.ndarray) -> np.ndarray:
        import lightgbm as lgb

        if isinstance(self._model, lgb.Booster):
            return self._model.predict(X)
        if self._is_classifier and hasattr(self._model, "predict_proba"):
            return self._model.predict_proba(X)[:, 1]
        return self._model.predict(X)

    def predict(self, X: np.ndarray) -> np.ndarray:
        return self._predict_raw(X)

    def predict_proba(self, X: np.ndarray) -> np.ndarray | None:
        if self._calibrator is not None:
            return self._calibrator.predict_proba(X)[:, 1]
        if self._is_classifier:
            return self._predict_raw(X)
        return None

    def calibrate(self, X_val: np.ndarray, y_val: np.ndarray) -> None:
        if not self._is_classifier:
            return
        try:
            from sklearn.calibration import CalibratedClassifierCV
            self._calibrator = CalibratedClassifierCV(
                self._model, method="isotonic", cv="prefit"
            )
            self._calibrator.fit(X_val, y_val)
        except Exception as exc:
            logger.warning("Calibration failed: %s", exc)
            self._calibrator = None

    def get_feature_importance(self) -> dict[str, float] | None:
        import lightgbm as lgb

        if self._model is None:
            return None
        if isinstance(self._model, lgb.Booster):
            importance = np.array(self._model.feature_importance(importance_type="gain"), dtype=float)
        else:
            importance = self._model.feature_importances_.astype(float)
        if not self._feature_names or len(self._feature_names) != len(importance):
            return None
        total = importance.sum()
        if total == 0:
            return None
        pct = importance / total
        return dict(zip(self._feature_names, pct.tolist()))

    def get_params(self) -> dict[str, Any]:
        return dict(self._params)

    def save(self, path: Path) -> None:
        import joblib
        path.mkdir(parents=True, exist_ok=True)
        self._model.booster_.save_model(str(path / "model.txt"))
        meta = {
            "name": self.name,
            "params": self._params,
            "feature_names": self._feature_names,
            "is_classifier": self._is_classifier,
        }
        (path / "model_meta.json").write_text(json.dumps(meta, default=str), encoding="utf-8")
        if self._calibrator is not None:
            joblib.dump(self._calibrator, path / "calibrator.joblib")

    @classmethod
    def load(cls, path: Path) -> LightGBMPredictor:
        import lightgbm as lgb

        meta = json.loads((path / "model_meta.json").read_text(encoding="utf-8"))
        obj = cls.__new__(cls)
        obj._params = meta.get("params", {})
        obj._feature_names = meta.get("feature_names", [])
        obj._is_classifier = meta.get("is_classifier", False)

        booster = lgb.Booster(model_file=str(path / "model.txt"))
        obj._model = booster

        calibrator_path = path / "calibrator.joblib"
        if calibrator_path.exists():
            import joblib
            obj._calibrator = joblib.load(calibrator_path)
        else:
            obj._calibrator = None
        return obj
