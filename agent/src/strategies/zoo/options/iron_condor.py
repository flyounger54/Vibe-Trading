
# ============================================================
# 中文名称: 铁鹰组合策略
# 简要说明: 铁鹰组合在低波动、区间震荡的市场中获利。当已实现波动率低于
#           阈值且价格在均线附近时，信号为正（适合收取权利金）；当波动率
#           飙升或价格突破区间时，信号为负（突破风险，应平仓止损）。
# 典型用途: 检测适合卖出铁鹰组合的市场环境。
# ============================================================
"""Iron Condor Strategy (opt_iron_condor).

Iron condor profits from low volatility / range-bound markets. The signal
detects market regime: when realized vol is low AND price is within a
range near the moving average, signal is positive (favorable for premium
collection). When vol spikes or price breaks the range, signal turns
negative (breakout risk, close position / cut loss).
"""

from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd

__strategy_meta__ = {
    "id": "opt_iron_condor",
    "nickname": "铁鹰组合",
    "category": "options",
    "description": (
        "Iron condor regime detection. Positive signal when realized "
        "volatility is low and price trades within a range near its "
        "moving average (favorable for premium collection). Negative "
        "signal when volatility spikes or price breaks the range "
        "(breakout risk). Value represents confidence level."
    ),
    "universe": ["equity_us"],
    "frequency": ["1D"],
    "columns_required": ["close"],
    "default_params": {
        "vol_lookback": 20,
        "ma_period": 50,
        "low_vol_pct": 0.4,
        "range_pct": 0.05,
    },
    "risk_profile": "medium",
    "min_bars": 55,
    "reference": "经典铁鹰期权策略",
    "factors_used": [],
}


class SignalEngine:
    """铁鹰组合信号引擎。"""

    def __init__(self, **params: Any) -> None:
        self.vol_lookback: int = int(params.get("vol_lookback", 20))
        self.ma_period: int = int(params.get("ma_period", 50))
        self.low_vol_pct: float = float(params.get("low_vol_pct", 0.4))
        self.range_pct: float = float(params.get("range_pct", 0.05))
        if self.vol_lookback < 2:
            raise ValueError(f"vol_lookback must be >= 2, got {self.vol_lookback}")
        if self.ma_period < 2:
            raise ValueError(f"ma_period must be >= 2, got {self.ma_period}")
        if not (0.0 < self.low_vol_pct <= 1.0):
            raise ValueError(
                f"low_vol_pct ({self.low_vol_pct}) must be in (0, 1]"
            )
        if not (0.0 < self.range_pct <= 1.0):
            raise ValueError(
                f"range_pct ({self.range_pct}) must be in (0, 1]"
            )

    def generate(
        self, data_map: Dict[str, pd.DataFrame]
    ) -> Dict[str, pd.Series]:
        """为每个标的生成铁鹰组合适合度信号。

        Returns:
            Dict mapping ticker -> pd.Series of signals in [-1.0, 1.0].
            Positive = favorable for iron condor (collect premium).
            Negative = unfavorable (breakout risk).
        """
        signals: Dict[str, pd.Series] = {}

        for code, df in data_map.items():
            if "close" not in df.columns:
                continue
            signals[code] = self._generate_one(df)

        return signals

    def _generate_one(self, df: pd.DataFrame) -> pd.Series:
        """对单个标的生成铁鹰组合信号。"""
        close: pd.Series = df["close"].astype(float)

        # --- Component 1: Volatility regime ---
        # Daily log returns
        log_ret = np.log(close / close.shift(1))

        # Annualised realized volatility
        rolling_std = log_ret.rolling(
            window=self.vol_lookback, min_periods=self.vol_lookback
        ).std()
        realized_vol = rolling_std * np.sqrt(252)

        # Volatility percentile over a longer window (using 4x vol_lookback)
        vol_rank_window = self.vol_lookback * 4
        vol_pct_rank = realized_vol.rolling(
            window=vol_rank_window, min_periods=vol_rank_window
        ).apply(
            lambda x: pd.Series(x).rank(pct=True).iloc[-1],
            raw=False,
        )

        # Vol score: low vol -> positive, high vol -> negative
        # At low_vol_pct percentile: score = 0 (neutral)
        # Below low_vol_pct: score ramps up to +1
        # Above low_vol_pct: score ramps down to -1
        vol_score = (self.low_vol_pct - vol_pct_rank) / max(
            self.low_vol_pct, 1.0 - self.low_vol_pct
        )

        # --- Component 2: Range-bound detection ---
        # Moving average
        ma = close.rolling(
            window=self.ma_period, min_periods=self.ma_period
        ).mean()

        # Distance from MA as fraction of MA
        ma_distance = (close - ma).abs() / ma.where(ma > 0, other=np.nan)

        # Range score: within range -> positive, outside -> negative
        # At range_pct boundary: score = 0
        # Closer to MA: score ramps up to +1
        # Further from MA: score ramps down to -1
        range_score = (self.range_pct - ma_distance) / self.range_pct

        # --- Combine scores ---
        # Both components contribute equally
        # When both are positive (low vol + range-bound): strong positive signal
        # When either is negative: signal degrades or goes negative
        raw_signal = 0.5 * vol_score + 0.5 * range_score

        # Clip to [-1.0, 1.0]
        signal = raw_signal.clip(lower=-1.0, upper=1.0)

        # NaN where insufficient data
        valid = vol_pct_rank.notna() & ma.notna()
        signal = signal.where(valid, other=np.nan)

        return signal
