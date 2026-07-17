
# ============================================================
# 中文名称: 52周新高动量策略
# 简要说明: 以收盘价接近52周高点的程度作为动量信号。
#           Ratio = close / 52w_high。Ratio > long_threshold → 做多信号，
#           Ratio < short_threshold → 做空信号，中间地带线性插值至零。
#           信号值 ∈ [-1, 1]，NaN 安全。
# 典型用途: 捕捉接近新高时的正动量效应和远离新高时的负动量效应。
# ============================================================
"""52-Week High Momentum (mom_52w_high).

Uses nearness to the 52-week high as a momentum signal.  Ratio =
close / rolling-max(close, 252).  Above the long threshold the signal
is positive (stronger as ratio approaches 1.0); below the short
threshold the signal is negative.  Between the two thresholds the
signal tapers linearly to zero.
"""

from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd

__strategy_meta__ = {
    "id": "mom_52w_high",
    "nickname": "52周新高动量",
    "category": "momentum",
    "description": (
        "52-week high momentum. Ratio = close / 52-week high. "
        "Ratio above long_threshold produces a long signal (stronger "
        "closer to 1.0); ratio below short_threshold produces a short "
        "signal. Between thresholds the signal tapers linearly to zero."
    ),
    "universe": ["equity_cn", "equity_us"],
    "frequency": ["1D"],
    "columns_required": ["close"],
    "default_params": {"high_period": 252, "long_threshold": 0.9, "short_threshold": 0.7},
    "risk_profile": "medium",
    "min_bars": 260,
    "reference": "George & Hwang, The 52-Week High and Momentum Investing, JF 2004",
    "factors_used": [],
}


class SignalEngine:
    """52周新高动量信号引擎。"""

    def __init__(self, **params: Any) -> None:
        self.high_period: int = int(params.get("high_period", 252))
        self.long_threshold: float = float(params.get("long_threshold", 0.9))
        self.short_threshold: float = float(params.get("short_threshold", 0.7))
        if self.high_period < 1:
            raise ValueError(f"high_period ({self.high_period}) must be >= 1")
        if not (0 < self.short_threshold < self.long_threshold <= 1.0):
            raise ValueError(
                f"Need 0 < short_threshold ({self.short_threshold}) "
                f"< long_threshold ({self.long_threshold}) <= 1.0"
            )

    def generate(
        self, data_map: Dict[str, pd.DataFrame]
    ) -> Dict[str, pd.Series]:
        """为每个标的生成52周新高动量信号。

        Returns:
            Dict mapping ticker -> pd.Series of signals in [-1.0, 1.0].
        """
        signals: Dict[str, pd.Series] = {}

        for code, df in data_map.items():
            if "close" not in df.columns:
                continue
            signals[code] = self._generate_one(df)

        return signals

    def _generate_one(self, df: pd.DataFrame) -> pd.Series:
        """对单个标的生成52周新高动量信号。"""
        close: pd.Series = df["close"].astype(float)

        # Rolling 52-week (high_period) maximum
        high_52w = close.rolling(
            window=self.high_period, min_periods=self.high_period
        ).max()

        # Ratio: how close is current price to the 52-week high
        safe_high = high_52w.where(high_52w > 0, other=np.nan)
        ratio = close / safe_high

        lt = self.long_threshold
        st = self.short_threshold
        mid = (lt + st) / 2.0  # midpoint of neutral zone

        # --- Piecewise linear signal ---
        # ratio >= lt: long signal, linearly from 0 at lt to +1 at 1.0
        # ratio <= st: short signal, linearly from 0 at st to -1 at 0
        # st < ratio < lt: neutral zone, linearly from slight negative to slight positive
        signal = pd.Series(np.nan, index=close.index)

        # Long zone: ratio in [lt, 1.0] -> signal in [0, 1]
        long_mask = ratio >= lt
        signal = signal.where(
            ~long_mask,
            other=((ratio - lt) / (1.0 - lt)).clip(lower=0.0, upper=1.0),
        )

        # Short zone: ratio in [0, st] -> signal in [-1, 0]
        short_mask = ratio <= st
        signal = signal.where(
            ~short_mask,
            other=-((st - ratio) / st).clip(lower=0.0, upper=1.0),
        )

        # Neutral zone: ratio in (st, lt) -> linearly interpolate to 0
        neutral_mask = (ratio > st) & (ratio < lt) & ratio.notna()
        neutral_signal = (ratio - mid) / (lt - st) * 2.0  # ranges ~ [-1, 1] across zone
        neutral_signal = neutral_signal * 0.3  # dampen neutral zone signal
        signal = signal.where(
            ~neutral_mask,
            other=neutral_signal.clip(lower=-0.3, upper=0.3),
        )

        # Clip for safety and mask NaN where rolling max is unavailable
        signal = signal.clip(lower=-1.0, upper=1.0)
        signal = signal.where(high_52w.notna(), other=np.nan)

        return signal
