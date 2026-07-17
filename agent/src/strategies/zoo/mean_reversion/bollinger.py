
# ============================================================
# 中文名称: 布林带均值回归策略
# 简要说明: 价格低于下轨做多（信号强度与偏离均线距离成正比），高于上轨做空。
#           价格回归中轨时信号衰减至零。信号值 ∈ [-1, 1]，NaN 安全。
# 典型用途: 捕捉价格围绕均线的振荡回归机会，适用于震荡市各类资产。
# ============================================================
"""Bollinger Band Mean Reversion (mr_bollinger).

Go long when price drops below the lower Bollinger Band (signal strength
proportional to distance from the middle band); go short when price
exceeds the upper band. Signal fades as price reverts to the mean.
"""

from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd

__strategy_meta__ = {
    "id": "mr_bollinger",
    "nickname": "布林带均值回归",
    "category": "mean_reversion",
    "description": (
        "Bollinger Band mean reversion. Price below lower band produces a "
        "long signal whose strength is proportional to the distance from the "
        "middle band; price above upper band produces a short signal. "
        "Signal fades as price reverts toward the mean."
    ),
    "universe": ["equity_us", "equity_cn", "equity_hk", "crypto", "futures"],
    "frequency": ["1D"],
    "columns_required": ["close"],
    "default_params": {"period": 20, "num_std": 2.0},
    "risk_profile": "low",
    "min_bars": 25,
    "reference": "Bollinger, Bollinger on Bollinger Bands, 2001",
    "factors_used": [],
}


class SignalEngine:
    """布林带均值回归信号引擎。"""

    def __init__(self, **params: Any) -> None:
        self.period: int = int(params.get("period", 20))
        self.num_std: float = float(params.get("num_std", 2.0))
        if self.period < 2:
            raise ValueError(f"period ({self.period}) must be >= 2")
        if self.num_std <= 0:
            raise ValueError(f"num_std ({self.num_std}) must be > 0")

    def generate(
        self, data_map: Dict[str, pd.DataFrame]
    ) -> Dict[str, pd.Series]:
        """为每个标的生成布林带均值回归信号。

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
        """对单个标的生成布林带均值回归信号。"""
        close: pd.Series = df["close"].astype(float)

        mid = close.rolling(window=self.period, min_periods=self.period).mean()
        std = close.rolling(window=self.period, min_periods=self.period).std()

        # Band width: distance from mid to upper (or lower)
        band_width = self.num_std * std

        # Deviation from mid, normalised by band width
        # Positive deviation = price above mid; negative = price below mid
        deviation = close - mid

        # Raw signal: negative deviation -> long (positive signal)
        # positive deviation -> short (negative signal)
        # Normalise so that touching the band gives signal magnitude ~1.0
        raw = -deviation / band_width.where(band_width > 0, other=np.nan)

        # Clip to [-1.0, 1.0]
        signal = raw.clip(lower=-1.0, upper=1.0)

        # NaN where bands are not yet computed
        signal = signal.where(mid.notna() & std.notna(), other=np.nan)

        return signal
