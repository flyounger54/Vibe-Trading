"""PyTorch 3-layer MLP Predictor implementation."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


class DeepNNPredictor:
    """Simple 3-layer MLP for tabular factor data.

    Architecture: Input → 256 → 128 → 1, with BatchNorm + Dropout.
    Requires: pip install 'vibe-trading[ml-deep]'
    """

    name = "deep_nn"

    def __init__(self, **params: Any) -> None:
        self._params = {
            "hidden_dims": [256, 128],
            "dropout": 0.3,
            "lr": 1e-3,
            "epochs": 50,
            "batch_size": 2048,
            "task": "regression",  # "regression" | "classification"
            **params,
        }
        self._model = None
        self._scaler = None
        self._feature_importance_: dict[str, float] | None = None
        self._calibrator = None
        self._n_features = 0

    def _build_model(self, n_features: int) -> Any:
        import torch
        import torch.nn as nn

        hidden = self._params["hidden_dims"]
        dropout = self._params["dropout"]
        task = self._params["task"]

        layers = []
        in_dim = n_features
        for h in hidden:
            layers.extend([
                nn.Linear(in_dim, h),
                nn.BatchNorm1d(h),
                nn.ReLU(),
                nn.Dropout(dropout),
            ])
            in_dim = h

        if task == "classification":
            layers.append(nn.Linear(in_dim, 1))
            layers.append(nn.Sigmoid())
        else:
            layers.append(nn.Linear(in_dim, 1))

        return nn.Sequential(*layers)

    def fit(self, X: np.ndarray, y: np.ndarray, **kwargs: Any) -> None:
        import torch
        import torch.nn as nn
        from sklearn.preprocessing import StandardScaler

        self._n_features = X.shape[1]
        self._scaler = StandardScaler()
        X_clean = np.nan_to_num(X, nan=0.0)
        X_scaled = self._scaler.fit_transform(X_clean).astype(np.float32)
        y_arr = y.astype(np.float32).reshape(-1, 1)

        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self._model = self._build_model(self._n_features).to(device)

        task = self._params["task"]
        if task == "classification":
            criterion = nn.BCELoss()
        else:
            criterion = nn.MSELoss()

        optimizer = torch.optim.Adam(self._model.parameters(), lr=self._params["lr"])
        batch_size = self._params["batch_size"]
        epochs = self._params["epochs"]

        X_t = torch.from_numpy(X_scaled).to(device)
        y_t = torch.from_numpy(y_arr).to(device)
        dataset = torch.utils.data.TensorDataset(X_t, y_t)
        loader = torch.utils.data.DataLoader(dataset, batch_size=batch_size, shuffle=True)

        self._model.train()
        for epoch in range(epochs):
            for batch_X, batch_y in loader:
                optimizer.zero_grad()
                pred = self._model(batch_X)
                loss = criterion(pred, batch_y)
                loss.backward()
                optimizer.step()

    def predict(self, X: np.ndarray) -> np.ndarray:
        import torch

        if self._model is None or self._scaler is None:
            raise RuntimeError("Model not fitted")

        self._model.eval()
        device = next(self._model.parameters()).device
        X_clean = np.nan_to_num(X, nan=0.0)
        X_scaled = self._scaler.transform(X_clean).astype(np.float32)
        X_t = torch.from_numpy(X_scaled).to(device)

        with torch.no_grad():
            preds = self._model(X_t).cpu().numpy().flatten()
        return preds

    def predict_proba(self, X: np.ndarray) -> np.ndarray | None:
        if self._params["task"] != "classification":
            return None

        raw = self.predict(X)

        if self._calibrator is not None:
            return self._calibrator.predict_proba(raw.reshape(-1, 1))[:, 1]

        return raw

    def calibrate(self, X_val: np.ndarray, y_val: np.ndarray) -> None:
        if self._params["task"] != "classification":
            return

        from sklearn.calibration import CalibratedClassifierCV
        from sklearn.base import BaseEstimator, ClassifierMixin

        raw_preds = self.predict(X_val)

        class _Wrapper(BaseEstimator, ClassifierMixin):
            classes_ = np.array([0, 1])

            def __init__(self, preds: np.ndarray):
                self._preds = preds

            def fit(self, X, y):
                return self

            def predict_proba(self, X):
                p = self._preds[:len(X)]
                return np.column_stack([1 - p, p])

            def predict(self, X):
                return (self.predict_proba(X)[:, 1] > 0.5).astype(int)

        wrapper = _Wrapper(raw_preds)
        cal = CalibratedClassifierCV(wrapper, cv="prefit", method="isotonic")
        cal.fit(raw_preds.reshape(-1, 1), y_val)
        self._calibrator = cal

    def get_feature_importance(self) -> dict[str, float] | None:
        return self._feature_importance_

    def get_params(self) -> dict[str, Any]:
        return dict(self._params)

    def save(self, path: Path) -> None:
        import torch
        import joblib

        path.mkdir(parents=True, exist_ok=True)

        if self._model is not None:
            torch.save(self._model.state_dict(), path / "model.pt")

        meta = {
            "name": self.name,
            "params": self._params,
            "n_features": self._n_features,
        }
        (path / "model_meta.json").write_text(json.dumps(meta), encoding="utf-8")

        if self._scaler is not None:
            joblib.dump(self._scaler, path / "scaler.joblib")

        if self._calibrator is not None:
            joblib.dump(self._calibrator, path / "calibrator.joblib")

    @classmethod
    def load(cls, path: Path) -> DeepNNPredictor:
        import torch
        import joblib

        meta = json.loads((path / "model_meta.json").read_text(encoding="utf-8"))
        obj = cls(**meta["params"])
        obj._n_features = meta["n_features"]

        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        obj._model = obj._build_model(obj._n_features).to(device)

        state_path = path / "model.pt"
        if state_path.exists():
            obj._model.load_state_dict(torch.load(state_path, map_location=device, weights_only=True))
            obj._model.eval()

        scaler_path = path / "scaler.joblib"
        if scaler_path.exists():
            obj._scaler = joblib.load(scaler_path)

        cal_path = path / "calibrator.joblib"
        if cal_path.exists():
            obj._calibrator = joblib.load(cal_path)

        return obj
