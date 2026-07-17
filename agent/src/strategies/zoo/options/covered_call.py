
# ============================================================
# 中文名称: 备兑看涨策略
# 简要说明: 持有标的多头，模拟卖出虚值看涨期权的效果。通过已实现波动率
#           动态调节仓位：高波时降低头寸至 ~0.7（模拟权利金收入降低风险），
#           低波时保持满仓（1.0），价格急跌时减仓至 0.5。
#           信号代表备兑看涨组合的净 Delta。
# 典型用途: 在持有标的多头的同时通过波动率过滤提升风险调整后收益。
# ============================================================
"""Covered Call Strategy (opt_covered_call).

Hold the underlying long and simulate the effect of selling OTM calls.
When realized volatility is high, reduce position to ~0.7 (premium income
reduces risk). When vol is low, maintain full long (1.0). When price
drops sharply, reduce to 0.5 (underlying risk without call premium).
Signal represents net delta of the covered call position.
"""

from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd

__strategy_meta__ = {
    "id": "opt_covered_call",
    "nickname": "备兑看涨",
    "category": "options",
    "description": (
        "Covered call strategy on the underlying. Hold long position and "
        "simulate selling OTM calls via volatility-based position sizing. "
        "High realized vol reduces position to ~0.7 (premium income effect); "
        "low vol keeps full long (1.0); sharp drawdown reduces to 0.5. "
        "Signal represents net delta of covered call position."
    ),
    "universe": ["equity_cn", "equity_us"],
    "frequency": ["1D"],
    "columns_required": ["close"],
    "default_params": {
        "vol_lookback": 20,
        "high_vol_threshold": 0.3,
        "base_position": 1.0,
        "hedged_position": 0.7,
    },
    "risk_profile": "low",
    "min_bars": 25,
    "reference": "经典备兑看涨策略, 已有 options-strategy skill",
    "factors_used": [],
}


class SignalEngine:
    """备兑看涨信号引擎。"""

    def __init__(self, **params: Any) -> None:
        self.vol_lookback: int = int(params.get("vol_lookback", 20))
        self.high_vol_threshold: float = float(params.get("high_vol_threshold", 0.3))
        self.base_position: float = float(params.get("base_position", 1.0))
        self.hedged_position: float = float(params.get("hedged_position", 0.7))
        if self.vol_lookback < 2:
            raise ValueError(f"vol_lookback must be >= 2, got {self.vol_lookback}")
        if not (0.0 < self.high_vol_threshold <= 1.0):
            raise ValueError(
                f"high_vol_threshold ({self.high_vol_threshold}) must be in (0, 1]"
            )
        if not (0.0 < self.hedged_position <= self.base_position <= 1.0):
            raise ValueError(
                "require 0 < hedged_position <= base_position <= 1.0, "
                f"got hedged_position={self.hedged_position}, "
                f"base_position={self.base_position}"
            )

    def generate(
        self, data_map: Dict[str, pd.DataFrame]
    ) -> Dict[str, pd.Series]:
        """为每个标的生成备兑看涨组合的净 Delta 信号。

        Returns:
            Dict mapping ticker -> pd.Series of signals in [0.0, 1.0].
            Covered call is always net long, so signals are non-negative.
        """
        signals: Dict[str, pd.Series] = {}

        for code, df in data_map.items():
            if "close" not in df.columns:
                continue
            signals[code] = self._generate_one(df)

        return signals

    def _generate_one(self, df: pd.DataFrame) -> pd.Series:
        """对单个标的生成备兑看涨信号。"""
        close: pd.Series = df["close"].astype(float)

        # Daily log returns
        log_ret = np.log(close / close.shift(1))

        # Annualised realized volatility (rolling std of log returns * sqrt(252))
        rolling_std = log_ret.rolling(
            window=self.vol_lookback, min_periods=self.vol_lookback
        ).std()
        realized_vol = rolling_std * np.sqrt(252)

        # Drawdown from rolling high (using same lookback window)
        rolling_high = close.rolling(
            window=self.vol_lookback, min_periods=self.vol_lookback
        ).max()
        drawdown = (close - rolling_high) / rolling_high.where(
            rolling_high > 0, other=np.nan
        )

        # --- Position sizing logic ---
        # Start with base position (full long)
        signal = pd.Series(np.nan, index=close.index, dtype=float)

        # Mask: only compute where we have enough data
        valid = realized_vol.notna() & rolling_high.notna()

        # Default: base position (low vol, no drawdown)
        signal[valid] = self.base_position

        # High vol regime: reduce to hedged position (simulating call premium)
        high_vol_mask = valid & (realized_vol >= self.high_vol_threshold)
        # Scale between hedged and base: higher vol -> closer to hedged
        # Use a smooth transition: linear interpolation from threshold to 2x threshold
        vol_ratio = (realized_vol / self.high_vol_threshold).clip(upper=2.0)
        # At threshold: ratio=1 -> position=hedged_position
        # Below threshold: ratio<1 -> position=base_position
        # Above threshold: ratio>1 -> interpolate toward hedged
        hedged_signal = self.base_position - (
            (vol_ratio - 1.0).clip(lower=0.0)
            * (self.base_position - self.hedged_position)
        )
        signal[high_vol_mask] = hedged_signal[high_vol_mask]

        # Sharp drawdown (> 10%): reduce to 0.5 regardless of vol
        # This represents the risk of the underlying without sufficient
        # premium income to compensate
        sharp_drop_mask = valid & (drawdown < -0.10)
        signal[sharp_drop_mask] = 0.5

        # Clip to [0.0, 1.0] for safety
        signal = signal.clip(lower=0.0, upper=1.0)

        return signal
