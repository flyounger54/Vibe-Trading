
# ============================================================
# 中文名称: SuperTrend策略
# 简要说明: 基于ATR的趋势跟踪指标。上轨 = HL2 + mult*ATR，下轨 = HL2 - mult*ATR。
#           收盘价突破上轨翻多(+1)，跌破下轨翻空(-1)。带翻转维持方向不变。
#           信号值 ∈ [-1, 1]，NaN 安全。
# 典型用途: 简洁高效的趋势跟踪，适用于A股与加密货币等波动性资产。
# ============================================================
"""SuperTrend Indicator (trend_supertrend).

ATR-based trend-following indicator.  Upper band = HL2 + multiplier * ATR,
lower band = HL2 - multiplier * ATR.  When price closes above the previous
upper band, trend flips to long; below the previous lower band, trend flips
to short.  Bands ratchet inward to lock in the trend direction.
"""

from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd

__strategy_meta__ = {
    "id": "trend_supertrend",
    "nickname": "SuperTrend",
    "category": "trend",
    "description": (
        "SuperTrend indicator. Upper band = HL2 + multiplier * ATR, "
        "lower band = HL2 - multiplier * ATR. Close above previous upper "
        "band flips to long (+1); close below previous lower band flips "
        "to short (-1). Bands ratchet to maintain trend direction."
    ),
    "universe": ["equity_cn", "crypto"],
    "frequency": ["1D"],
    "columns_required": ["high", "low", "close"],
    "default_params": {"atr_period": 10, "multiplier": 3.0},
    "risk_profile": "medium",
    "min_bars": 15,
    "reference": "Olivier Seban, SuperTrend Indicator",
    "factors_used": [],
}


class SignalEngine:
    """SuperTrend信号引擎。"""

    def __init__(self, **params: Any) -> None:
        self.atr_period: int = int(params.get("atr_period", 10))
        self.multiplier: float = float(params.get("multiplier", 3.0))
        if self.atr_period < 1:
            raise ValueError(f"atr_period ({self.atr_period}) must be >= 1")
        if self.multiplier <= 0:
            raise ValueError(f"multiplier ({self.multiplier}) must be > 0")

    def generate(
        self, data_map: Dict[str, pd.DataFrame]
    ) -> Dict[str, pd.Series]:
        """为每个标的生成SuperTrend信号。

        Returns:
            Dict mapping ticker -> pd.Series of signals in [-1.0, 1.0].
        """
        signals: Dict[str, pd.Series] = {}

        for code, df in data_map.items():
            if not {"high", "low", "close"}.issubset(df.columns):
                continue
            signals[code] = self._generate_one(df)

        return signals

    def _generate_one(self, df: pd.DataFrame) -> pd.Series:
        """对单个标的生成SuperTrend信号。"""
        high: pd.Series = df["high"].astype(float)
        low: pd.Series = df["low"].astype(float)
        close: pd.Series = df["close"].astype(float)

        n = len(close)
        period = self.atr_period
        mult = self.multiplier

        # --- ATR via Wilder smoothing ---
        prev_close = close.shift(1)
        tr = pd.concat(
            [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
            axis=1,
        ).max(axis=1)
        atr = tr.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()

        # --- Basic bands ---
        hl2 = (high + low) / 2.0
        basic_upper_s = hl2 + mult * atr
        basic_lower_s = hl2 - mult * atr

        # --- SuperTrend calculation (iterative for band ratcheting) ---
        # Convert to numpy arrays upfront to avoid pandas indexing warnings
        basic_upper = basic_upper_s.values.copy()
        basic_lower = basic_lower_s.values.copy()
        upper_band = basic_upper.copy()
        lower_band = basic_lower.copy()
        supertrend = np.full(n, np.nan)
        direction = np.full(n, np.nan)  # +1 = long, -1 = short

        close_arr = close.values
        atr_arr = atr.values

        # Find first valid index (where ATR is available)
        first_valid = period  # ATR needs at least `period` bars
        if first_valid >= n:
            return pd.Series(np.full(n, np.nan), index=close.index)

        # Initialise at first valid bar
        direction[first_valid] = 1.0
        supertrend[first_valid] = lower_band[first_valid]

        for i in range(first_valid + 1, n):
            # Ratchet upper band: only lower it (tighter), never raise
            if basic_upper[i] < upper_band[i - 1] or close_arr[i - 1] > upper_band[i - 1]:
                upper_band[i] = basic_upper[i]
            else:
                upper_band[i] = upper_band[i - 1]

            # Ratchet lower band: only raise it (tighter), never lower
            if basic_lower[i] > lower_band[i - 1] or close_arr[i - 1] < lower_band[i - 1]:
                lower_band[i] = basic_lower[i]
            else:
                lower_band[i] = lower_band[i - 1]

            # Determine direction
            if np.isnan(atr_arr[i]):
                direction[i] = np.nan
                supertrend[i] = np.nan
                continue

            prev_dir = direction[i - 1]
            if prev_dir == 1.0:
                # Was long: stay long unless close drops below lower band
                if close_arr[i] < lower_band[i]:
                    direction[i] = -1.0
                    supertrend[i] = upper_band[i]
                else:
                    direction[i] = 1.0
                    supertrend[i] = lower_band[i]
            else:
                # Was short: stay short unless close rises above upper band
                if close_arr[i] > upper_band[i]:
                    direction[i] = 1.0
                    supertrend[i] = lower_band[i]
                else:
                    direction[i] = -1.0
                    supertrend[i] = upper_band[i]

        signal = pd.Series(direction, index=close.index)

        # Clip for safety (already +/-1, but guard edge cases)
        signal = signal.clip(lower=-1.0, upper=1.0)

        return signal
