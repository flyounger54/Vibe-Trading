
# ============================================================
# 中文名称: 质量+价值组合策略
# 简要说明: 结合 ROE（质量因子）和 PE_TTM（价值因子）对股票打分排名，
#           排名靠前的标的产生做多信号，排名靠后的产生做空信号。
#           信号值 ∈ [-1, 1]，NaN 安全，无前瞻偏差。
# 典型用途: A 股基本面量化选股，适合中长期组合配置。
# ============================================================
"""Quality + Value Multi-Factor Strategy (mf_quality_value).

Combines ROE (quality proxy) and PE_TTM (value proxy) to rank
instruments. Top-ranked instruments receive long signals; bottom-ranked
receive short signals. Rebalances every ``rebalance_days`` bars.
"""

from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd

__strategy_meta__ = {
    "id": "mf_quality_value",
    "nickname": "质量+价值组合",
    "category": "multi_factor",
    "description": (
        "Combine ROE (quality) and PE_TTM (value) to rank stocks. "
        "Top ranked instruments get long signals, bottom ranked get short signals. "
        "Rebalances periodically with configurable quality/value weights."
    ),
    "universe": ["equity_cn"],
    "frequency": ["1D"],
    "columns_required": ["close", "volume", "pe_ttm", "roe"],
    "default_params": {
        "top_n": 10,
        "rebalance_days": 20,
        "quality_weight": 0.5,
        "value_weight": 0.5,
    },
    "risk_profile": "low",
    "min_bars": 30,
    "reference": "Asness, Frazzini & Pedersen, Quality Minus Junk, 2019",
    "factors_used": ["roe", "pe_ttm"],
}


def _percentile_rank(series: pd.Series) -> pd.Series:
    """Compute cross-sectional percentile rank in [0, 1], NaN-safe."""
    valid = series.dropna()
    if len(valid) == 0:
        return pd.Series(np.nan, index=series.index)
    ranks = valid.rank(pct=True)
    return ranks.reindex(series.index)


class SignalEngine:
    """质量+价值组合信号引擎。"""

    def __init__(self, **params: Any) -> None:
        self.top_n: int = int(params.get("top_n", 10))
        self.rebalance_days: int = int(params.get("rebalance_days", 20))
        self.quality_weight: float = float(params.get("quality_weight", 0.5))
        self.value_weight: float = float(params.get("value_weight", 0.5))

        total = self.quality_weight + self.value_weight
        if total <= 0:
            raise ValueError("quality_weight + value_weight must be > 0")
        # Normalise weights to sum to 1
        self.quality_weight /= total
        self.value_weight /= total

    def generate(
        self, data_map: Dict[str, pd.DataFrame]
    ) -> Dict[str, pd.Series]:
        """为每个标的生成质量+价值综合信号。

        Logic per rebalance window:
        1. For each instrument, take the latest available ROE and PE_TTM.
        2. Quality score = cross-sectional percentile rank of ROE (higher = better).
        3. Value score  = cross-sectional percentile rank of 1/PE_TTM (lower PE = better).
        4. Composite    = quality_weight * quality_score + value_weight * value_score.
        5. Top-N → signal +1.0; bottom-N → signal -1.0; rest → 0.0.

        Returns:
            Dict mapping ticker -> pd.Series of signals in [-1.0, 1.0].
        """
        # --- collect latest factor values per bar across all instruments ---
        # Build a DataFrame: index = date, columns = code, values = factor
        all_codes = list(data_map.keys())
        if not all_codes:
            return {}

        # Use the first instrument's index as the reference date axis
        ref_index = next(iter(data_map.values())).index

        roe_panel: Dict[str, pd.Series] = {}
        pe_panel: Dict[str, pd.Series] = {}
        for code, df in data_map.items():
            if not {"close", "pe_ttm", "roe"}.issubset(df.columns):
                continue
            roe_panel[code] = df["roe"].astype(float)
            pe_panel[code] = df["pe_ttm"].astype(float)

        if not roe_panel:
            return {}

        roe_df = pd.DataFrame(roe_panel)
        pe_df = pd.DataFrame(pe_panel)

        # Composite score per bar: percentile-rank cross-sectionally
        # Quality: higher ROE is better
        quality_rank = roe_df.rank(axis=1, pct=True, na_option="keep")
        # Value: lower PE is better → rank 1/PE
        inv_pe = 1.0 / pe_df.replace(0.0, np.nan)
        value_rank = inv_pe.rank(axis=1, pct=True, na_option="keep")

        composite = (
            self.quality_weight * quality_rank + self.value_weight * value_rank
        )

        # --- generate signals with rebalance cadence ---
        n_bars = len(composite)
        n_codes = len(composite.columns)
        effective_top_n = min(self.top_n, max(1, n_codes // 3))

        signals: Dict[str, pd.Series] = {}
        for code in composite.columns:
            signals[code] = pd.Series(np.nan, index=composite.index, dtype=float)

        prev_signal: Dict[str, float] = {}

        for i in range(n_bars):
            # Only recompute on rebalance dates
            if i % self.rebalance_days != 0:
                for code in composite.columns:
                    signals[code].iloc[i] = prev_signal.get(code, np.nan)
                continue

            row = composite.iloc[i].dropna()
            if len(row) < 2:
                for code in composite.columns:
                    signals[code].iloc[i] = prev_signal.get(code, np.nan)
                continue

            sorted_codes = row.sort_values(ascending=False)
            top_codes = set(sorted_codes.index[:effective_top_n])
            bottom_codes = set(sorted_codes.index[-effective_top_n:])

            for code in composite.columns:
                if code in top_codes:
                    val = 1.0
                elif code in bottom_codes:
                    val = -1.0
                else:
                    val = 0.0
                signals[code].iloc[i] = val
                prev_signal[code] = val

        # Forward-fill within rebalance windows, then clip
        for code in signals:
            signals[code] = signals[code].ffill().clip(-1.0, 1.0)

        return signals
