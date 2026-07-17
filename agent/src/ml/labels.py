"""Label construction: multi-horizon, multi-type, excess returns, transaction costs."""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from src.ml.base_model import LabelConfig

logger = logging.getLogger(__name__)


def build_labels(
    panel: dict[str, pd.DataFrame],
    config: LabelConfig,
) -> pd.Series:
    """Construct labels aligned to feature matrix MultiIndex(date, code).

    Supports:
    - Multiple horizons (1, 5, 7, 14 days)
    - Excess returns over benchmark
    - Transaction cost deduction
    - Four label types: return, rank, binary, top_bottom
    """
    close = panel.get("close")
    if close is None:
        raise ValueError("panel missing 'close' for label construction")

    raw_returns = close.shift(-config.horizon) / close - 1.0

    if config.benchmark is not None:
        bench_returns = _load_benchmark_returns(panel, config.benchmark, config.horizon)
        if bench_returns is not None:
            raw_returns = raw_returns.sub(bench_returns, axis=0)

    if config.cost_bps > 0:
        raw_returns = raw_returns - config.cost_bps / 10000.0

    if config.label_type == "return":
        labels_wide = raw_returns

    elif config.label_type == "rank":
        labels_wide = raw_returns.rank(axis=1, pct=True, na_option="keep")

    elif config.label_type == "binary":
        labels_wide = (raw_returns > config.threshold).astype(float)
        labels_wide = labels_wide.where(raw_returns.notna())

    elif config.label_type == "top_bottom":
        q = config.quantile_pct
        labels_wide = _top_bottom_labels(raw_returns, q)

    else:
        raise ValueError(f"Unknown label_type: {config.label_type!r}")

    try:
        stacked = labels_wide.stack(dropna=False)
    except (ValueError, TypeError):
        stacked = labels_wide.stack(future_stack=True)
    stacked.index.names = ["date", "code"]
    stacked = stacked.dropna()
    stacked.name = config.key
    return stacked


def _top_bottom_labels(
    returns: pd.DataFrame, quantile_pct: float
) -> pd.DataFrame:
    """Label top quantile as +1, bottom as -1, middle as NaN (dropped)."""
    ranks = returns.rank(axis=1, pct=True, na_option="keep")
    result = pd.DataFrame(np.nan, index=returns.index, columns=returns.columns)
    result = result.where(~(ranks >= 1.0 - quantile_pct), other=1.0)
    result = result.where(~(ranks <= quantile_pct), other=-1.0)
    return result


def _load_benchmark_returns(
    panel: dict[str, pd.DataFrame],
    benchmark: str,
    horizon: int,
) -> pd.Series | None:
    """Load benchmark returns as a Series indexed by date."""
    try:
        from src.market_data import fetch_market_data

        bench_df = fetch_market_data(benchmark, source="auto")
        if bench_df is None or bench_df.empty:
            return None
        bench_close = bench_df.set_index("date")["close"]
        bench_ret = bench_close.shift(-horizon) / bench_close - 1.0
        bench_ret.index = pd.DatetimeIndex(bench_ret.index)
        return bench_ret
    except Exception as exc:
        logger.warning("Failed to load benchmark %s: %s", benchmark, exc)
        return None


def build_multi_horizon_labels(
    panel: dict[str, pd.DataFrame],
    configs: list[LabelConfig],
) -> pd.DataFrame:
    """Build labels for multiple horizons/types, aligned to the same MultiIndex.

    Returns DataFrame with columns named by each config's key.
    """
    series_dict = {}
    for cfg in configs:
        series_dict[cfg.key] = build_labels(panel, cfg)

    return pd.DataFrame(series_dict)
