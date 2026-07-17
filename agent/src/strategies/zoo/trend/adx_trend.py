
# ============================================================
# 中文名称: ADX趋势过滤策略
# 简要说明: 使用ADX衡量趋势强度。ADX > 阈值时趋势强劲，由+DI/-DI判定方向：
#           +DI > -DI 做多(+1)，+DI < -DI 做空(-1)。
#           ADX < 阈值时无信号(0)。信号值 ∈ [-1, 1]，NaN 安全。
# 典型用途: 过滤震荡行情，仅在趋势明确时入场，适用于全市场。
# ============================================================
"""ADX Trend Filter (trend_adx).

Uses the Average Directional Index to gauge trend strength.  When ADX
exceeds a threshold the trend is deemed strong and the signal direction
is determined by +DI vs -DI.  Below the threshold the signal is flat.
"""

from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd

__strategy_meta__ = {
    "id": "trend_adx",
    "nickname": "ADX趋势过滤",
    "category": "trend",
    "description": (
        "ADX trend filter. Measures trend strength via ADX (smoothed DX). "
        "When ADX > threshold, +DI > -DI produces a long signal and "
        "+DI < -DI produces a short signal, scaled by ADX strength. "
        "ADX below threshold produces a flat (zero) signal."
    ),
    "universe": ["equity_us", "equity_cn", "equity_hk", "crypto", "futures"],
    "frequency": ["1D"],
    "columns_required": ["high", "low", "close"],
    "default_params": {"adx_period": 14, "adx_threshold": 25},
    "risk_profile": "medium",
    "min_bars": 30,
    "reference": "Wilder, New Concepts in Technical Trading Systems, 1978",
    "factors_used": [],
}


class SignalEngine:
    """ADX趋势过滤信号引擎。"""

    def __init__(self, **params: Any) -> None:
        self.adx_period: int = int(params.get("adx_period", 14))
        self.adx_threshold: float = float(params.get("adx_threshold", 25))
        if self.adx_period < 2:
            raise ValueError(f"adx_period ({self.adx_period}) must be >= 2")
        if self.adx_threshold <= 0:
            raise ValueError(f"adx_threshold ({self.adx_threshold}) must be > 0")

    def generate(
        self, data_map: Dict[str, pd.DataFrame]
    ) -> Dict[str, pd.Series]:
        """为每个标的生成ADX趋势过滤信号。

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
        """对单个标的生成ADX趋势过滤信号。"""
        high: pd.Series = df["high"].astype(float)
        low: pd.Series = df["low"].astype(float)
        close: pd.Series = df["close"].astype(float)

        period = self.adx_period

        # --- True Range ---
        prev_close = close.shift(1)
        tr = pd.concat(
            [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
            axis=1,
        ).max(axis=1)

        # --- Directional Movement ---
        up_move = high - high.shift(1)
        down_move = low.shift(1) - low
        plus_dm = pd.Series(
            np.where((up_move > down_move) & (up_move > 0), up_move, 0.0),
            index=close.index,
        )
        minus_dm = pd.Series(
            np.where((down_move > up_move) & (down_move > 0), down_move, 0.0),
            index=close.index,
        )

        # --- Wilder smoothing (EWM with alpha=1/period) ---
        alpha = 1.0 / period
        atr = tr.ewm(alpha=alpha, min_periods=period, adjust=False).mean()
        plus_dm_smooth = plus_dm.ewm(alpha=alpha, min_periods=period, adjust=False).mean()
        minus_dm_smooth = minus_dm.ewm(alpha=alpha, min_periods=period, adjust=False).mean()

        # --- Directional Indicators ---
        safe_atr = atr.where(atr > 0, other=np.nan)
        plus_di = 100.0 * plus_dm_smooth / safe_atr
        minus_di = 100.0 * minus_dm_smooth / safe_atr

        # --- DX and ADX ---
        di_sum = plus_di + minus_di
        safe_di_sum = di_sum.where(di_sum > 0, other=np.nan)
        dx = 100.0 * (plus_di - minus_di).abs() / safe_di_sum
        adx = dx.ewm(alpha=alpha, min_periods=period, adjust=False).mean()

        # --- Signal Generation ---
        # Direction: +DI > -DI → long (+1), +DI < -DI → short (-1)
        direction = pd.Series(
            np.where(plus_di > minus_di, 1.0, -1.0), index=close.index
        )

        # Strength: scale by how far ADX exceeds threshold, normalise to [0, 1]
        # At threshold ADX → 0 strength, at 2x threshold → full strength
        strength = ((adx - self.adx_threshold) / self.adx_threshold).clip(
            lower=0.0, upper=1.0
        )

        raw = direction * strength

        # Clip for safety and mask NaN
        signal = raw.clip(lower=-1.0, upper=1.0)
        signal = signal.where(adx.notna() & plus_di.notna() & minus_di.notna(), other=np.nan)

        return signal
