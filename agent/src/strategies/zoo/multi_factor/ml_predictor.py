"""ML Predictor strategy: dual-mode signal engine (fixed model / rolling window).

Loads a pre-trained model, computes alpha-zoo factors, predicts forward
returns, and generates position signals based on cross-sectional rank.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

__strategy_meta__ = {
    "id": "mf_ml_predictor",
    "nickname": "ML预测组合",
    "category": "multi_factor",
    "description": (
        "Load a pre-trained ML model (LightGBM/XGBoost/Ridge), compute alpha-zoo "
        "factors as features, predict forward returns, cross-sectionally rank, "
        "and go long on top quintile. Supports fixed model or rolling-window "
        "schedule mode that auto-switches model versions by date."
    ),
    "universe": ["equity_cn", "equity_us"],
    "frequency": ["1D"],
    "columns_required": ["open", "high", "low", "close", "volume"],
    "default_params": {
        "model_id": "",
        "schedule_name": "",
        "top_pct": 0.2,
        "bottom_pct": 0.0,
        "rebalance_days": 5,
        "use_proba": False,
    },
    "risk_profile": "medium",
    "min_bars": 60,
    "reference": "ML model trained via train_model tool",
    "factors_used": [],
}


class SignalEngine:
    """Dual-mode ML signal engine."""

    def __init__(self, **params: Any) -> None:
        self.model_id: str = params.get("model_id", "")
        self.schedule_name: str = params.get("schedule_name", "")
        self.top_pct: float = float(params.get("top_pct", 0.2))
        self.bottom_pct: float = float(params.get("bottom_pct", 0.0))
        self.rebalance_days: int = int(params.get("rebalance_days", 5))
        self.use_proba: bool = bool(params.get("use_proba", False))

        self.is_configured = bool(self.model_id or self.schedule_name)

    def generate(
        self, data_map: Dict[str, pd.DataFrame]
    ) -> Dict[str, pd.Series]:
        if not self.is_configured:
            if data_map:
                logger.warning(
                    "mf_ml_predictor has no model_id or schedule_name; "
                    "returning neutral signals"
                )
            return _zero_signals(data_map)
        if self.schedule_name:
            return self._generate_rolling(data_map)
        return self._generate_fixed(data_map)

    def _generate_fixed(
        self, data_map: Dict[str, pd.DataFrame]
    ) -> Dict[str, pd.Series]:
        """Single model for all dates."""
        from src.ml.storage import load_model

        model, metadata = load_model(self.model_id)
        panel = _data_map_to_panel(data_map)
        factor_ids = metadata.get("factor_ids", [])

        predictions = _predict_with_model(model, metadata, panel, factor_ids)
        if predictions is None:
            return _zero_signals(data_map)

        return self._predictions_to_signals(predictions, data_map)

    def _generate_rolling(
        self, data_map: Dict[str, pd.DataFrame]
    ) -> Dict[str, pd.Series]:
        """Switch model versions by date."""
        from src.ml.storage import load_model

        versions = _load_schedule_versions(self.schedule_name)
        if not versions:
            return _zero_signals(data_map)

        panel = _data_map_to_panel(data_map)
        all_dates = _get_all_dates(data_map)

        all_predictions: dict[str, pd.Series] = {}

        for i, version in enumerate(versions):
            valid_start = pd.Timestamp(version["train_window"][1])
            if i + 1 < len(versions):
                valid_end = pd.Timestamp(versions[i + 1]["train_window"][1])
            else:
                valid_end = pd.Timestamp("2099-12-31")

            segment_dates = [d for d in all_dates if valid_start <= d < valid_end]
            if not segment_dates:
                continue

            model, metadata = load_model(version["model_id"])
            factor_ids = metadata.get("factor_ids", [])

            segment_panel = _slice_panel(panel, segment_dates)
            predictions = _predict_with_model(model, metadata, segment_panel, factor_ids)

            if predictions is not None:
                for code, series in predictions.items():
                    if code not in all_predictions:
                        all_predictions[code] = series
                    else:
                        all_predictions[code] = pd.concat([all_predictions[code], series]).sort_index()
                        all_predictions[code] = all_predictions[code][~all_predictions[code].index.duplicated(keep="last")]

        if not all_predictions:
            return _zero_signals(data_map)

        return self._predictions_to_signals(all_predictions, data_map)

    def _predictions_to_signals(
        self,
        predictions: dict[str, pd.Series],
        data_map: Dict[str, pd.DataFrame],
    ) -> Dict[str, pd.Series]:
        """Convert prediction scores to trading signals via cross-sectional ranking."""
        pred_df = pd.DataFrame(predictions)
        if pred_df.empty:
            return _zero_signals(data_map)

        ranked = pred_df.rank(axis=1, pct=True, na_option="keep")

        signal_df = pd.DataFrame(0.0, index=pred_df.index, columns=pred_df.columns)

        if self.use_proba:
            long_mask = ranked >= (1.0 - self.top_pct)
            signal_df = signal_df.where(~long_mask, pred_df)
            if self.bottom_pct > 0:
                short_mask = ranked <= self.bottom_pct
                signal_df = signal_df.where(~short_mask, -pred_df.abs())
        else:
            long_mask = ranked >= (1.0 - self.top_pct)
            signal_df = signal_df.where(~long_mask, other=1.0)
            if self.bottom_pct > 0:
                short_mask = ranked <= self.bottom_pct
                signal_df = signal_df.where(~short_mask, other=-1.0)

        signal_df = signal_df.where(pred_df.notna(), other=0.0)

        if self.rebalance_days > 1:
            signal_df = _apply_rebalance(signal_df, self.rebalance_days)

        result: Dict[str, pd.Series] = {}
        for code in signal_df.columns:
            if code in data_map:
                result[code] = signal_df[code].reindex(data_map[code].index, fill_value=0.0)
        return result


def _data_map_to_panel(
    data_map: Dict[str, pd.DataFrame],
) -> dict[str, pd.DataFrame]:
    """Convert backtest engine format to factor zoo panel format."""
    fields = ["open", "high", "low", "close", "volume", "amount"]
    panel: dict[str, pd.DataFrame] = {}
    for field in fields:
        series_dict = {}
        for code, df in data_map.items():
            if field in df.columns:
                series_dict[code] = df[field].astype(float)
        if series_dict:
            panel[field] = pd.DataFrame(series_dict)
    return panel


def _predict_with_model(
    model: Any,
    metadata: dict,
    panel: dict[str, pd.DataFrame],
    factor_ids: list[str],
) -> dict[str, pd.Series] | None:
    """Compute features and run model prediction, return wide predictions."""
    from src.ml.base_model import PreprocessConfig
    from src.ml.features import build_feature_matrix, preprocess_features

    try:
        features = build_feature_matrix(panel, factor_ids=factor_ids)
    except Exception:
        return None

    if features.empty:
        return None

    pp_raw = metadata.get("preprocess_config", {})
    pp_config = PreprocessConfig(
        winsorize=pp_raw.get("winsorize", True),
        zscore=pp_raw.get("zscore", True),
        fillna_strategy=pp_raw.get("fillna_strategy", "median"),
    )
    features, _ = preprocess_features(features, pp_config)

    X = features.values
    preds = model.predict(X)

    pred_series = pd.Series(preds, index=features.index)
    result: dict[str, pd.Series] = {}
    for code in features.index.get_level_values("code").unique():
        mask = features.index.get_level_values("code") == code
        result[code] = pred_series.loc[mask].droplevel("code")

    return result


def _load_schedule_versions(schedule_name: str) -> list[dict]:
    """Load model versions for a schedule."""
    import json
    from pathlib import Path

    versions_path = Path.home() / ".vibe-trading" / "models" / "versions.json"
    if not versions_path.exists():
        return []

    try:
        data = json.loads(versions_path.read_text(encoding="utf-8"))
        return data.get(schedule_name, [])
    except Exception:
        return []


def _get_all_dates(data_map: Dict[str, pd.DataFrame]) -> list[pd.Timestamp]:
    all_idx = set()
    for df in data_map.values():
        all_idx.update(df.index)
    return sorted(all_idx)


def _slice_panel(
    panel: dict[str, pd.DataFrame], dates: list,
) -> dict[str, pd.DataFrame]:
    return {k: df.loc[df.index.isin(dates)] for k, df in panel.items()}


def _apply_rebalance(
    signal_df: pd.DataFrame, rebalance_days: int,
) -> pd.DataFrame:
    """Hold positions for rebalance_days, only update at rebalance points."""
    result = signal_df.copy()
    dates = result.index
    for i in range(len(dates)):
        if i % rebalance_days != 0 and i > 0:
            result.iloc[i] = result.iloc[i - 1]
    return result


def _zero_signals(data_map: Dict[str, pd.DataFrame]) -> Dict[str, pd.Series]:
    return {code: pd.Series(0.0, index=df.index) for code, df in data_map.items()}
