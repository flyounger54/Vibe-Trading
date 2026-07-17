
# ============================================================
# 中文名称: AH 股溢价套利策略
# 简要说明: 针对恰好 2 个标的(A 股与 H 股同公司)，计算 AH 溢价 =
#           A_price / H_price，再对溢价做滚动 Z-score。当溢价扩大
#           超过历史均值 + N*std 时做空 A 做多 H；收窄时反向。
#           通用于任意价格比率均值回归配对(如 ADR/H 股)。
#           信号值 ∈ [-1, 1]，NaN 安全，无前瞻偏差。
# 典型用途: AH 股溢价套利，港股通跨市场配对交易。
# ============================================================
"""AH Premium Arbitrage (sa_ah_premium).

For pairs of A-share and H-share of the same company. Compute AH
premium = A_price / H_price (adjusted). When premium expands beyond
historical mean + N*std, short A and long H. When it contracts, reverse.
Works on any 2 instruments as a price-ratio mean-reversion strategy.
"""

from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd

__strategy_meta__ = {
    "id": "sa_ah_premium",
    "nickname": "AH股溢价套利",
    "category": "stat_arb",
    "description": (
        "AH premium arbitrage for pairs of A-share and H-share. Compute "
        "AH premium ratio and Z-score. When premium expands beyond "
        "threshold, short A / long H; when contracts, reverse. Generic "
        "price-ratio mean-reversion for any 2 instruments."
    ),
    "universe": ["equity_cn", "equity_hk"],
    "frequency": ["1D"],
    "columns_required": ["close"],
    "default_params": {"lookback": 60, "entry_z": 1.5, "exit_z": 0.3},
    "risk_profile": "medium",
    "min_bars": 65,
    "reference": "AH股溢价套利, 已有 adr-hshare skill",
    "factors_used": [],
}


def _rolling_zscore(series: pd.Series, window: int) -> pd.Series:
    """Z-score of series over a rolling window, NaN-safe."""
    mean = series.rolling(window=window, min_periods=window).mean()
    std = series.rolling(window=window, min_periods=window).std()
    z = (series - mean) / std.replace(0.0, np.nan)
    return z


class SignalEngine:
    """AH 股溢价套利信号引擎。"""

    def __init__(self, **params: Any) -> None:
        self.lookback: int = int(params.get("lookback", 60))
        self.entry_z: float = float(params.get("entry_z", 1.5))
        self.exit_z: float = float(params.get("exit_z", 0.3))

        if self.entry_z <= self.exit_z:
            raise ValueError(
                f"entry_z ({self.entry_z}) must be > exit_z ({self.exit_z})"
            )
        if self.lookback < 1:
            raise ValueError(f"lookback ({self.lookback}) must be >= 1")

    def generate(
        self, data_map: Dict[str, pd.DataFrame]
    ) -> Dict[str, pd.Series]:
        """为 AH 配对标的生成溢价套利信号。

        Requires exactly 2 instruments. The first is treated as the
        A-share (or higher-premium leg), the second as the H-share
        (or lower-premium leg).

        Signal logic per bar:
        - Premium = close_A / close_H
        - Z = rolling z-score of premium over lookback window
        - Z > entry_z  → premium too high → short A (-1), long H (+1)
        - Z < -entry_z → premium too low  → long A (+1), short H (-1)
        - |Z| < exit_z → flatten both legs

        Returns:
            Dict mapping ticker -> pd.Series of signals in [-1.0, 1.0].
            A-share signal is the primary; H-share signal is its negative.
        """
        codes = list(data_map.keys())

        if len(codes) != 2:
            # Graceful fallback: return zero signals for all instruments
            signals: Dict[str, pd.Series] = {}
            for code, df in data_map.items():
                signals[code] = pd.Series(0.0, index=df.index, dtype=float)
            return signals

        code_a, code_h = codes[0], codes[1]
        df_a, df_h = data_map[code_a], data_map[code_h]

        if "close" not in df_a.columns or "close" not in df_h.columns:
            return {
                code_a: pd.Series(0.0, index=df_a.index, dtype=float),
                code_h: pd.Series(0.0, index=df_h.index, dtype=float),
            }

        # Extract and align close prices
        close_a = df_a["close"].astype(float)
        close_h = df_h["close"].astype(float)
        common_idx = close_a.index.intersection(close_h.index)

        if len(common_idx) < self.lookback:
            return {
                code_a: pd.Series(0.0, index=df_a.index, dtype=float),
                code_h: pd.Series(0.0, index=df_h.index, dtype=float),
            }

        a = close_a.reindex(common_idx)
        h = close_h.reindex(common_idx)

        # AH premium ratio (guard against zero/negative H price)
        premium = a / h.clip(lower=1e-10)

        # Z-score of premium
        z = _rolling_zscore(premium, self.lookback)

        # State-machine signal generation
        signal_a = pd.Series(np.nan, index=common_idx, dtype=float)
        position = 0.0  # current position for A-share leg

        for i in range(len(common_idx)):
            z_val = z.iloc[i]

            if np.isnan(z_val):
                signal_a.iloc[i] = np.nan
                continue

            if position == 0.0:
                # Not in trade: check for entry
                if z_val > self.entry_z:
                    # Premium too high: short A, long H
                    position = -1.0
                elif z_val < -self.entry_z:
                    # Premium too low: long A, short H
                    position = 1.0
            else:
                # In trade: check for exit
                if abs(z_val) < self.exit_z:
                    position = 0.0
                # Check for reversal
                elif position < 0 and z_val < -self.entry_z:
                    position = 1.0
                elif position > 0 and z_val > self.entry_z:
                    position = -1.0

            signal_a.iloc[i] = position

        # H-share signal is opposite (pair trade: market-neutral)
        signal_h = -signal_a

        # Reindex to original instrument indices
        return {
            code_a: signal_a.reindex(df_a.index).clip(-1.0, 1.0),
            code_h: signal_h.reindex(df_h.index).clip(-1.0, 1.0),
        }
