
# ============================================================
# 中文名称: 全天候组合策略
# 简要说明: 根据近期收益和波动率判断宏观经济象限（增长/通胀），
#           向当前象限表现好的资产倾斜权重，月度再平衡。
# 典型用途: 跨周期的多资产稳健配置。
# ============================================================
"""All-Weather Allocation (alloc_all_weather).

Simplified Bridgewater All-Weather: classify each instrument's recent
performance by a growth/inflation regime proxy (returns vs volatility),
then tilt weights toward assets performing well in the detected regime.
Rebalanced monthly.
"""

from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd

__strategy_meta__ = {
    "id": "alloc_all_weather",
    "nickname": "全天候组合",
    "category": "allocation",
    "description": (
        "Simplified All-Weather strategy: detect growth/inflation regime "
        "from recent returns and volatility, tilt weights toward assets "
        "performing well in the current regime. Rebalance monthly."
    ),
    "universe": ["equity_us", "equity_cn"],
    "frequency": ["1D"],
    "columns_required": ["close"],
    "default_params": {"lookback": 60, "rebalance_days": 20},
    "risk_profile": "low",
    "min_bars": 65,
    "reference": "Bridgewater, All Weather Strategy, Ray Dalio",
    "factors_used": [],
}


class SignalEngine:
    """全天候组合信号引擎。"""

    def __init__(self, **params: Any) -> None:
        self.lookback: int = int(params.get("lookback", 60))
        self.rebalance_days: int = int(params.get("rebalance_days", 20))
        if self.lookback < 2:
            raise ValueError(f"lookback must be >= 2, got {self.lookback}")
        if self.rebalance_days < 1:
            raise ValueError(f"rebalance_days must be >= 1, got {self.rebalance_days}")

    def generate(
        self, data_map: Dict[str, pd.DataFrame]
    ) -> Dict[str, pd.Series]:
        """Generate regime-tilted allocation weights.

        Returns:
            Dict mapping ticker -> pd.Series of weights in [0.0, 1.0].
        """
        if not data_map:
            return {}

        # Step 1: Compute rolling return and volatility for each instrument
        metrics: Dict[str, Dict[str, pd.Series]] = {}
        common_index: pd.Index | None = None

        for code, df in data_map.items():
            if "close" not in df.columns:
                continue
            close = df["close"].astype(float)
            ret = close.pct_change()

            roll_ret = ret.rolling(
                window=self.lookback, min_periods=self.lookback
            ).mean()
            roll_vol = ret.rolling(
                window=self.lookback, min_periods=self.lookback
            ).std()

            metrics[code] = {"return": roll_ret, "volatility": roll_vol}

            if common_index is None:
                common_index = close.index
            else:
                common_index = common_index.intersection(close.index)

        if not metrics or common_index is None or len(common_index) == 0:
            return {}

        # Step 2: Build aligned DataFrames
        ret_df = pd.DataFrame(
            {c: m["return"].reindex(common_index) for c, m in metrics.items()}
        )
        vol_df = pd.DataFrame(
            {c: m["volatility"].reindex(common_index) for c, m in metrics.items()}
        )

        # Step 3: Regime classification per row
        # "Growth" proxy: cross-sectional median return
        # "Inflation/Risk" proxy: cross-sectional median volatility
        median_ret = ret_df.median(axis=1)
        median_vol = vol_df.median(axis=1)

        # Regime score for each asset:
        # - High return + Low vol  -> strong performer in favorable regime -> high weight
        # - Low return + High vol  -> weak performer -> low weight
        # Score = normalized return rank - normalized vol rank (cross-sectional)
        n_assets = ret_df.shape[1]
        if n_assets <= 1:
            # Single asset: equal weight
            for code in metrics:
                idx = metrics[code]["return"].index
                signals_series = pd.Series(1.0, index=idx)
                signals_series = signals_series.where(
                    metrics[code]["return"].notna(), other=np.nan
                )
                return {code: signals_series}

        ret_rank = ret_df.rank(axis=1, pct=True)  # higher return -> higher rank
        vol_rank = vol_df.rank(axis=1, pct=True, ascending=False)  # lower vol -> higher rank

        # Combined regime score in [0, 1]
        regime_score = (ret_rank + vol_rank) / 2.0

        # Step 4: Convert scores to weights (normalize rows to sum to 1)
        weight_sum = regime_score.sum(axis=1).replace(0, np.nan)
        raw_weights = regime_score.div(weight_sum, axis=0)

        # Step 5: Apply rebalance schedule
        rebalanced = raw_weights.copy()
        rebalanced.iloc[:] = np.nan

        for i in range(0, len(common_index), self.rebalance_days):
            rebalanced.iloc[i] = raw_weights.iloc[i]

        rebalanced = rebalanced.ffill()

        # Step 6: Output signals
        signals: Dict[str, pd.Series] = {}
        for code in metrics:
            if code in rebalanced.columns:
                weight = rebalanced[code].reindex(metrics[code]["return"].index)
                weight = weight.clip(0.0, 1.0)
                signals[code] = weight

        return signals
