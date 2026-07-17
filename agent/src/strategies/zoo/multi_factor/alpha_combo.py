
# ============================================================
# 中文名称: Alpha Zoo 组合策略
# 简要说明: 从 OHLCV 计算多个简单 alpha 因子(动量、短期反转、量价背离、
#           波动率)，等权或 IC 加权合成综合得分。按合成排名做多头部标的。
#           设计为后续对接 Factor Zoo 因子库的多因子框架。
#           信号值 ∈ [-1, 1]，NaN 安全，无前瞻偏差。
# 典型用途: 多因子 alpha 组合，适用于 A 股、美股中大盘股票池。
# ============================================================
"""Alpha Combo (mf_alpha_combo).

Combine multiple alpha signals into a composite score. Compute several
simple alpha-like factors from OHLCV: momentum (N-day return), reversal
(short-term mean reversion), volume-price divergence, and volatility
(inverse). Equal-weight the factors, rank by composite, top N long.
"""

from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd

__strategy_meta__ = {
    "id": "mf_alpha_combo",
    "nickname": "Alpha Zoo组合",
    "category": "multi_factor",
    "description": (
        "Combine multiple alpha signals into a composite score. Compute "
        "momentum, reversal, volume-price divergence, and inverse "
        "volatility from OHLCV. Equal-weight factors, rank by composite, "
        "top quintile long. Designed to integrate with Factor Zoo."
    ),
    "universe": ["equity_cn", "equity_us"],
    "frequency": ["1D"],
    "columns_required": ["close", "volume"],
    "default_params": {
        "mom_period": 20,
        "rev_period": 5,
        "vol_period": 20,
        "rebalance_days": 10,
        "top_pct": 0.2,
    },
    "risk_profile": "medium",
    "min_bars": 30,
    "reference": "联动 Factor Zoo 的多因子组合",
    "factors_used": ["alpha101_001", "alpha101_002"],
}


def _momentum_factor(close: pd.DataFrame, period: int) -> pd.DataFrame:
    """N-day return, cross-sectionally ranked."""
    ret = close / close.shift(period) - 1.0
    return ret.rank(axis=1, pct=True, na_option="keep")


def _reversal_factor(close: pd.DataFrame, period: int) -> pd.DataFrame:
    """Short-term reversal: negative of short-term return, ranked.

    Stocks that declined recently are expected to revert (rank higher).
    """
    short_ret = close / close.shift(period) - 1.0
    rev = -short_ret
    return rev.rank(axis=1, pct=True, na_option="keep")


def _volume_price_divergence(
    close: pd.DataFrame, volume: pd.DataFrame, period: int
) -> pd.DataFrame:
    """Volume-price divergence: rank of (volume change - price change).

    When volume rises but price doesn't follow, bullish divergence.
    Signal = volume_change_rank - price_change_rank (cross-sectional).
    """
    price_change = close / close.shift(period) - 1.0
    vol_change = volume.rolling(window=period, min_periods=period).mean() / (
        volume.shift(period).rolling(window=period, min_periods=period).mean().replace(0.0, np.nan)
    ) - 1.0

    price_rank = price_change.rank(axis=1, pct=True, na_option="keep")
    vol_rank = vol_change.rank(axis=1, pct=True, na_option="keep")

    # Divergence: high volume change but low price change -> bullish
    div = vol_rank - price_rank
    return div.rank(axis=1, pct=True, na_option="keep")


def _inverse_vol_factor(close: pd.DataFrame, period: int) -> pd.DataFrame:
    """Inverse volatility factor: low-vol stocks rank higher."""
    log_ret = np.log(close / close.shift(1).replace(0.0, np.nan))
    vol = log_ret.rolling(window=period, min_periods=period).std()
    inv_vol = -vol
    return inv_vol.rank(axis=1, pct=True, na_option="keep")


class SignalEngine:
    """Alpha Zoo 组合信号引擎。"""

    def __init__(self, **params: Any) -> None:
        self.mom_period: int = int(params.get("mom_period", 20))
        self.rev_period: int = int(params.get("rev_period", 5))
        self.vol_period: int = int(params.get("vol_period", 20))
        self.rebalance_days: int = int(params.get("rebalance_days", 10))
        self.top_pct: float = float(params.get("top_pct", 0.2))

        if not (0 < self.top_pct < 1):
            raise ValueError(f"top_pct ({self.top_pct}) must be in (0, 1)")

    def generate(
        self, data_map: Dict[str, pd.DataFrame]
    ) -> Dict[str, pd.Series]:
        """为横截面中的所有标的生成 alpha 组合信号。

        Computes 4 alpha factors from OHLCV, equal-weights them into a
        composite score, and ranks cross-sectionally. Top quintile
        receives long signal (+1), rest 0.

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

        # Compute individual factor ranks
        f_mom = _momentum_factor(close_panel, self.mom_period)
        f_rev = _reversal_factor(close_panel, self.rev_period)
        f_vpd = _volume_price_divergence(close_panel, volume_panel, self.mom_period)
        f_vol = _inverse_vol_factor(close_panel, self.vol_period)

        # Equal-weight composite (average of rank scores)
        composite = (f_mom + f_rev + f_vpd + f_vol) / 4.0

        # Handle partial NaN: where any factor is NaN, count only non-NaN
        factor_count = (
            f_mom.notna().astype(float)
            + f_rev.notna().astype(float)
            + f_vpd.notna().astype(float)
            + f_vol.notna().astype(float)
        )
        factor_sum = (
            f_mom.fillna(0.0) + f_rev.fillna(0.0)
            + f_vpd.fillna(0.0) + f_vol.fillna(0.0)
        )
        composite = factor_sum / factor_count.replace(0, np.nan)

        # Cross-sectional rank of composite
        composite_rank = composite.rank(axis=1, pct=True, na_option="keep")

        # Signal: top pct -> +1 (long only for alpha combo)
        top_threshold = 1.0 - self.top_pct
        signal_matrix = pd.DataFrame(0.0, index=close_panel.index, columns=close_panel.columns)
        signal_matrix = signal_matrix.where(
            ~(composite_rank >= top_threshold), other=1.0
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
