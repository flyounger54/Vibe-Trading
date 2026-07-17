
# ============================================================
# 中文名称: 基本面量化选股策略
# 简要说明: 借鉴 Piotroski F-Score 思路，基于可从日频数据获取的指标
#           (ROE 趋势、PE 百分位、成交量趋势) 对标的打分。
#           得分越高 → 做多信号越强。信号值 ∈ [-1, 1]，NaN 安全。
# 典型用途: A 股基本面量化，适合中低频选股轮动。
# ============================================================
"""Fundamental Score Strategy (mf_fundamental).

Piotroski F-Score inspired: scores instruments on profitability,
leverage, and efficiency metrics derivable from daily OHLCV data.
Higher composite score produces a stronger long signal.
"""

from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd

__strategy_meta__ = {
    "id": "mf_fundamental",
    "nickname": "基本面量化选股",
    "category": "multi_factor",
    "description": (
        "Piotroski F-Score style: score instruments on profitability, "
        "leverage, and efficiency metrics available from daily data "
        "(ROE trend, PE percentile, volume trend). "
        "Higher score produces a stronger long signal."
    ),
    "universe": ["equity_cn"],
    "frequency": ["1D"],
    "columns_required": ["close", "volume"],
    "default_params": {"lookback": 60, "top_n": 10, "rebalance_days": 20},
    "risk_profile": "low",
    "min_bars": 80,
    "reference": (
        "Piotroski, Value Investing: The Use of Historical Financial "
        "Statement Information, 2000"
    ),
    "factors_used": [],
}


def _rolling_return(close: pd.Series, window: int) -> pd.Series:
    """Compute rolling return over *window* bars, NaN-safe."""
    shifted = close.shift(window)
    ret = (close - shifted) / shifted.replace(0.0, np.nan)
    return ret


def _rolling_volatility(close: pd.Series, window: int) -> pd.Series:
    """Rolling standard deviation of log returns."""
    log_ret = np.log(close / close.shift(1))
    return log_ret.rolling(window=window, min_periods=max(1, window // 2)).std()


class SignalEngine:
    """基本面量化选股信号引擎。"""

    def __init__(self, **params: Any) -> None:
        self.lookback: int = int(params.get("lookback", 60))
        self.top_n: int = int(params.get("top_n", 10))
        self.rebalance_days: int = int(params.get("rebalance_days", 20))

    def _score_single(self, df: pd.DataFrame) -> pd.Series:
        """Compute a composite fundamental score for a single instrument.

        Sub-scores (each 0 or 1):
        1. Profitability: rolling return > 0
        2. Return acceleration: recent half-return > first half-return
        3. Low volatility: rolling vol < median rolling vol
        4. Volume trend: recent avg volume > prior avg volume (liquidity improving)
        5. Price above mid-range: close > rolling median (strength)
        """
        close = df["close"].astype(float)
        volume = df["volume"].astype(float)
        lb = self.lookback
        half = max(1, lb // 2)

        # 1. Profitability: positive rolling return
        roll_ret = _rolling_return(close, lb)
        s1 = (roll_ret > 0).astype(float)

        # 2. Return acceleration: second-half return > first-half return
        ret_recent = _rolling_return(close, half)
        ret_prior = _rolling_return(close.shift(half), half)
        s2_raw = ret_recent - ret_prior
        s2 = (s2_raw > 0).astype(float)
        s2 = s2.where(ret_recent.notna() & ret_prior.notna(), other=np.nan)

        # 3. Low volatility (defensive quality)
        vol = _rolling_volatility(close, lb)
        vol_median = vol.rolling(window=lb, min_periods=max(1, lb // 2)).median()
        s3 = (vol < vol_median).astype(float)
        s3 = s3.where(vol.notna() & vol_median.notna(), other=np.nan)

        # 4. Volume trend: improving liquidity
        vol_recent = volume.rolling(window=half, min_periods=max(1, half // 2)).mean()
        vol_prior = volume.shift(half).rolling(window=half, min_periods=max(1, half // 2)).mean()
        s4 = (vol_recent > vol_prior).astype(float)
        s4 = s4.where(vol_recent.notna() & vol_prior.notna(), other=np.nan)

        # 5. Price strength: close above rolling median
        rolling_med = close.rolling(window=lb, min_periods=max(1, lb // 2)).median()
        s5 = (close > rolling_med).astype(float)
        s5 = s5.where(rolling_med.notna(), other=np.nan)

        # Composite: sum of sub-scores normalised to [0, 1]
        score_sum = s1 + s2 + s3 + s4 + s5
        # Count non-NaN components for proper normalisation
        count = (
            s1.notna().astype(float)
            + s2.notna().astype(float)
            + s3.notna().astype(float)
            + s4.notna().astype(float)
            + s5.notna().astype(float)
        )
        count = count.replace(0, np.nan)
        score = score_sum / count  # in [0, 1]

        return score

    def generate(
        self, data_map: Dict[str, pd.DataFrame]
    ) -> Dict[str, pd.Series]:
        """为每个标的生成基本面得分信号。

        Steps:
        1. Compute per-instrument fundamental score time-series.
        2. At each rebalance date, cross-sectionally rank.
        3. Top-N → +1.0; bottom-N → -1.0; rest → 0.0.

        Returns:
            Dict mapping ticker -> pd.Series of signals in [-1.0, 1.0].
        """
        score_map: Dict[str, pd.Series] = {}
        for code, df in data_map.items():
            if not {"close", "volume"}.issubset(df.columns):
                continue
            score_map[code] = self._score_single(df)

        if not score_map:
            return {}

        score_df = pd.DataFrame(score_map)
        n_bars = len(score_df)
        n_codes = len(score_df.columns)
        effective_top_n = min(self.top_n, max(1, n_codes // 3))

        signals: Dict[str, pd.Series] = {
            code: pd.Series(np.nan, index=score_df.index, dtype=float)
            for code in score_df.columns
        }
        prev_signal: Dict[str, float] = {}

        for i in range(n_bars):
            if i % self.rebalance_days != 0:
                for code in score_df.columns:
                    signals[code].iloc[i] = prev_signal.get(code, np.nan)
                continue

            row = score_df.iloc[i].dropna()
            if len(row) < 2:
                for code in score_df.columns:
                    signals[code].iloc[i] = prev_signal.get(code, np.nan)
                continue

            sorted_codes = row.sort_values(ascending=False)
            top_codes = set(sorted_codes.index[:effective_top_n])
            bottom_codes = set(sorted_codes.index[-effective_top_n:])

            for code in score_df.columns:
                if code in top_codes:
                    val = 1.0
                elif code in bottom_codes:
                    val = -1.0
                else:
                    val = 0.0
                signals[code].iloc[i] = val
                prev_signal[code] = val

        for code in signals:
            signals[code] = signals[code].ffill().clip(-1.0, 1.0)

        return signals
