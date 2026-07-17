
# ============================================================
# 中文名称: 因子轮动策略
# 简要说明: 在动量、价值、波动率三个因子之间轮动，根据近期因子表现
#           (尾部 IC，即因子得分与前瞻收益的秩相关) 动态加权。
#           倾向 IC 最优的因子，合成得分排名后做多头部、做空尾部。
#           信号值 ∈ [-1, 1]，NaN 安全，无前瞻偏差。
# 典型用途: 多因子动态配置，适用于 A 股、美股中大盘股票池。
# ============================================================
"""Factor Rotation (mf_factor_rotation).

Rotate between momentum, value, and volatility factor exposures based
on recent factor performance. Compute trailing IC (rank correlation
between factor score and forward return) for each factor. Tilt
portfolio toward factors with best recent IC. Final composite score
ranks instruments: top N long, bottom N short.
"""

from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd

__strategy_meta__ = {
    "id": "mf_factor_rotation",
    "nickname": "因子轮动",
    "category": "multi_factor",
    "description": (
        "Rotate between momentum, value, and volatility factor exposures "
        "based on recent factor performance. Compute trailing IC (rank "
        "correlation) for each factor. Tilt portfolio toward factors with "
        "best recent IC. Top quintile long, bottom quintile short."
    ),
    "universe": ["equity_cn", "equity_us"],
    "frequency": ["1D"],
    "columns_required": ["close", "volume"],
    "default_params": {
        "lookback": 60,
        "ic_lookback": 20,
        "rebalance_days": 20,
        "top_pct": 0.2,
    },
    "risk_profile": "medium",
    "min_bars": 100,
    "reference": "Arnott, Beck & Kalesnik, Timing Smart Beta Strategies, 2016",
    "factors_used": [],
}


def _rank_cross_section(series: pd.Series) -> pd.Series:
    """Rank a series cross-sectionally, returning pct ranks in [0, 1]."""
    return series.rank(pct=True, na_option="keep")


def _compute_momentum_score(close_panel: pd.DataFrame, lookback: int) -> pd.DataFrame:
    """Trailing return over lookback period, ranked cross-sectionally."""
    ret = close_panel / close_panel.shift(lookback) - 1.0
    return ret.rank(axis=1, pct=True, na_option="keep")


def _compute_value_score(close_panel: pd.DataFrame, lookback: int) -> pd.DataFrame:
    """Inverse price-to-MA ratio as a value proxy, ranked cross-sectionally.

    Value = MA(close, lookback) / close. High ratio means the stock is
    cheap relative to its own moving average (a simple value proxy when
    PE data is unavailable).
    """
    ma = close_panel.rolling(window=lookback, min_periods=lookback).mean()
    value_raw = ma / close_panel.replace(0.0, np.nan)
    return value_raw.rank(axis=1, pct=True, na_option="keep")


def _compute_volatility_score(
    close_panel: pd.DataFrame, lookback: int
) -> pd.DataFrame:
    """Inverse realized volatility rank (low-vol preferred).

    Low volatility stocks get higher rank (inverse vol factor).
    """
    log_ret = np.log(close_panel / close_panel.shift(1).replace(0.0, np.nan))
    vol = log_ret.rolling(window=lookback, min_periods=lookback).std()
    # Inverse: negate so that low-vol gets high rank
    inv_vol = -vol
    return inv_vol.rank(axis=1, pct=True, na_option="keep")


def _compute_trailing_ic(
    factor_ranks: pd.DataFrame,
    forward_return_ranks: pd.DataFrame,
    ic_lookback: int,
) -> pd.Series:
    """Compute trailing IC as rolling mean of per-bar rank correlation.

    IC_t = spearman_corr(factor_rank_t, forward_return_rank_t) over the
    last ic_lookback bars. Returns a Series indexed by time.

    Uses shifted forward returns so that at time t we only use factor
    scores from t - ic_lookback - 1 to t - 1 correlated with returns
    realised from t - ic_lookback to t (no lookahead).
    """
    # Per-bar cross-sectional rank correlation
    n_cols = factor_ranks.shape[1]
    if n_cols < 3:
        return pd.Series(0.0, index=factor_ranks.index)

    ic_series = pd.Series(np.nan, index=factor_ranks.index)
    for i in range(len(factor_ranks)):
        f_row = factor_ranks.iloc[i]
        r_row = forward_return_ranks.iloc[i]
        mask = f_row.notna() & r_row.notna()
        if mask.sum() >= 3:
            ic_series.iloc[i] = f_row[mask].corr(r_row[mask])

    # Rolling mean of IC over ic_lookback bars
    trailing_ic = ic_series.rolling(window=ic_lookback, min_periods=1).mean()
    return trailing_ic.fillna(0.0)


