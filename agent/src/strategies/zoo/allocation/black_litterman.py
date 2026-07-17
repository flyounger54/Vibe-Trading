
# ============================================================
# 中文名称: Black-Litterman 配置策略
# 简要说明: 简化版 Black-Litterman 模型：以等权为先验(市值加权的代理)，
#           叠加动量信号作为观点(看涨/看跌)，通过 BL 公式得到后验权重。
#           每 N 天重新平衡，信号值 ∈ [0, 1]，NaN 安全。
# 典型用途: 多资产组合配置，融合先验均衡与主观/量化观点。
# ============================================================
"""Simplified Black-Litterman Allocation (alloc_black_litterman).

Start with equal-weight prior (proxy for market-cap weights), overlay
momentum-based views (positive momentum → bullish, negative → bearish),
and combine via the BL formula to obtain posterior weights.
"""

from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd

__strategy_meta__ = {
    "id": "alloc_black_litterman",
    "nickname": "Black-Litterman",
    "category": "allocation",
    "description": (
        "Simplified Black-Litterman: equal-weight prior with momentum-based "
        "views. Posterior weights combine market equilibrium and quantitative "
        "views via the BL formula. Rebalanced every N days."
    ),
    "universe": ["equity_cn", "equity_us"],
    "frequency": ["1D"],
    "columns_required": ["close"],
    "default_params": {
        "lookback": 60,
        "tau": 0.05,
        "confidence": 0.5,
        "rebalance_days": 20,
    },
    "risk_profile": "medium",
    "min_bars": 65,
    "reference": "Black & Litterman, Global Portfolio Optimization, FAJ 1992",
    "factors_used": [],
}


class SignalEngine:
    """Black-Litterman 配置信号引擎。"""

    def __init__(self, **params: Any) -> None:
        self.lookback: int = int(params.get("lookback", 60))
        self.tau: float = float(params.get("tau", 0.05))
        self.confidence: float = float(params.get("confidence", 0.5))
        self.rebalance_days: int = int(params.get("rebalance_days", 20))
        if self.lookback < 2:
            raise ValueError(f"lookback must be >= 2, got {self.lookback}")
        if self.tau <= 0:
            raise ValueError(f"tau must be > 0, got {self.tau}")
        if not 0.0 < self.confidence <= 1.0:
            raise ValueError(
                f"confidence must be in (0, 1], got {self.confidence}"
            )
        if self.rebalance_days < 1:
            raise ValueError(
                f"rebalance_days must be >= 1, got {self.rebalance_days}"
            )

    def generate(
        self, data_map: Dict[str, pd.DataFrame]
    ) -> Dict[str, pd.Series]:
        """Generate Black-Litterman allocation weights.

        Returns:
            Dict mapping ticker -> pd.Series of weights in [0.0, 1.0].
            Weights across all tickers sum to ~1.0 at each rebalance point.
        """
        if not data_map:
            return {}

        # Step 1: Collect trailing returns for each instrument
        ret_map: Dict[str, pd.Series] = {}
        common_index: pd.Index | None = None

        for code, df in data_map.items():
            if "close" not in df.columns:
                continue
            close = df["close"].astype(float)
            ret = close.pct_change()
            ret_map[code] = ret
            if common_index is None:
                common_index = ret.index
            else:
                common_index = common_index.intersection(ret.index)

        if not ret_map or common_index is None or len(common_index) == 0:
            return {}

        n_assets = len(ret_map)
        codes = list(ret_map.keys())

        # Step 2: Build trailing return DataFrame aligned to common index
        ret_df = pd.DataFrame(
            {code: ret.reindex(common_index) for code, ret in ret_map.items()}
        )

        # Step 3: Compute trailing cumulative return (momentum signal)
        trailing_ret = ret_df.rolling(
            window=self.lookback, min_periods=self.lookback
        ).sum()

        # Step 4: Compute rolling volatility for normalization
        trailing_vol = ret_df.rolling(
            window=self.lookback, min_periods=self.lookback
        ).std()

        # Step 5: Prior weights = 1/N (equal weight)
        prior_weight = 1.0 / n_assets

        # Step 6: Compute posterior weights row-by-row
        # View = sign(trailing_return) * confidence * (|trailing_return| / trailing_vol)
        # This gives a magnitude-scaled view direction
        # Posterior = prior + tau * view_adjustment, then normalize
        ret_magnitude = trailing_ret.abs()
        vol_safe = trailing_vol.replace(0, np.nan)
        view_strength = (ret_magnitude / vol_safe).clip(upper=2.0)  # cap at 2

        # View direction: positive momentum → bullish, negative → bearish
        view_direction = np.sign(trailing_ret)

        # View adjustment: direction * confidence * normalized strength
        view_adjustment = view_direction * self.confidence * view_strength

        # Posterior (unnormalized): prior + tau * view_adjustment
        posterior_raw = prior_weight + self.tau * view_adjustment

        # Floor at zero — no negative weights in allocation
        posterior_raw = posterior_raw.clip(lower=0.0)

        # Normalize so weights sum to 1.0 per row
        row_sum = posterior_raw.sum(axis=1).replace(0, np.nan)
        posterior = posterior_raw.div(row_sum, axis=0)

        # Step 7: Apply rebalance schedule
        rebalanced = posterior.copy()
        rebalanced.iloc[:] = np.nan

        for i in range(0, len(common_index), self.rebalance_days):
            rebalanced.iloc[i] = posterior.iloc[i]

        rebalanced = rebalanced.ffill()

        # Step 8: Produce per-ticker signal series
        signals: Dict[str, pd.Series] = {}
        for code in codes:
            if code in rebalanced.columns:
                weight = rebalanced[code].reindex(ret_map[code].index)
                weight = weight.clip(0.0, 1.0)
                signals[code] = weight

        return signals
