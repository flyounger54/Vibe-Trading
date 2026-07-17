
# ============================================================
# 中文名称: DeFi 收益轮动策略
# 简要说明: 在加密资产间按风险调整收益轮动配置。按 return/volatility 排名，
#           头部资产获得更高权重。若有 apy 列直接使用，否则用历史收益代理。
#           每 N 天重新平衡，信号值 ∈ [0, 1]，NaN 安全。
# 典型用途: 加密货币组合的收益率轮动配置。
# ============================================================
"""DeFi Yield Rotation (crypto_defi_yield).

Rotate between crypto assets based on risk-adjusted return (Sharpe-like
ratio). Top performers by return/volatility get higher allocation.
Uses APY column directly if available, otherwise trailing return as proxy.
"""

from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd

__strategy_meta__ = {
    "id": "crypto_defi_yield",
    "nickname": "DeFi收益轮动",
    "category": "crypto",
    "description": (
        "Rotate between crypto assets by risk-adjusted return. "
        "Rank instruments by return/volatility, allocate more to top "
        "performers. Uses APY directly if available, else trailing return."
    ),
    "universe": ["crypto"],
    "frequency": ["1D"],
    "columns_required": ["close"],
    "default_params": {
        "lookback": 14,
        "vol_lookback": 30,
        "rebalance_days": 7,
        "top_pct": 0.3,
    },
    "risk_profile": "high",
    "min_bars": 35,
    "reference": "DeFi 收益轮动, 已有 defi-yield skill",
    "factors_used": [],
}


class SignalEngine:
    """DeFi 收益轮动信号引擎。"""

    def __init__(self, **params: Any) -> None:
        self.lookback: int = int(params.get("lookback", 14))
        self.vol_lookback: int = int(params.get("vol_lookback", 30))
        self.rebalance_days: int = int(params.get("rebalance_days", 7))
        self.top_pct: float = float(params.get("top_pct", 0.3))
        if self.lookback < 2:
            raise ValueError(f"lookback must be >= 2, got {self.lookback}")
        if self.vol_lookback < 2:
            raise ValueError(f"vol_lookback must be >= 2, got {self.vol_lookback}")
        if self.rebalance_days < 1:
            raise ValueError(
                f"rebalance_days must be >= 1, got {self.rebalance_days}"
            )
        if not 0.0 < self.top_pct <= 1.0:
            raise ValueError(f"top_pct must be in (0, 1], got {self.top_pct}")

    def generate(
        self, data_map: Dict[str, pd.DataFrame]
    ) -> Dict[str, pd.Series]:
        """Generate DeFi yield rotation signals.

        Returns:
            Dict mapping ticker -> pd.Series of weights in [0.0, 1.0].
            Only top_pct fraction of assets receive non-zero allocation.
        """
        if not data_map:
            return {}

        # Step 1: Compute score for each instrument
        score_map: Dict[str, pd.Series] = {}
        ret_map: Dict[str, pd.Series] = {}
        common_index: pd.Index | None = None

        for code, df in data_map.items():
            if "close" not in df.columns:
                continue

            close = df["close"].astype(float)
            ret = close.pct_change()
            ret_map[code] = ret

            if "apy" in df.columns:
                # Direct yield measure — use APY as score
                apy = df["apy"].astype(float)
                # Rolling mean APY for smoothing
                score = apy.rolling(
                    window=self.lookback, min_periods=self.lookback
                ).mean()
            else:
                # Proxy: risk-adjusted return = trailing_return / trailing_vol
                trailing_ret = ret.rolling(
                    window=self.lookback, min_periods=self.lookback
                ).sum()
                trailing_vol = ret.rolling(
                    window=self.vol_lookback, min_periods=self.vol_lookback
                ).std()
                vol_safe = trailing_vol.replace(0, np.nan)
                score = trailing_ret / vol_safe

            score_map[code] = score
            if common_index is None:
                common_index = score.index
            else:
                common_index = common_index.intersection(score.index)

        if not score_map or common_index is None or len(common_index) == 0:
            return {}

        codes = list(score_map.keys())
        n_assets = len(codes)
        n_top = max(1, int(np.ceil(n_assets * self.top_pct)))

        # Step 2: Build score DataFrame aligned to common index
        score_df = pd.DataFrame(
            {code: score.reindex(common_index) for code, score in score_map.items()}
        )

        # Step 3: Rank and allocate — top assets get weight, rest get zero
        # Use rank (ascending=False → higher score = lower rank number)
        rank_df = score_df.rank(axis=1, ascending=False, method="min")

        # Top assets: rank <= n_top
        is_top = rank_df <= n_top

        # Weight proportional to score among top assets (softmax-like)
        # Use score itself — shift to positive, then normalize
        top_scores = score_df.where(is_top, other=np.nan)

        # Shift scores to be non-negative within each row
        row_min = top_scores.min(axis=1)
        shifted = top_scores.sub(row_min, axis=0)

        # Add small epsilon to avoid all-zero rows
        shifted = shifted + 1e-8

        # Normalize to sum to 1.0 per row
        row_sum = shifted.sum(axis=1).replace(0, np.nan)
        weights = shifted.div(row_sum, axis=0)

        # Non-top assets get zero weight
        weights = weights.fillna(0.0)

        # Step 4: Apply rebalance schedule
        rebalanced = weights.copy()
        rebalanced.iloc[:] = np.nan

        for i in range(0, len(common_index), self.rebalance_days):
            rebalanced.iloc[i] = weights.iloc[i]

        rebalanced = rebalanced.ffill()

        # Step 5: Produce per-ticker signal series
        signals: Dict[str, pd.Series] = {}
        for code in codes:
            if code in rebalanced.columns:
                weight = rebalanced[code].reindex(ret_map[code].index)
                weight = weight.clip(0.0, 1.0)
                signals[code] = weight

        return signals