class SignalEngine:
    """因子轮动信号引擎。"""

    def __init__(self, **params: Any) -> None:
        self.lookback: int = int(params.get("lookback", 60))
        self.ic_lookback: int = int(params.get("ic_lookback", 20))
        self.rebalance_days: int = int(params.get("rebalance_days", 20))
        self.top_pct: float = float(params.get("top_pct", 0.2))

        if self.lookback < 1:
            raise ValueError(f"lookback ({self.lookback}) must be >= 1")
        if self.ic_lookback < 1:
            raise ValueError(f"ic_lookback ({self.ic_lookback}) must be >= 1")
        if not (0 < self.top_pct < 0.5):
            raise ValueError(f"top_pct ({self.top_pct}) must be in (0, 0.5)")

    def generate(
        self, data_map: Dict[str, pd.DataFrame]
    ) -> Dict[str, pd.Series]:
        """为横截面中的所有标的生成因子轮动信号。

        For each instrument compute 3 sub-scores (momentum, value,
        volatility). Weight sub-scores by their trailing IC. Final
        composite score ranks instruments: top pct long (+1), bottom
        pct short (-1).

        Returns:
            Dict mapping ticker -> pd.Series of signals in [-1.0, 1.0].
        """
        close_dict: Dict[str, pd.Series] = {}
        for code, df in data_map.items():
            if "close" not in df.columns:
                continue
            close_dict[code] = df["close"].astype(float)

        if len(close_dict) < 5:
            return {
                code: pd.Series(0.0, index=s.index)
                for code, s in close_dict.items()
            }

        close_panel = pd.DataFrame(close_dict)

        # Compute factor scores (cross-sectional ranks at each time step)
        mom_scores = _compute_momentum_score(close_panel, self.lookback)
        val_scores = _compute_value_score(close_panel, self.lookback)
        vol_scores = _compute_volatility_score(close_panel, self.lookback)

        # Shift factor scores forward by 1 bar for IC alignment:
        # IC_t uses factor from t-1 and return from t-1 to t
        mom_shifted = mom_scores.shift(1)
        val_shifted = val_scores.shift(1)
        vol_shifted = vol_scores.shift(1)

        # Realised return rank for IC (use current bar return, not forward)
        realised_ret = close_panel / close_panel.shift(1) - 1.0
        realised_ret_rank = realised_ret.rank(axis=1, pct=True, na_option="keep")

        # Trailing IC for each factor (no lookahead: uses lagged factors vs
        # realised returns)
        ic_mom = _compute_trailing_ic(mom_shifted, realised_ret_rank, self.ic_lookback)
        ic_val = _compute_trailing_ic(val_shifted, realised_ret_rank, self.ic_lookback)
        ic_vol = _compute_trailing_ic(vol_shifted, realised_ret_rank, self.ic_lookback)

        # Stack ICs and normalise to weights (softmax-like: abs proportional)
        ic_stack = pd.DataFrame(
            {"mom": ic_mom, "val": ic_val, "vol": ic_vol},
            index=close_panel.index,
        )
        # Use absolute IC as weight; clip tiny negatives to zero
        ic_abs = ic_stack.clip(lower=0.0)
        ic_sum = ic_abs.sum(axis=1).replace(0.0, np.nan)
        w_mom = ic_abs["mom"] / ic_sum
        w_val = ic_abs["val"] / ic_sum
        w_vol = ic_abs["vol"] / ic_sum

        # Fallback: equal weight when all ICs are non-positive
        w_mom = w_mom.fillna(1.0 / 3)
        w_val = w_val.fillna(1.0 / 3)
        w_vol = w_vol.fillna(1.0 / 3)

        # Composite score: IC-weighted sum of factor ranks
        composite = pd.DataFrame(0.0, index=close_panel.index, columns=close_panel.columns)
        for col in close_panel.columns:
            composite[col] = (
                w_mom * mom_scores[col]
                + w_val * val_scores[col]
                + w_vol * vol_scores[col]
            )

        # Cross-sectional rank of composite
        composite_rank = composite.rank(axis=1, pct=True, na_option="keep")

        # Signal assignment
        top_threshold = 1.0 - self.top_pct
        bottom_threshold = self.top_pct

        signal_matrix = pd.DataFrame(0.0, index=close_panel.index, columns=close_panel.columns)
        signal_matrix = signal_matrix.where(
            ~(composite_rank >= top_threshold), other=1.0
        )
        signal_matrix = signal_matrix.where(
            ~(composite_rank <= bottom_threshold), other=-1.0
        )

        # Apply rebalance period (hold signals between rebalance dates)
        if self.rebalance_days > 1:
            signal_matrix = self._apply_rebalance(signal_matrix, mom_scores)

        # NaN where factor scores are not yet available
        signal_matrix = signal_matrix.where(mom_scores.notna(), other=np.nan)

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
