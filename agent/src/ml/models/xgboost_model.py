"""XGBoost model — optional dependency."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


class XGBoostPredictor:
    """XGBoost with auto objective detection and native NaN handling."""

    name = "xgboost"

    _DEFAULT_PARAMS = {
        "max_depth": 6,
        "learning_rate": 0.05,
        "n_estimators": 500,
        "subsample": 0.8,
        "colsample_bytree": 0.8,
        "verbosity": 0,
    }

    def __init__(self, **params: Any) -> None:
        self._params = {**self._DEFAULT_PARAMS, **params}
        self._model: Any = None
        self._feature_names: list[str] = []
        self._is_classifier = False
        self._calibrator: Any = None

    def fit(self, X: np.ndarray, y: np.ndarray, **kwargs: Any) -> None:
        import xgboost as xgb

        self._feature_names = kwargs.get("feature_names", [])
        unique_y = np.unique(y[~np.isnan(y)])
        self._is_classifier = len(unique_y) <= 10

        params = dict(self._params)
        if self._is_classifier:
            params.setdefault("objective", "binary:logistic")
            params.setdefault("eval_metric", "auc")
            self._model = xgb.XGBClassifier(**params)
        else:
            params.setdefault("objective", "reg:squarederror")
            self._model = xgb.XGBRegressor(**params)

        self._model.fit(X, y)

    def predict(self, X: np.ndarray) -> np.ndarray:
        if self._is_classifier and hasattr(self._model, "predict_proba"):
            return self._model.predict_proba(X)[:, 1]
        return self._model.predict(X)

    def predict_proba(self, X: np.ndarray) -> np.ndarray | None:
        if self._calibrator is not None:
            return self._calibrator.predict_proba(X)[:, 1]
        if self._is_classifier and hasattr(self._model, "predict_proba"):
            return self._model.predict_proba(X)[:, 1]
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
        if self._model is None:
            return None
        importance = self._model.feature_importances_
        if len(self._feature_names) != len(importance):
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
        self._model.save_model(str(path / "model.json"))
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
    def load(cls, path: Path) -> XGBoostPredictor:
        import xgboost as xgb

        meta = json.loads((path / "model_meta.json").read_text(encoding="utf-8"))
        obj = cls.__new__(cls)
        obj._params = meta.get("params", {})
        obj._feature_names = meta.get("feature_names", [])
        obj._is_classifier = meta.get("is_classifier", False)

        if obj._is_classifier:
            obj._model = xgb.XGBClassifier(**obj._params)
        else:
            obj._model = xgb.XGBRegressor(**obj._params)
        obj._model.load_model(str(path / "model.json"))

        calibrator_path = path / "calibrator.joblib"
        if calibrator_path.exists():
            import joblib
            obj._calibrator = joblib.load(calibrator_path)
        else:
            obj._calibrator = None
        return obj
