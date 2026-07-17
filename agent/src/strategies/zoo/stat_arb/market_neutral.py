
# ============================================================
# 中文名称: 市场中性多空策略
# 简要说明: 美元中性多空策略。按合成得分(动量+反转+成交量)对所有标的排名，
#           做多头部五分之一、做空尾部五分之一，权重合计为零(市场中性)。
#           信号值 ∈ [-1, 1]，NaN 安全，无前瞻偏差。
# 典型用途: 经典统计套利框架，适用于 A 股、美股中大盘股票池。
# ============================================================
"""Market Neutral Long-Short (sa_market_neutral).

Dollar-neutral long-short: rank all instruments by composite score
(momentum + reversal + volume), go long top quintile, short bottom
quintile, with weights summing to zero (market-neutral).
"""

from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd

__strategy_meta__ = {
    "id": "sa_market_neutral",
    "nickname": "市场中性多空",
    "category": "stat_arb",
    "description": (
        "Dollar-neutral long-short: rank all instruments by composite "
        "score (momentum + reversal + volume). Long top quintile, short "
        "bottom quintile, weights sum to zero (market-neutral)."
    ),
    "universe": ["equity_cn", "equity_us"],
    "frequency": ["1D"],
    "columns_required": ["close", "volume"],
    "default_params": {
        "lookback": 20,
        "top_pct": 0.2,
        "bottom_pct": 0.2,
        "rebalance_days": 5,
    },
    "risk_profile": "low",
    "min_bars": 30,
    "reference": "Khandani & Lo, What Happened to the Quants in August 2007, 2007",
    "factors_used": [],
}


def _momentum_score(close: pd.DataFrame, lookback: int) -> pd.DataFrame:
    """Trailing return over lookback period, ranked cross-sectionally."""
    ret = close / close.shift(lookback) - 1.0
    return ret.rank(axis=1, pct=True, na_option="keep")


def _reversal_score(close: pd.DataFrame) -> pd.DataFrame:
    """5-day short-term reversal, ranked cross-sectionally.

    Negative of 5-day return: recent losers rank higher (reversal).
    """
    short_ret = close / close.shift(5) - 1.0
    rev = -short_ret
    return rev.rank(axis=1, pct=True, na_option="keep")


def _volume_score(volume: pd.DataFrame, lookback: int) -> pd.DataFrame:
    """Volume trend: ratio of recent average volume to longer average.

    High recent volume relative to historical → higher score.
    """
    short_window = max(5, lookback // 4)
    vol_short = volume.rolling(window=short_window, min_periods=short_window).mean()
    vol_long = volume.rolling(window=lookback, min_periods=lookback).mean()
    vol_ratio = vol_short / vol_long.replace(0.0, np.nan)
    return vol_ratio.rank(axis=1, pct=True, na_option="keep")


class SignalEngine:
    """市场中性多空信号引擎。"""

    def __init__(self, **params: Any) -> None:
        self.lookback: int = int(params.get("lookback", 20))
        self.top_pct: float = float(params.get("top_pct", 0.2))
        self.bottom_pct: float = float(params.get("bottom_pct", 0.2))
        self.rebalance_days: int = int(params.get("rebalance_days", 5))

        if self.lookback < 1:
            raise ValueError(f"lookback ({self.lookback}) must be >= 1")
        if not (0 < self.top_pct < 0.5):
            raise ValueError(f"top_pct ({self.top_pct}) must be in (0, 0.5)")
        if not (0 < self.bottom_pct < 0.5):
            raise ValueError(f"bottom_pct ({self.bottom_pct}) must be in (0, 0.5)")

    def generate(
        self, data_map: Dict[str, pd.DataFrame]
    ) -> Dict[str, pd.Series]:
        """为横截面中的所有标的生成市场中性多空信号。

        Composite = equal-weight(momentum_rank + reversal_rank +
        volume_rank). Top quintile → +1 (long), bottom quintile → -1
        (short), middle → 0. The long and short sides are symmetric so
        net exposure is approximately zero.

        Returns:
            Dict mapping ticker -> pd.Series of signals in [-1.0, 1.0].
        """
        close_dict: Dict[str, pd.Series] = {}
        volume_dict: Dict[str, pd.Series] = {}

        for code, df in data_map.items():
            if "close" not in df.columns:
                continue
            close_dict[code] = df["close"].astype(float)
            if "volume" in df.columns:
                volume_dict[code] = df["volume"].astype(float)
            else:
                volume_dict[code] = pd.Series(np.nan, index=df.index)

        if len(close_dict) < 5:
            return {
                code: pd.Series(0.0, index=s.index)
                for code, s in close_dict.items()
            }

        close_panel = pd.DataFrame(close_dict)
        volume_panel = pd.DataFrame(volume_dict)

        # Compute sub-scores
        f_mom = _momentum_score(close_panel, self.lookback)
        f_rev = _reversal_score(close_panel)
        f_vol = _volume_score(volume_panel, self.lookback)

        # Composite: average of available factor ranks (NaN-safe)
        factor_count = (
            f_mom.notna().astype(float)
            + f_rev.notna().astype(float)
            + f_vol.notna().astype(float)
        )
        factor_sum = f_mom.fillna(0.0) + f_rev.fillna(0.0) + f_vol.fillna(0.0)
        composite = factor_sum / factor_count.replace(0, np.nan)

        # Cross-sectional rank of composite
        composite_rank = composite.rank(axis=1, pct=True, na_option="keep")

        # Signal: top → +1 (long), bottom → -1 (short), middle → 0
        top_threshold = 1.0 - self.top_pct
        bottom_threshold = self.bottom_pct

        signal_matrix = pd.DataFrame(
            0.0, index=close_panel.index, columns=close_panel.columns
        )
        signal_matrix = signal_matrix.where(
            ~(composite_rank >= top_threshold), other=1.0
        )
        signal_matrix = signal_matrix.where(
            ~(composite_rank <= bottom_threshold), other=-1.0
        )

        # Apply rebalance hold period
        if self.rebalance_days > 1:
            signal_matrix = self._apply_rebalance(signal_matrix, composite)

        # NaN where composite is not yet computable
        signal_matrix = signal_matrix.where(composite.notna(), other=np.nan)

        # Map back to original instrument indices
        signals: Dict[str, pd.Series] = {}
        for code in close_dict:
            if code in signal_matrix.columns:
                sig = signal_matrix[code].reindex(data_map[code].index)
                signals[code] = sig.clip(-1.0, 1.0)
            else:
                signals[code] = pd.Series(0.0, index=data_map[code].index)

        return signals

    def _apply_rebalance(
        self, raw_signals: pd.DataFrame, reference: pd.DataFrame
    ) -> pd.DataFrame:
        """Hold signals constant between rebalance dates."""
        result = raw_signals.copy()
        valid_rows = reference.dropna(how="all").index
        if len(valid_rows) == 0:
            return result

        first_valid_loc = raw_signals.index.get_loc(valid_rows[0])
        total_rows = len(raw_signals)
        rebalance_locs = set(range(first_valid_loc, total_rows, self.rebalance_days))

        last_signal = None
        for i in range(first_valid_loc, total_rows):
            if i in rebalance_locs:
                last_signal = raw_signals.iloc[i]
            elif last_signal is not None:
                result.iloc[i] = last_signal

        return result
