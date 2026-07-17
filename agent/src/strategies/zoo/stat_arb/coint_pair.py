
# ============================================================
# 中文名称: 协整配对交易策略
# 简要说明: 针对恰好 2 个标的，计算对数价差、滚动 OLS 对冲比率、
#           价差 Z-score。|Z| > entry_z 开仓，Z 穿越 exit_z 平仓。
#           信号值 ∈ [-1, 1]，NaN 安全，无前瞻偏差。
# 典型用途: A 股 / 美股配对交易，市场中性套利。
# ============================================================
"""Cointegration Pair Trading (sa_coint_pair).

For exactly 2 instruments: compute log price spread, fit rolling OLS
hedge ratio, compute Z-score of spread. Entry when |Z| > threshold,
exit when |Z| < exit threshold.
"""

from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd

__strategy_meta__ = {
    "id": "sa_coint_pair",
    "nickname": "协整配对交易",
    "category": "stat_arb",
    "description": (
        "For exactly 2 instruments: compute log price spread, fit rolling "
        "OLS hedge ratio, compute Z-score of spread. Entry when |Z| > "
        "threshold, exit when Z crosses exit zone."
    ),
    "universe": ["equity_cn", "equity_us"],
    "frequency": ["1D"],
    "columns_required": ["close"],
    "default_params": {"lookback": 60, "entry_z": 2.0, "exit_z": 0.5},
    "risk_profile": "medium",
    "min_bars": 120,
    "reference": "Engle & Granger, Co-Integration and Error Correction, 1987",
    "factors_used": [],
}


def _rolling_ols_beta(y: pd.Series, x: pd.Series, window: int) -> pd.Series:
    """Rolling OLS slope coefficient (hedge ratio), pure pandas.

    beta_t = cov(y, x)_t / var(x)_t  over a rolling window.
    """
    xy_cov = y.rolling(window=window, min_periods=window).cov(x)
    x_var = x.rolling(window=window, min_periods=window).var()
    beta = xy_cov / x_var.replace(0.0, np.nan)
    return beta


def _rolling_zscore(spread: pd.Series, window: int) -> pd.Series:
    """Z-score of spread over a rolling window, NaN-safe."""
    mean = spread.rolling(window=window, min_periods=window).mean()
    std = spread.rolling(window=window, min_periods=window).std()
    z = (spread - mean) / std.replace(0.0, np.nan)
    return z


class SignalEngine:
    """协整配对交易信号引擎。"""

    def __init__(self, **params: Any) -> None:
        self.lookback: int = int(params.get("lookback", 60))
        self.entry_z: float = float(params.get("entry_z", 2.0))
        self.exit_z: float = float(params.get("exit_z", 0.5))

        if self.entry_z <= self.exit_z:
            raise ValueError(
                f"entry_z ({self.entry_z}) must be > exit_z ({self.exit_z})"
            )

    def generate(
        self, data_map: Dict[str, pd.DataFrame]
    ) -> Dict[str, pd.Series]:
        """为配对标的生成协整交易信号。

        Requires exactly 2 instruments in data_map. The first is treated as
        the dependent variable (Y), the second as the independent (X).

        Signal logic per bar:
        - Spread = log(Y) - beta * log(X)
        - Z = rolling z-score of spread
        - Z < -entry_z  →  long Y / short X  (spread mean-reverts up)
        - Z >  entry_z  →  short Y / long X  (spread mean-reverts down)
        - |Z| < exit_z  →  flatten

        Returns:
            Dict mapping ticker -> pd.Series of signals in [-1.0, 1.0].
            Y instrument signal is the primary; X signal is its negative.
        """
        codes = list(data_map.keys())

        if len(codes) != 2:
            # Graceful fallback: return zero signals for all instruments
            signals: Dict[str, pd.Series] = {}
            for code, df in data_map.items():
                signals[code] = pd.Series(0.0, index=df.index, dtype=float)
            return signals

        code_y, code_x = codes[0], codes[1]
        df_y, df_x = data_map[code_y], data_map[code_x]

        if "close" not in df_y.columns or "close" not in df_x.columns:
            return {
                code_y: pd.Series(0.0, index=df_y.index, dtype=float),
                code_x: pd.Series(0.0, index=df_x.index, dtype=float),
            }

        # Align on common index
        close_y = df_y["close"].astype(float)
        close_x = df_x["close"].astype(float)
        common_idx = close_y.index.intersection(close_x.index)

        if len(common_idx) < self.lookback:
            return {
                code_y: pd.Series(0.0, index=df_y.index, dtype=float),
                code_x: pd.Series(0.0, index=df_x.index, dtype=float),
            }

        y = close_y.reindex(common_idx)
        x = close_x.reindex(common_idx)

        # Log prices (guard against non-positive)
        log_y = np.log(y.clip(lower=1e-10))
        log_x = np.log(x.clip(lower=1e-10))

        # Rolling hedge ratio
        beta = _rolling_ols_beta(log_y, log_x, self.lookback)

        # Spread and Z-score
        spread = log_y - beta * log_x
        z = _rolling_zscore(spread, self.lookback)

        # Generate position signal with state machine
        signal_y = pd.Series(np.nan, index=common_idx, dtype=float)
        position = 0.0  # current position state

        for i in range(len(common_idx)):
            z_val = z.iloc[i]

            if np.isnan(z_val):
                signal_y.iloc[i] = np.nan
                continue

            if position == 0.0:
                # Not in a trade: check for entry
                if z_val < -self.entry_z:
                    position = 1.0   # long Y / short X
                elif z_val > self.entry_z:
                    position = -1.0  # short Y / long X
            else:
                # In a trade: check for exit
                if abs(z_val) < self.exit_z:
                    position = 0.0
                # Also check for reversal
                elif position > 0 and z_val > self.entry_z:
                    position = -1.0
                elif position < 0 and z_val < -self.entry_z:
                    position = 1.0

            signal_y.iloc[i] = position

        # X signal is opposite of Y signal (pair trade)
        signal_x = -signal_y

        # Reindex back to original instrument indices
        return {
            code_y: signal_y.reindex(df_y.index).clip(-1.0, 1.0),
            code_x: signal_x.reindex(df_x.index).clip(-1.0, 1.0),
        }
