
# ============================================================
# 中文名称: 横截面动量策略
# 简要说明: 按过去 N 日收益率对标的排序。收益率最高的五分之一做多(+1)，
#           最低的五分之一做空(-1)，其余为零。需至少 5 个标的。
#           信号值 ∈ [-1, 1]，NaN 安全，无前瞻偏差。
# 典型用途: 经典学术因子策略，适用于大规模股票池的横截面动量效应捕捉。
# ============================================================
"""Cross-Sectional Momentum (mom_cross_section).

Rank instruments by their trailing N-day return. Top quintile receives
a long signal (+1), bottom quintile receives a short signal (-1).
Requires at least 5 instruments in the data_map to form meaningful
quintiles.
"""

from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd

__strategy_meta__ = {
    "id": "mom_cross_section",
    "nickname": "横截面动量",
    "category": "momentum",
    "description": (
        "Cross-sectional momentum. Rank instruments by past N-day return; "
        "top quintile goes long, bottom quintile goes short. "
        "Requires >= 5 instruments for meaningful ranking."
    ),
    "universe": ["equity_cn", "equity_us"],
    "frequency": ["1D"],
    "columns_required": ["close"],
    "default_params": {
        "lookback": 60,
        "holding_period": 20,
        "top_pct": 0.2,
        "bottom_pct": 0.2,
    },
    "risk_profile": "medium",
    "min_bars": 80,
    "reference": "Jegadeesh & Titman, Returns to Buying Winners and Selling Losers, 1993",
    "factors_used": [],
}


class SignalEngine:
    """横截面动量信号引擎。"""

    def __init__(self, **params: Any) -> None:
        self.lookback: int = int(params.get("lookback", 60))
        self.holding_period: int = int(params.get("holding_period", 20))
        self.top_pct: float = float(params.get("top_pct", 0.2))
        self.bottom_pct: float = float(params.get("bottom_pct", 0.2))
        if self.lookback < 1:
            raise ValueError(f"lookback ({self.lookback}) must be >= 1")
        if not (0 < self.top_pct < 1):
            raise ValueError(f"top_pct ({self.top_pct}) must be in (0, 1)")
        if not (0 < self.bottom_pct < 1):
            raise ValueError(f"bottom_pct ({self.bottom_pct}) must be in (0, 1)")

    def generate(
        self, data_map: Dict[str, pd.DataFrame]
    ) -> Dict[str, pd.Series]:
        """为横截面中的所有标的生成动量排序信号。

        Returns:
            Dict mapping ticker -> pd.Series of signals in [-1.0, 1.0].
        """
        # Collect close prices into a single DataFrame for cross-sectional ranking
        close_dict: Dict[str, pd.Series] = {}
        for code, df in data_map.items():
            if "close" not in df.columns:
                continue
            close_dict[code] = df["close"].astype(float)

        if len(close_dict) < 5:
            # Not enough instruments for meaningful quintile ranking;
            # return zero signals for all available instruments
            return {
                code: pd.Series(0.0, index=s.index)
                for code, s in close_dict.items()
            }

        # Align all close series on a common datetime index
        close_panel = pd.DataFrame(close_dict)

        # Past N-day return (simple return, no lookahead)
        past_return = close_panel / close_panel.shift(self.lookback) - 1.0

        # Rank across instruments at each time step (pct=True -> [0, 1])
        ranked = past_return.rank(axis=1, pct=True, na_option="keep")

        # Build signal matrix
        # Top quintile -> +1 (long), bottom quintile -> -1 (short), else 0
        signal_matrix = pd.DataFrame(0.0, index=ranked.index, columns=ranked.columns)

        top_threshold = 1.0 - self.top_pct
        bottom_threshold = self.bottom_pct

        signal_matrix = signal_matrix.where(
            ~(ranked >= top_threshold), other=1.0
        )
        signal_matrix = signal_matrix.where(
            ~(ranked <= bottom_threshold), other=-1.0
        )

        # Apply holding period: hold each signal for `holding_period` bars
        # by stepping through rebalance dates
        if self.holding_period > 1:
            signal_matrix = self._apply_holding(signal_matrix)

        # NaN where past return is not available
        signal_matrix = signal_matrix.where(past_return.notna(), other=np.nan)

        # Convert back to dict
        signals: Dict[str, pd.Series] = {}
        for code in close_dict:
            if code in signal_matrix.columns:
                signals[code] = signal_matrix[code]

        return signals

    def _apply_holding(self, raw_signals: pd.DataFrame) -> pd.DataFrame:
        """Resample signals at holding_period intervals to avoid daily churn."""
        result = raw_signals.copy()
        valid_rows = raw_signals.dropna(how="all").index

        if len(valid_rows) == 0:
            return result

        # Identify rebalance dates: every `holding_period` bars from the
        # first valid row
        first_valid_loc = raw_signals.index.get_loc(valid_rows[0])
        total_rows = len(raw_signals)

        rebalance_locs = list(range(first_valid_loc, total_rows, self.holding_period))
        rebalance_dates = set(raw_signals.index[i] for i in rebalance_locs)

        # Forward-fill between rebalance dates
        last_signal = None
        for i in range(first_valid_loc, total_rows):
            idx = raw_signals.index[i]
            if idx in rebalance_dates:
                last_signal = raw_signals.iloc[i]
            elif last_signal is not None:
                result.iloc[i] = last_signal

        return result
