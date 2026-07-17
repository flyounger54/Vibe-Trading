"""Ridge regression model — built-in fallback (no extra dependencies)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import joblib
import numpy as np

logger = logging.getLogger(__name__)


class RidgePredictor:
    """sklearn Ridge with median imputation and standard scaling."""

    name = "ridge"

    def __init__(self, **params: Any) -> None:
        from sklearn.linear_model import Ridge
        from sklearn.pipeline import Pipeline
        from sklearn.preprocessing import StandardScaler

        self._params = {"alpha": 1.0, **params}
        ridge_params = {k: v for k, v in self._params.items() if k in {"alpha", "fit_intercept", "max_iter", "tol"}}
        self._model = Pipeline([
            ("scaler", StandardScaler()),
            ("ridge", Ridge(**ridge_params)),
        ])
        self._feature_names: list[str] = []
        self._medians: np.ndarray | None = None
        self._calibrator: Any = None

    def fit(self, X: np.ndarray, y: np.ndarray, **kwargs: Any) -> None:
        self._feature_names = kwargs.get("feature_names", [])
        self._medians = np.nanmedian(X, axis=0)
        X_filled = np.where(np.isnan(X), self._medians, X)
        self._model.fit(X_filled, y)

    def predict(self, X: np.ndarray) -> np.ndarray:
        if self._medians is not None:
            X = np.where(np.isnan(X), self._medians, X)
        return self._model.predict(X)

    def predict_proba(self, X: np.ndarray) -> np.ndarray | None:
        if self._calibrator is not None:
            if self._medians is not None:
                X = np.where(np.isnan(X), self._medians, X)
            return self._calibrator.predict_proba(X)[:, 1]
        return None

    def calibrate(self, X_val: np.ndarray, y_val: np.ndarray) -> None:
        unique = np.unique(y_val[~np.isnan(y_val)])
        if len(unique) > 10:
            return
        from sklearn.calibration import CalibratedClassifierCV

        if self._medians is not None:
            X_val = np.where(np.isnan(X_val), self._medians, X_val)
        try:
            self._calibrator = CalibratedClassifierCV(
                self._model, method="isotonic", cv="prefit"
            )
            self._calibrator.fit(X_val, y_val)
        except Exception as exc:
            logger.warning("Calibration failed: %s", exc)
            self._calibrator = None

    def get_feature_importance(self) -> dict[str, float] | None:
        coefs = self._model.named_steps["ridge"].coef_
        if len(self._feature_names) != len(coefs):
            return None
        importance = np.abs(coefs)
        total = importance.sum()
        if total == 0:
            return None
        pct = importance / total
        return dict(zip(self._feature_names, pct.tolist()))

    def get_params(self) -> dict[str, Any]:
        return dict(self._params)

    def save(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        joblib.dump(self._model, path / "model.joblib")
        if self._medians is not None:
            np.save(path / "medians.npy", self._medians)
        if self._calibrator is not None:
            joblib.dump(self._calibrator, path / "calibrator.joblib")
        meta = {
            "name": self.name,
            "params": self._params,
            "feature_names": self._feature_names,
        }
        (path / "model_meta.json").write_text(json.dumps(meta, default=str), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> RidgePredictor:
        meta = json.loads((path / "model_meta.json").read_text(encoding="utf-8"))
        obj = cls.__new__(cls)
        obj._params = meta.get("params", {})
        obj._feature_names = meta.get("feature_names", [])
        obj._model = joblib.load(path / "model.joblib")
        medians_path = path / "medians.npy"
        obj._medians = np.load(medians_path) if medians_path.exists() else None
        calibrator_path = path / "calibrator.joblib"
        obj._calibrator = joblib.load(calibrator_path) if calibrator_path.exists() else None
        return obj
