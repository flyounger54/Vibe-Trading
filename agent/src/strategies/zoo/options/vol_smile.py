
# ============================================================
# 中文名称: 波动率微笑交易
# 简要说明: 使用 OHLCV 数据估计波动率偏度代理。通过 (high-close)/(close-low)
#           比值衡量上行/下行波动不对称性。ratio > 1 → 正偏（上行波动更大）
#           → 看多；ratio < 1 → 负偏 → 看空。使用滚动均值平滑后生成信号。
#           信号 = (smoothed_ratio - 1.0) * scale, 裁剪至 [-1, 1]。
# 典型用途: 捕捉隐含在价格结构中的方向性波动率不对称信息。
# ============================================================
"""Volatility Smile Trade (opt_vol_smile).

Volatility smile proxy using OHLCV: estimate skew from the ratio
(high - close) / (close - low). When ratio > 1, there is positive skew
(more upside volatility) -> bullish. When ratio < 1, negative skew
-> bearish. A rolling average smooths the ratio.

Signal = (smoothed_ratio - 1.0) * scale, clipped to [-1, 1].
"""

from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd

__strategy_meta__ = {
    "id": "opt_vol_smile",
    "nickname": "波动率微笑交易",
    "category": "options",
    "description": (
        "Volatility smile proxy using OHLCV data. Estimate skew from "
        "(high - close) / (close - low) ratio. Ratio > 1 -> positive "
        "skew (more upside vol) -> bullish. Ratio < 1 -> negative skew "
        "-> bearish. Uses rolling average for smoothing. "
        "Signal = (smoothed_ratio - 1.0) * scale, clipped to [-1, 1]."
    ),
    "universe": ["equity_us"],
    "frequency": ["1D"],
    "columns_required": ["open", "high", "low", "close"],
    "default_params": {"smooth_period": 10, "scale": 2.0},
    "risk_profile": "high",
    "min_bars": 15,
    "reference": "波动率微笑交易, 已有 options-advanced skill",
    "factors_used": [],
}


class SignalEngine:
    """波动率微笑交易信号引擎。"""

    def __init__(self, **params: Any) -> None:
        self.smooth_period: int = int(params.get("smooth_period", 10))
        self.scale: float = float(params.get("scale", 2.0))
        if self.smooth_period < 1:
            raise ValueError(
                f"smooth_period must be >= 1, got {self.smooth_period}"
            )
        if self.scale <= 0:
            raise ValueError(f"scale must be > 0, got {self.scale}")

    def generate(
        self, data_map: Dict[str, pd.DataFrame]
    ) -> Dict[str, pd.Series]:
        """Generate volatility smile signals.

        Returns:
            Dict mapping ticker -> pd.Series of signals in [-1.0, 1.0].
        """
        signals: Dict[str, pd.Series] = {}

        for code, df in data_map.items():
            required = {"open", "high", "low", "close"}
            if not required.issubset(df.columns):
                continue
            signals[code] = self._generate_one(df)

        return signals

    def _generate_one(self, df: pd.DataFrame) -> pd.Series:
        """Generate volatility smile signal for a single ticker."""
        high: pd.Series = df["high"].astype(float)
        low: pd.Series = df["low"].astype(float)
        close: pd.Series = df["close"].astype(float)

        # Upside range: distance from close to high
        upside = high - close
        # Downside range: distance from close to low
        downside = close - low

        # Avoid division by zero: replace zero downside with NaN
        downside_safe = downside.where(downside > 0, other=np.nan)

        # Skew ratio: upside / downside
        # > 1 means more upside volatility (positive skew, bullish)
        # < 1 means more downside volatility (negative skew, bearish)
        raw_ratio = upside / downside_safe

        # Smooth with rolling mean
        smoothed = raw_ratio.rolling(
            window=self.smooth_period, min_periods=self.smooth_period
        ).mean()

        # Signal: deviation from symmetry (ratio = 1.0), scaled
        raw_signal = (smoothed - 1.0) * self.scale

        # Clip to [-1, 1]
        signal = raw_signal.clip(-1.0, 1.0)

        # NaN-safe: preserve NaN where smoothed is not available
        signal = signal.where(smoothed.notna(), other=np.nan)

        return signal
