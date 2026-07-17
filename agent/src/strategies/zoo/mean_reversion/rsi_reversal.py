
# ============================================================
# 中文名称: RSI超买超卖反转策略
# 简要说明: RSI 低于超卖阈值做多（信号强度与 RSI 极端程度成正比），
#           高于超买阈值做空。中性区间信号为零。信号值 ∈ [-1, 1]，NaN 安全。
# 典型用途: 捕捉短期超买超卖后的均值回归机会。
# ============================================================
"""RSI Overbought/Oversold Reversal (mr_rsi_reversal).

Go long when RSI falls below the oversold threshold; go short when RSI
rises above the overbought threshold. Signal strength is proportional
to the RSI extremity beyond the threshold.
"""

from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd

__strategy_meta__ = {
    "id": "mr_rsi_reversal",
    "nickname": "RSI超买超卖",
    "category": "mean_reversion",
    "description": (
        "RSI overbought/oversold reversal. RSI below the oversold threshold "
        "produces a long signal; RSI above the overbought threshold produces "
        "a short signal. Signal strength scales linearly with RSI extremity."
    ),
    "universe": ["equity_us", "equity_cn", "equity_hk", "crypto", "futures"],
    "frequency": ["1D"],
    "columns_required": ["close"],
    "default_params": {"period": 14, "oversold": 30, "overbought": 70},
    "risk_profile": "low",
    "min_bars": 20,
    "reference": "Wilder, New Concepts in Technical Trading Systems, 1978",
    "factors_used": [],
}


def _compute_rsi(close: pd.Series, period: int) -> pd.Series:
    """Wilder-smoothed RSI, range 0-100."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    alpha = 1.0 / period
    avg_gain = gain.ewm(alpha=alpha, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=alpha, min_periods=period).mean()
    rs = avg_gain / avg_loss.where(avg_loss > 0, other=np.nan)
    rsi = 100.0 - 100.0 / (1.0 + rs)
    return rsi


class SignalEngine:
    """RSI 超买超卖反转信号引擎。"""

    def __init__(self, **params: Any) -> None:
        self.period: int = int(params.get("period", 14))
        self.oversold: float = float(params.get("oversold", 30))
        self.overbought: float = float(params.get("overbought", 70))
        if self.period < 2:
            raise ValueError(f"period ({self.period}) must be >= 2")
        if self.oversold >= self.overbought:
            raise ValueError(
                f"oversold ({self.oversold}) must be < overbought ({self.overbought})"
            )

    def generate(
        self, data_map: Dict[str, pd.DataFrame]
    ) -> Dict[str, pd.Series]:
        """为每个标的生成 RSI 反转信号。

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
        """对单个标的生成 RSI 反转信号。"""
        close: pd.Series = df["close"].astype(float)
        rsi = _compute_rsi(close, self.period)

        # Neutral zone: signal = 0
        signal = pd.Series(0.0, index=close.index)

        # Oversold zone: RSI < oversold -> long signal
        # Scale linearly: RSI=oversold -> 0; RSI=0 -> +1.0
        oversold_mask = rsi < self.oversold
        signal = signal.where(
            ~oversold_mask,
            other=(self.oversold - rsi) / self.oversold,
        )

        # Overbought zone: RSI > overbought -> short signal
        # Scale linearly: RSI=overbought -> 0; RSI=100 -> -1.0
        overbought_mask = rsi > self.overbought
        signal = signal.where(
            ~overbought_mask,
            other=-(rsi - self.overbought) / (100.0 - self.overbought),
        )

        # Clip to [-1.0, 1.0] for safety
        signal = signal.clip(lower=-1.0, upper=1.0)

        # NaN where RSI is not yet computed
        signal = signal.where(rsi.notna(), other=np.nan)

        return signal
