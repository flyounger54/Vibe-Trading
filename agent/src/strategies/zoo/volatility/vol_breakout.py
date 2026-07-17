
# ============================================================
# 中文名称: 波动率突破策略
# 简要说明: 当日内波幅 (high-low) 超过 N 倍 ATR 时视为突破。
#           close > open 为多头突破 (+), close < open 为空头突破 (-),
#           信号强度与 range/ATR 比值成正比（上限 1.0）。
# 典型用途: 捕捉波动放大后的方向性突破机会。
# ============================================================
"""Volatility Breakout (vol_breakout).

When today's range (high - low) exceeds N * ATR, a directional breakout
is detected. Close above open implies bullish; close below open implies
bearish. Signal magnitude is proportional to range / ATR ratio, capped
at 1.0.
"""

from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd

__strategy_meta__ = {
    "id": "vol_breakout",
    "nickname": "波动率突破",
    "category": "volatility",
    "description": (
        "Signals a breakout when daily range exceeds N * ATR. "
        "Bullish if close > open, bearish if close < open. "
        "Signal strength proportional to range/ATR ratio (capped at 1.0)."
    ),
    "universe": ["equity_us", "equity_cn", "equity_hk", "crypto", "futures"],
    "frequency": ["1D"],
    "columns_required": ["open", "high", "low", "close"],
    "default_params": {"atr_period": 14, "breakout_mult": 1.5},
    "risk_profile": "medium",
    "min_bars": 20,
    "reference": "经典技术分析",
    "factors_used": [],
}


class SignalEngine:
    """波动率突破信号引擎。"""

    def __init__(self, **params: Any) -> None:
        self.atr_period: int = int(params.get("atr_period", 14))
        self.breakout_mult: float = float(params.get("breakout_mult", 1.5))
        if self.atr_period < 1:
            raise ValueError(f"atr_period must be >= 1, got {self.atr_period}")
        if self.breakout_mult <= 0:
            raise ValueError(f"breakout_mult must be > 0, got {self.breakout_mult}")

    def generate(
        self, data_map: Dict[str, pd.DataFrame]
    ) -> Dict[str, pd.Series]:
        """Generate volatility breakout signals.

        Returns:
            Dict mapping ticker -> pd.Series of signals in [-1.0, 1.0].
        """
        signals: Dict[str, pd.Series] = {}

        for code, df in data_map.items():
            required = {"open", "high", "low", "close"}
            if not required.issubset(df.columns):
                continue

            open_ = df["open"].astype(float)
            high = df["high"].astype(float)
            low = df["low"].astype(float)
            close = df["close"].astype(float)

            # True Range
            prev_close = close.shift(1)
            tr = pd.concat(
                [
                    high - low,
                    (high - prev_close).abs(),
                    (low - prev_close).abs(),
                ],
                axis=1,
            ).max(axis=1)

            atr = tr.rolling(window=self.atr_period, min_periods=self.atr_period).mean()

            # Daily range
            day_range = high - low

            # Range / ATR ratio
            ratio = day_range / atr.replace(0, np.nan)

            # Direction: close > open -> bullish (+1), close < open -> bearish (-1)
            direction = np.sign(close - open_)

            # Signal: activate only when ratio exceeds breakout_mult
            # Strength = (ratio - breakout_mult) / breakout_mult, normalized to [0, 1]
            excess = (ratio - self.breakout_mult).clip(lower=0)
            strength = (excess / self.breakout_mult).clip(upper=1.0)

            raw = direction * strength

            # Clamp to [-1, 1] and preserve NaN where ATR is not ready
            raw = raw.clip(-1.0, 1.0)
            raw = raw.where(atr.notna(), other=np.nan)

            signals[code] = raw

        return signals
