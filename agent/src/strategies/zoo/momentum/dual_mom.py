
# ============================================================
# 中文名称: 双动量策略
# 简要说明: 结合绝对动量（时间序列：收益率 > 0?）和相对动量（截面：
#           该标的是否排名靠前？）。绝对动量为正且相对排名前50%→全仓做多(1.0)，
#           绝对为正但排名后50%→半仓做多(0.5)，绝对为负→平仓或轻微做空(-0.3)。
#           信号值 ∈ [-1, 1]，NaN 安全，无前瞻偏差。
# 典型用途: 全球资产轮动配置，股票池动量选股。
# ============================================================
"""Dual Momentum (mom_dual).

Combines absolute momentum (time-series: is return > 0?) with relative
momentum (cross-sectional: is this instrument the best performer?).
Absolute positive + relative top half -> full long (1.0).
Absolute positive + relative bottom half -> half long (0.5).
Absolute negative -> flat (0.0) or slight short (-0.3).
"""

from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd

__strategy_meta__ = {
    "id": "mom_dual",
    "nickname": "双动量",
    "category": "momentum",
    "description": (
        "Dual momentum (Antonacci). Combines absolute momentum (time-series: "
        "is return > 0?) and relative momentum (cross-sectional: is this "
        "instrument the best performer?). Full long when both positive, "
        "half long when only absolute positive, flat or slight short when "
        "absolute negative."
    ),
    "universe": ["equity_us", "equity_hk"],
    "frequency": ["1D"],
    "columns_required": ["close"],
    "default_params": {"abs_lookback": 252, "rel_lookback": 60, "rebalance_days": 20},
    "risk_profile": "medium",
    "min_bars": 260,
    "reference": "Antonacci, Dual Momentum Investing, 2014",
    "factors_used": [],
}


class SignalEngine:
    """双动量信号引擎。"""

    def __init__(self, **params: Any) -> None:
        self.abs_lookback: int = int(params.get("abs_lookback", 252))
        self.rel_lookback: int = int(params.get("rel_lookback", 60))
        self.rebalance_days: int = int(params.get("rebalance_days", 20))
        if self.abs_lookback < 1:
            raise ValueError(f"abs_lookback ({self.abs_lookback}) must be >= 1")
        if self.rel_lookback < 1:
            raise ValueError(f"rel_lookback ({self.rel_lookback}) must be >= 1")
        if self.rebalance_days < 1:
            raise ValueError(
                f"rebalance_days ({self.rebalance_days}) must be >= 1"
            )

    def generate(
        self, data_map: Dict[str, pd.DataFrame]
    ) -> Dict[str, pd.Series]:
        """为所有标的生成双动量信号。

        Step 1: Compute absolute momentum (time-series) per instrument.
        Step 2: Compute relative momentum (cross-sectional rank) across
                all instruments.
        Step 3: Combine into final signal with rebalance cadence.

        Returns:
            Dict mapping ticker -> pd.Series of signals in [-1.0, 1.0].
        """
        if not data_map:
            return {}

        # ---- Step 1: Absolute momentum per instrument ----
        abs_returns: Dict[str, pd.Series] = {}
        rel_returns: Dict[str, pd.Series] = {}

        for code, df in data_map.items():
            if "close" not in df.columns:
                continue
            close = df["close"].astype(float)

            # Absolute momentum: return over abs_lookback
            abs_ret = close / close.shift(self.abs_lookback) - 1.0
            abs_returns[code] = abs_ret

            # Relative momentum: return over rel_lookback
            rel_ret = close / close.shift(self.rel_lookback) - 1.0
            rel_returns[code] = rel_ret

        if not abs_returns:
            return {}

        codes = list(abs_returns.keys())

        # ---- Step 2: Cross-sectional rank for relative momentum ----
        # Build a DataFrame of relative returns for ranking
        rel_df = pd.DataFrame(rel_returns)

        # Percent rank: 0.0 = worst, 1.0 = best (across instruments)
        # Use rank with pct=True; NaNs are excluded from ranking
        rel_rank = rel_df.rank(axis=1, pct=True, na_option="keep")

        # ---- Step 3: Combine into signals with rebalance cadence ----
        signals: Dict[str, pd.Series] = {}

        for code in codes:
            df = data_map[code]
            close = df["close"].astype(float)
            n = len(close)

            abs_ret = abs_returns[code]

            # Get relative rank; if code not in rank columns (shouldn't
            # happen), use 0.5 as neutral rank
            if code in rel_rank.columns:
                rank = rel_rank[code]
            else:
                rank = pd.Series(0.5, index=close.index)

            # Raw signal computation per bar
            raw = np.full(n, np.nan)

            abs_arr = abs_ret.values
            rank_arr = rank.reindex(close.index).values

            for i in range(n):
                a = abs_arr[i]
                r = rank_arr[i]

                if np.isnan(a) or np.isnan(r):
                    continue

                if a > 0:
                    # Absolute momentum positive
                    if r >= 0.5:
                        # Top half: full long
                        raw[i] = 1.0
                    else:
                        # Bottom half: half long
                        raw[i] = 0.5
                else:
                    # Absolute momentum negative or zero
                    raw[i] = -0.3

            # Apply rebalance cadence: hold signal constant between
            # rebalance dates to avoid daily churn
            signal_arr = np.full(n, np.nan)
            bars_since_rebal = 0
            current_signal = np.nan

            for i in range(n):
                if np.isnan(raw[i]):
                    signal_arr[i] = np.nan
                    continue

                # Rebalance on first valid bar or every rebalance_days
                if np.isnan(current_signal) or bars_since_rebal >= self.rebalance_days:
                    current_signal = raw[i]
                    bars_since_rebal = 0

                signal_arr[i] = current_signal
                bars_since_rebal += 1

            signal = pd.Series(signal_arr, index=close.index)
            signal = signal.clip(lower=-1.0, upper=1.0)
            signals[code] = signal

        return signals
