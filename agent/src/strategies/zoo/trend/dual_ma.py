
# ============================================================
# 中文名称: 双均线交叉策略
# 简要说明: 快慢均线交叉产生趋势信号。快均线上穿慢均线做多(+1)，下穿做空(-1)，
#           交叉之间保持方向不变。信号值 ∈ [-1, 1]，NaN 安全。
# 典型用途: 捕捉中短期趋势拐点，适用于各类资产的趋势跟踪。
# ============================================================
"""Dual Moving Average Crossover (trend_dual_ma).

Classic trend-following strategy: fast MA crossing above the slow MA
generates a long signal; crossing below generates a short/flat signal.
"""

from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd

__strategy_meta__ = {
    "id": "trend_dual_ma",
    "nickname": "双均线交叉",
    "category": "trend",
    "description": (
        "Fast/slow simple moving average crossover. "
        "Fast MA above slow MA produces a long signal (+1); "
        "fast MA below slow MA produces a short signal (-1)."
    ),
    "universe": ["equity_us", "equity_cn", "equity_hk", "crypto", "futures"],
    "frequency": ["1D"],
    "columns_required": ["close"],
    "default_params": {"fast_period": 5, "slow_period": 20},
    "risk_profile": "low",
    "min_bars": 30,
    "reference": "",
    "factors_used": [],
}


class SignalEngine:
    """双均线交叉信号引擎。"""

    def __init__(self, **params: Any) -> None:
        self.fast_period: int = int(params.get("fast_period", 5))
        self.slow_period: int = int(params.get("slow_period", 20))
        if self.fast_period >= self.slow_period:
            raise ValueError(
                f"fast_period ({self.fast_period}) must be < slow_period ({self.slow_period})"
            )

    def generate(
        self, data_map: Dict[str, pd.DataFrame]
    ) -> Dict[str, pd.Series]:
        """为每个标的生成双均线交叉信号。

        Returns:
            Dict mapping ticker -> pd.Series of signals in [-1.0, 1.0].
        """
        signals: Dict[str, pd.Series] = {}

        for code, df in data_map.items():
            if "close" not in df.columns:
                continue

            close: pd.Series = df["close"].astype(float)

            fast_ma = close.rolling(window=self.fast_period, min_periods=self.fast_period).mean()
            slow_ma = close.rolling(window=self.slow_period, min_periods=self.slow_period).mean()

            # 快均线 > 慢均线 → +1 (做多); 快均线 < 慢均线 → -1 (做空)
            raw = pd.Series(np.where(fast_ma > slow_ma, 1.0, -1.0), index=close.index)

            # 均线尚未就绪的区间置 NaN
            raw = raw.where(fast_ma.notna() & slow_ma.notna(), other=np.nan)

            signals[code] = raw

        return signals
