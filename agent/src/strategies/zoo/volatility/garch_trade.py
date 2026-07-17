
# ============================================================
# 中文名称: GARCH波动率交易
# 简要说明: 简化版 GARCH(1,1)，使用 EWMA 作为波动率代理。比较短期与长期
#           EWMA 波动率估计：短期波动远高于长期 → 波动率回归预期 → 做多
#           （卖出波动率）；短期波动远低于长期 → 波动率扩张预期 → 减仓。
#           信号 = (long_vol - short_vol) / long_vol, 裁剪至 [-1, 1]。
# 典型用途: 基于波动率均值回归的方向性交易。
# ============================================================
"""GARCH Volatility Trade (vol_garch).

Simplified GARCH(1,1) using EWMA as a volatility proxy. Compares
short-term EWMA vol against long-term EWMA vol. When short-term vol
is elevated relative to long-term (mean-reversion expected), signal
is positive (go long as vol compresses). When short-term vol is
depressed, signal is negative (vol expansion expected, reduce).

Signal = (long_vol - short_vol) / long_vol, clipped to [-1, 1].
"""

from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd

__strategy_meta__ = {
    "id": "vol_garch",
    "nickname": "GARCH波动率交易",
    "category": "volatility",
    "description": (
        "Simplified GARCH(1,1) volatility model using EWMA as proxy. "
        "Compares short-term EWMA vol vs long-term EWMA vol. "
        "When short-term vol >> long-term -> expect mean-reversion -> "
        "go long (sell vol). When short-term << long-term -> expect "
        "vol expansion -> reduce position. "
        "Signal = (long_vol - short_vol) / long_vol, clipped to [-1, 1]."
    ),
    "universe": ["equity_cn", "equity_us"],
    "frequency": ["1D"],
    "columns_required": ["close"],
    "default_params": {"short_span": 10, "long_span": 60, "ewma_lambda": 0.94},
    "risk_profile": "high",
    "min_bars": 65,
    "reference": "Bollerslev, Generalized Autoregressive Conditional Heteroskedasticity, 1986",
    "factors_used": [],
}


class SignalEngine:
    """GARCH波动率交易信号引擎。"""

    def __init__(self, **params: Any) -> None:
        self.short_span: int = int(params.get("short_span", 10))
        self.long_span: int = int(params.get("long_span", 60))
        self.ewma_lambda: float = float(params.get("ewma_lambda", 0.94))
        if self.short_span < 2:
            raise ValueError(f"short_span must be >= 2, got {self.short_span}")
        if self.long_span <= self.short_span:
            raise ValueError(
                f"long_span ({self.long_span}) must be > short_span ({self.short_span})"
            )
        if not (0.0 < self.ewma_lambda < 1.0):
            raise ValueError(
                f"ewma_lambda must be in (0, 1), got {self.ewma_lambda}"
            )

    def generate(
        self, data_map: Dict[str, pd.DataFrame]
    ) -> Dict[str, pd.Series]:
        """Generate GARCH volatility trade signals.

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
        """Generate signal for a single ticker."""
        close: pd.Series = df["close"].astype(float)

        # Daily log returns
        log_ret = np.log(close / close.shift(1))

        # Squared returns as variance proxy
        ret_sq = log_ret ** 2

        # EWMA variance estimates (using pandas ewm with specified span)
        # Short-term EWMA vol
        short_var = ret_sq.ewm(span=self.short_span, adjust=False).mean()
        short_vol = np.sqrt(short_var) * np.sqrt(252)

        # Long-term EWMA vol (using ewma_lambda for GARCH-like decay)
        # Convert lambda to span: span = (2 / (1 - lambda)) - 1
        # For lambda=0.94, span ~= 32.3, but we use long_span directly
        long_var = ret_sq.ewm(span=self.long_span, adjust=False).mean()
        long_vol = np.sqrt(long_var) * np.sqrt(252)

        # Signal: (long_vol - short_vol) / long_vol
        # Positive when short_vol < long_vol (vol compression expected -> go long)
        # Negative when short_vol > long_vol (vol expansion -> reduce)
        denom = long_vol.where(long_vol > 0, other=np.nan)
        raw_signal = (denom - short_vol) / denom

        # Clip to [-1, 1]
        signal = raw_signal.clip(-1.0, 1.0)

        # NaN-safe: invalidate where we don't have enough data
        min_required = self.long_span + 1
        signal.iloc[:min_required] = np.nan

        return signal
