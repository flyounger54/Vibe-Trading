
# ============================================================
# 中文名称: 波动率风险溢价
# 简要说明: 已实现波动率通常低于隐含波动率（恐惧溢价），交易这一价差。
#           无期权数据时使用已实现波动率代理：短窗口 vs 长窗口。
#           short < long → VRP 正 → 卖波动率（做多）；
#           short > long → VRP 负 → 买波动率（做空/减仓）。
#           信号 = tanh((long_vol - short_vol) / long_vol * scale)。
# 典型用途: 捕捉波动率风险溢价的均值回归交易机会。
# ============================================================
"""Volatility Risk Premium (vol_vrp).

Realized volatility tends to be lower than implied volatility (fear
premium). This strategy trades the VRP spread using a proxy: comparing
realized vol over short vs long windows.

If short_vol < long_vol -> VRP is positive -> sell vol (go long).
If short_vol > long_vol -> VRP is negative -> buy vol (go short/reduce).

Signal = tanh((long_vol - short_vol) / long_vol * scale_factor),
producing smooth values in [-1, 1].
"""

from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd

__strategy_meta__ = {
    "id": "vol_vrp",
    "nickname": "波动率风险溢价",
    "category": "volatility",
    "description": (
        "Volatility Risk Premium proxy strategy. Realized vol tends to be "
        "lower than implied vol (fear premium). Without options data, use "
        "short-window vs long-window realized vol as VRP proxy. "
        "short < long -> VRP positive -> sell vol (go long). "
        "short > long -> VRP negative -> buy vol (reduce). "
        "Signal = tanh((long_vol - short_vol) / long_vol * scale_factor)."
    ),
    "universe": ["equity_us"],
    "frequency": ["1D"],
    "columns_required": ["close"],
    "default_params": {"short_window": 5, "long_window": 30, "scale_factor": 5.0},
    "risk_profile": "high",
    "min_bars": 35,
    "reference": "Carr & Wu, Variance Risk Premiums, RFS 2009",
    "factors_used": [],
}


class SignalEngine:
    """波动率风险溢价信号引擎。"""

    def __init__(self, **params: Any) -> None:
        self.short_window: int = int(params.get("short_window", 5))
        self.long_window: int = int(params.get("long_window", 30))
        self.scale_factor: float = float(params.get("scale_factor", 5.0))
        if self.short_window < 2:
            raise ValueError(f"short_window must be >= 2, got {self.short_window}")
        if self.long_window <= self.short_window:
            raise ValueError(
                f"long_window ({self.long_window}) must be > "
                f"short_window ({self.short_window})"
            )
        if self.scale_factor <= 0:
            raise ValueError(f"scale_factor must be > 0, got {self.scale_factor}")

    def generate(
        self, data_map: Dict[str, pd.DataFrame]
    ) -> Dict[str, pd.Series]:
        """Generate VRP signals.

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
        """Generate VRP signal for a single ticker."""
        close: pd.Series = df["close"].astype(float)

        # Daily log returns
        log_ret = np.log(close / close.shift(1))

        # Annualised realized volatility over short and long windows
        short_std = log_ret.rolling(
            window=self.short_window, min_periods=self.short_window
        ).std()
        short_vol = short_std * np.sqrt(252)

        long_std = log_ret.rolling(
            window=self.long_window, min_periods=self.long_window
        ).std()
        long_vol = long_std * np.sqrt(252)

        # VRP proxy: (long_vol - short_vol) / long_vol
        # Positive when short < long (fear premium present -> sell vol -> long)
        # Negative when short > long (vol spike -> buy vol -> reduce/short)
        denom = long_vol.where(long_vol > 0, other=np.nan)
        vrp_ratio = (denom - short_vol) / denom

        # Apply tanh scaling for smooth [-1, 1] output
        signal = np.tanh(vrp_ratio * self.scale_factor)

        # NaN-safe: invalidate insufficient data region
        signal = signal.where(long_vol.notna(), other=np.nan)

        return signal
