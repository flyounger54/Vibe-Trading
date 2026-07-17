
# ============================================================
# 中文名称: 风险平价策略
# 简要说明: 按各标的滚动波动率的倒数分配权重，使每个资产对组合风险的
#           贡献大致相等。每 N 天重新平衡一次。
# 典型用途: 多资产组合的风险均衡配置。
# ============================================================
"""Risk Parity Allocation (alloc_risk_parity).

Allocate portfolio weights inversely proportional to each instrument's
rolling volatility so that each asset contributes roughly equal risk.
Weights are rebalanced every N days.
"""

from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd

__strategy_meta__ = {
    "id": "alloc_risk_parity",
    "nickname": "风险平价",
    "category": "allocation",
    "description": (
        "Allocate weights inversely proportional to rolling volatility. "
        "Rebalance every N days for equal risk contribution from each asset."
    ),
    "universe": ["equity_us", "equity_cn", "equity_hk", "crypto", "futures"],
    "frequency": ["1D"],
    "columns_required": ["close"],
    "default_params": {"vol_lookback": 60, "rebalance_days": 20},
    "risk_profile": "low",
    "min_bars": 65,
    "reference": "Qian, Risk Parity Portfolios, 2005",
    "factors_used": [],
}


class SignalEngine:
    """风险平价信号引擎。"""

    def __init__(self, **params: Any) -> None:
        self.vol_lookback: int = int(params.get("vol_lookback", 60))
        self.rebalance_days: int = int(params.get("rebalance_days", 20))
        if self.vol_lookback < 2:
            raise ValueError(f"vol_lookback must be >= 2, got {self.vol_lookback}")
        if self.rebalance_days < 1:
            raise ValueError(f"rebalance_days must be >= 1, got {self.rebalance_days}")

    def generate(
        self, data_map: Dict[str, pd.DataFrame]
    ) -> Dict[str, pd.Series]:
        """Generate risk-parity allocation weights.

        Returns:
            Dict mapping ticker -> pd.Series of weights in [0.0, 1.0].
            Weights across all tickers sum to 1.0 at each rebalance point.
        """
        if not data_map:
            return {}

        # Step 1: Compute rolling volatility for each instrument
        vol_map: Dict[str, pd.Series] = {}
        common_index: pd.Index | None = None

        for code, df in data_map.items():
            if "close" not in df.columns:
                continue
            close = df["close"].astype(float)
            ret = close.pct_change()
            vol = ret.rolling(
                window=self.vol_lookback, min_periods=self.vol_lookback
            ).std()
            vol_map[code] = vol
            if common_index is None:
                common_index = vol.index
            else:
                common_index = common_index.intersection(vol.index)

        if not vol_map or common_index is None or len(common_index) == 0:
            return {}

        # Step 2: Build a DataFrame of volatilities aligned to common index
        vol_df = pd.DataFrame(
            {code: vol.reindex(common_index) for code, vol in vol_map.items()}
        )

        # Step 3: Inverse-volatility weights (NaN-safe)
        inv_vol = 1.0 / vol_df.replace(0, np.nan)
        raw_weights = inv_vol.div(inv_vol.sum(axis=1), axis=0)

        # Step 4: Apply rebalance schedule — hold weights between rebalance dates
        rebalanced = raw_weights.copy()
        rebalanced.iloc[:] = np.nan

        # Mark rebalance rows
        for i in range(0, len(common_index), self.rebalance_days):
            rebalanced.iloc[i] = raw_weights.iloc[i]

        # Forward-fill between rebalance dates
        rebalanced = rebalanced.ffill()

        # Step 5: Produce per-ticker signal series (weight as signal in [0, 1])
        signals: Dict[str, pd.Series] = {}
        for code in vol_map:
            if code in rebalanced.columns:
                weight = rebalanced[code].reindex(vol_map[code].index)
                # Clamp to [0, 1] — allocation weights are non-negative
                weight = weight.clip(0.0, 1.0)
                signals[code] = weight

        return signals
