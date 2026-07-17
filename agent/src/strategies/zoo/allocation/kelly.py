
# ============================================================
# 中文名称: Kelly仓位管理策略
# 简要说明: 基于 Kelly 公式 f* = (p*b - q) / b 计算最优仓位比例。
#           从滚动窗口的收益率序列中估计胜率 p 和盈亏比 b，
#           使用半 Kelly (f*/2) 以保守方式控制仓位。负 Kelly 值归零。
# 典型用途: 多品种组合的仓位优化配置。
# ============================================================
"""Kelly Criterion Position Sizing (alloc_kelly).

Estimate optimal position size using the Kelly criterion:
f* = (p * b - q) / b, where p = win probability, b = avg_win / avg_loss,
q = 1 - p. Apply half-Kelly (f*/2) for conservatism. Negative Kelly
fractions are floored to zero (no shorting based on Kelly alone).
"""

from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd

__strategy_meta__ = {
    "id": "alloc_kelly",
    "nickname": "Kelly仓位管理",
    "category": "allocation",
    "description": (
        "Optimal position sizing via Kelly criterion: f* = (p*b - q) / b. "
        "Estimate win probability and win/loss ratio from rolling returns. "
        "Apply half-Kelly for conservatism; negative Kelly floors to zero."
    ),
    "universe": ["equity_us", "equity_cn", "equity_hk", "crypto", "futures"],
    "frequency": ["1D"],
    "columns_required": ["close"],
    "default_params": {"lookback": 60, "kelly_fraction": 0.5, "min_trades": 20},
    "risk_profile": "medium",
    "min_bars": 65,
    "reference": "Kelly, A New Interpretation of Information Rate, 1956",
    "factors_used": [],
}


class SignalEngine:
    """Kelly仓位管理信号引擎。"""

    def __init__(self, **params: Any) -> None:
        self.lookback: int = int(params.get("lookback", 60))
        self.kelly_fraction: float = float(params.get("kelly_fraction", 0.5))
        self.min_trades: int = int(params.get("min_trades", 20))
        if self.lookback < 2:
            raise ValueError(f"lookback must be >= 2, got {self.lookback}")
        if not 0.0 < self.kelly_fraction <= 1.0:
            raise ValueError(
                f"kelly_fraction must be in (0, 1], got {self.kelly_fraction}"
            )
        if self.min_trades < 1:
            raise ValueError(f"min_trades must be >= 1, got {self.min_trades}")

    def _rolling_kelly(self, returns: pd.Series) -> pd.Series:
        """Compute rolling half-Kelly fraction from a return series.

        For each rolling window:
        1. Count wins (ret > 0) and losses (ret < 0); skip zeros.
        2. win_rate p = n_wins / n_trades.
        3. avg_win / avg_loss = b.
        4. Kelly f* = (p * b - (1-p)) / b.
        5. Apply kelly_fraction multiplier (half-Kelly by default).
        6. Floor negative values to 0.
        """
        n = len(returns)
        result = pd.Series(np.nan, index=returns.index, dtype=float)

        # Use a rolling approach: for each position, look at the trailing window
        for i in range(self.lookback, n):
            window = returns.iloc[i - self.lookback : i]

            # Drop NaN values within the window
            valid = window.dropna()

            wins = valid[valid > 0]
            losses = valid[valid < 0]

            n_wins = len(wins)
            n_losses = len(losses)
            n_trades = n_wins + n_losses

            # Require minimum number of trades for statistical significance
            if n_trades < self.min_trades:
                result.iloc[i] = np.nan
                continue

            p = n_wins / n_trades
            q = 1.0 - p

            # Average win and average loss (absolute value)
            avg_win = wins.mean() if n_wins > 0 else 0.0
            avg_loss = abs(losses.mean()) if n_losses > 0 else 0.0

            # Avoid division by zero: if no losses, b is infinite -> Kelly = p
            # If no wins, Kelly is negative -> will be floored to 0
            if avg_loss == 0.0:
                if avg_win > 0.0:
                    # Perfect record: Kelly fraction = p (all wins, no losses)
                    kelly = p
                else:
                    # No meaningful trades
                    result.iloc[i] = np.nan
                    continue
            else:
                b = avg_win / avg_loss
                # Kelly formula: f* = (p * b - q) / b
                kelly = (p * b - q) / b

            # Apply fractional Kelly (e.g., half-Kelly)
            fractional = kelly * self.kelly_fraction

            # Floor negative Kelly to 0 (don't short on Kelly alone)
            fractional = max(fractional, 0.0)

            result.iloc[i] = fractional

        return result

    def generate(
        self, data_map: Dict[str, pd.DataFrame]
    ) -> Dict[str, pd.Series]:
        """Generate Kelly-criterion position sizing signals.

        Returns:
            Dict mapping ticker -> pd.Series of signals in [0.0, 1.0].
            Signal represents recommended position size fraction.
        """
        signals: Dict[str, pd.Series] = {}

        for code, df in data_map.items():
            if "close" not in df.columns:
                continue

            close = df["close"].astype(float)
            returns = close.pct_change()

            kelly_signal = self._rolling_kelly(returns)

            # Clip to [0, 1] — Kelly position sizing is non-negative
            kelly_signal = kelly_signal.clip(0.0, 1.0)

            signals[code] = kelly_signal

        return signals
