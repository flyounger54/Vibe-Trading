
# ============================================================
# 中文名称: 时间序列动量策略
# 简要说明: 对每个标的独立计算：过去 N 日收益率 > 0 做多，< 0 做空。
#           信号强度按收益率幅度缩放并经波动率目标调整，上限 [-1, 1]。
#           NaN 安全，无前瞻偏差。
# 典型用途: 在趋势性资产上捕捉时间序列上的持续动量效应。
# ============================================================
"""Time-Series Momentum (mom_tsmom).

For each instrument independently: if the past N-day return is positive,
go long; if negative, go short. Signal magnitude is scaled by the return
divided by realised volatility, then clipped to [-1, 1].
"""

from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd

__strategy_meta__ = {
    "id": "mom_tsmom",
    "nickname": "时间序列动量",
    "category": "momentum",
    "description": (
        "Time-series momentum. For each instrument: if the trailing N-day "
        "return is positive, go long; if negative, go short. Signal magnitude "
        "is scaled by return relative to realised volatility, with a "
        "volatility-targeting overlay."
    ),
    "universe": ["equity_us", "equity_cn", "equity_hk", "crypto", "futures"],
    "frequency": ["1D"],
    "columns_required": ["close"],
    "default_params": {"lookback": 252, "vol_lookback": 60, "vol_target": 0.15},
    "risk_profile": "medium",
    "min_bars": 260,
    "reference": "Moskowitz, Ooi & Pedersen, Time Series Momentum, JFE 2012",
    "factors_used": [],
}


class SignalEngine:
    """时间序列动量信号引擎。"""

    def __init__(self, **params: Any) -> None:
        self.lookback: int = int(params.get("lookback", 252))
        self.vol_lookback: int = int(params.get("vol_lookback", 60))
        self.vol_target: float = float(params.get("vol_target", 0.15))
        if self.lookback < 1:
            raise ValueError(f"lookback ({self.lookback}) must be >= 1")
        if self.vol_lookback < 2:
            raise ValueError(f"vol_lookback ({self.vol_lookback}) must be >= 2")
        if self.vol_target <= 0:
            raise ValueError(f"vol_target ({self.vol_target}) must be > 0")

    def generate(
        self, data_map: Dict[str, pd.DataFrame]
    ) -> Dict[str, pd.Series]:
        """为每个标的独立生成时间序列动量信号。

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
        """对单个标的生成时间序列动量信号。"""
        close: pd.Series = df["close"].astype(float)

        # Past N-day return (simple)
        past_return = close / close.shift(self.lookback) - 1.0

        # Annualised realised volatility (daily log returns)
        log_ret = np.log(close / close.shift(1))
        realised_vol = log_ret.rolling(
            window=self.vol_lookback, min_periods=self.vol_lookback
        ).std() * np.sqrt(252)

        # Direction: sign of past return
        direction = np.sign(past_return)

        # Magnitude: volatility-targeting scalar
        # Scale = vol_target / realised_vol (how much leverage to apply)
        # Capped so final signal stays in [-1, 1]
        vol_scalar = self.vol_target / realised_vol.where(
            realised_vol > 0, other=np.nan
        )

        # Raw signal = direction * min(vol_scalar, 1.0)
        # This gives full signal (+/-1) when vol is at or below target,
        # and reduced signal when vol exceeds target
        raw = direction * vol_scalar.clip(upper=1.0)

        # Clip to [-1.0, 1.0] for safety
        signal = raw.clip(lower=-1.0, upper=1.0)

        # NaN where inputs are not yet available
        needs_data = past_return.isna() | realised_vol.isna()
        signal = signal.where(~needs_data, other=np.nan)

        return signal
