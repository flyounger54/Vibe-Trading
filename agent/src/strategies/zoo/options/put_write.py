
# ============================================================
# 中文名称: 卖出看跌策略
# 简要说明: 模拟卖出看跌期权的收益特征。低波动环境（有利于卖出看跌）→ 满仓
#           做多（收取权利金）。高波动 → 部分减仓。市场急跌 → 大幅减仓
#           （看跌期权被行权）。与备兑看涨类似但波动敏感度不同：急跌时
#           更激进减仓，恢复更快。
# 典型用途: 在稳定市场环境中通过卖出看跌期权策略获取权利金收入。
# ============================================================
"""Put-Write Strategy (opt_put_write).

Simulate selling puts by holding a long position modulated by the
volatility regime. In low-vol environments (favorable for put selling),
maintain full long (collecting premium). In high-vol, reduce to partial
position. When the market drops sharply, significantly reduce position
(put gets exercised). More aggressive reduction on vol spikes than
covered call, with faster recovery.

Signal represents the net delta of a put-write position.
"""

from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd

__strategy_meta__ = {
    "id": "opt_put_write",
    "nickname": "卖出看跌",
    "category": "options",
    "description": (
        "Put-write strategy proxy. Simulate selling puts by holding a long "
        "position modulated by vol regime. Low vol -> full long (collecting "
        "premium). High vol -> partial position. Sharp market drop -> "
        "significant reduction (put exercised). More aggressive vol-spike "
        "reduction than covered call, faster recovery. "
        "Signal represents net delta of put-write position."
    ),
    "universe": ["equity_cn", "equity_us"],
    "frequency": ["1D"],
    "columns_required": ["close"],
    "default_params": {
        "vol_lookback": 20,
        "vol_threshold": 0.25,
        "drawdown_lookback": 10,
        "max_drawdown": 0.05,
    },
    "risk_profile": "medium",
    "min_bars": 25,
    "reference": "CBOE PutWrite Index (PUT)",
    "factors_used": [],
}


class SignalEngine:
    """卖出看跌信号引擎。"""

    def __init__(self, **params: Any) -> None:
        self.vol_lookback: int = int(params.get("vol_lookback", 20))
        self.vol_threshold: float = float(params.get("vol_threshold", 0.25))
        self.drawdown_lookback: int = int(params.get("drawdown_lookback", 10))
        self.max_drawdown: float = float(params.get("max_drawdown", 0.05))
        if self.vol_lookback < 2:
            raise ValueError(
                f"vol_lookback must be >= 2, got {self.vol_lookback}"
            )
        if self.vol_threshold <= 0:
            raise ValueError(
                f"vol_threshold must be > 0, got {self.vol_threshold}"
            )
        if self.drawdown_lookback < 2:
            raise ValueError(
                f"drawdown_lookback must be >= 2, got {self.drawdown_lookback}"
            )
        if not (0.0 < self.max_drawdown < 1.0):
            raise ValueError(
                f"max_drawdown must be in (0, 1), got {self.max_drawdown}"
            )

    def generate(
        self, data_map: Dict[str, pd.DataFrame]
    ) -> Dict[str, pd.Series]:
        """Generate put-write signals.

        Returns:
            Dict mapping ticker -> pd.Series of signals in [0.0, 1.0].
            Put-write is always net long, so signals are non-negative.
        """
        signals: Dict[str, pd.Series] = {}

        for code, df in data_map.items():
            if "close" not in df.columns:
                continue
            signals[code] = self._generate_one(df)

        return signals

    def _generate_one(self, df: pd.DataFrame) -> pd.Series:
        """Generate put-write signal for a single ticker."""
        close: pd.Series = df["close"].astype(float)

        # Daily log returns
        log_ret = np.log(close / close.shift(1))

        # Annualised realized volatility
        rolling_std = log_ret.rolling(
            window=self.vol_lookback, min_periods=self.vol_lookback
        ).std()
        realized_vol = rolling_std * np.sqrt(252)

        # Drawdown from rolling high (shorter lookback for faster reaction)
        rolling_high = close.rolling(
            window=self.drawdown_lookback, min_periods=self.drawdown_lookback
        ).max()
        drawdown = (close - rolling_high) / rolling_high.where(
            rolling_high > 0, other=np.nan
        )

        # --- Position sizing logic ---
        valid = realized_vol.notna() & rolling_high.notna()
        signal = pd.Series(np.nan, index=close.index, dtype=float)

        # Base: full long position in low-vol environment (1.0)
        signal[valid] = 1.0

        # High vol regime: reduce position more aggressively than covered call
        # Use exponential decay: position = exp(-k * (vol/threshold - 1))
        # where k controls aggressiveness of reduction
        vol_ratio = realized_vol / self.vol_threshold
        # For vol at threshold: ratio=1 -> factor=1.0 (no reduction)
        # For vol at 2x threshold: ratio=2 -> factor~=0.37
        # For vol at 3x threshold: ratio=3 -> factor~=0.135
        vol_factor = np.exp(-1.0 * (vol_ratio - 1.0).clip(lower=0.0))
        high_vol_mask = valid & (realized_vol > self.vol_threshold)
        signal[high_vol_mask] = vol_factor[high_vol_mask]

        # Sharp drawdown: aggressive reduction (put gets exercised)
        # Scale reduction: at max_drawdown -> 0.3, at 2x max_drawdown -> 0.1
        dd_ratio = (-drawdown / self.max_drawdown).clip(lower=0.0)
        dd_factor = np.exp(-1.5 * dd_ratio)
        sharp_drop = valid & (drawdown < -self.max_drawdown)
        # Take the minimum of vol-adjusted and drawdown-adjusted position
        signal[sharp_drop] = np.minimum(
            signal[sharp_drop], dd_factor[sharp_drop]
        )

        # Floor at 0.05 (never fully exit, small premium collection remains)
        signal = signal.clip(lower=0.05, upper=1.0)

        # NaN where data is insufficient
        signal = signal.where(valid, other=np.nan)

        return signal
