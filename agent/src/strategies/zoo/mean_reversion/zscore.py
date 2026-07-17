
# ============================================================
# 中文名称: Z-Score均值回归策略
# 简要说明: 计算收盘价相对滚动均值/标准差的Z-Score。Z > entry_z 做空，
#           Z < -entry_z 做多。|Z| < exit_z 时平仓(信号归零)。
#           信号强度与 |Z|/entry_z 成正比，上限 [-1, 1]。NaN 安全。
# 典型用途: 统计套利中的均值回归交易，适用于A股与美股。
# ============================================================
"""Z-Score Mean Reversion (mr_zscore).

Computes the Z-score of the close price relative to its rolling mean
and standard deviation.  Short when Z exceeds entry_z, long when Z
drops below -entry_z.  Signal flattens to zero when |Z| returns
within exit_z.  Signal magnitude is proportional to |Z|/entry_z,
capped at 1.0.
"""

from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd

__strategy_meta__ = {
    "id": "mr_zscore",
    "nickname": "Z-Score均值回归",
    "category": "mean_reversion",
    "description": (
        "Z-Score mean reversion. Computes rolling Z-score of price. "
        "Long when Z < -entry_z, short when Z > entry_z, flat when "
        "|Z| < exit_z. Signal strength proportional to |Z|/entry_z, "
        "capped at 1.0."
    ),
    "universe": ["equity_cn", "equity_us"],
    "frequency": ["1D"],
    "columns_required": ["close"],
    "default_params": {"lookback": 60, "entry_z": 2.0, "exit_z": 0.5},
    "risk_profile": "medium",
    "min_bars": 65,
    "reference": "Avellaneda & Lee, Statistical Arbitrage in the US Equities Market, 2010",
    "factors_used": [],
}


class SignalEngine:
    """Z-Score均值回归信号引擎。"""

    def __init__(self, **params: Any) -> None:
        self.lookback: int = int(params.get("lookback", 60))
        self.entry_z: float = float(params.get("entry_z", 2.0))
        self.exit_z: float = float(params.get("exit_z", 0.5))
        if self.lookback < 2:
            raise ValueError(f"lookback ({self.lookback}) must be >= 2")
        if self.entry_z <= 0:
            raise ValueError(f"entry_z ({self.entry_z}) must be > 0")
        if self.exit_z < 0:
            raise ValueError(f"exit_z ({self.exit_z}) must be >= 0")
        if self.exit_z >= self.entry_z:
            raise ValueError(
                f"exit_z ({self.exit_z}) must be < entry_z ({self.entry_z})"
            )

    def generate(
        self, data_map: Dict[str, pd.DataFrame]
    ) -> Dict[str, pd.Series]:
        """为每个标的生成Z-Score均值回归信号。

        Returns:
            Dict mapping ticker -> pd.Series of signals in [-1.0, 1.0].
        """
        signals: Dict[str, pd.Series] = {}

        for code, df in data_map.items():
            if "close" not in df.columns:
                continue
            signals[code] = self._generate_one(df)

        return signals

    def _generate_one(self, df: pd.DataFrame) -> pd.Series:
        """对单个标的生成Z-Score均值回归信号。"""
        close: pd.Series = df["close"].astype(float)

        roll_mean = close.rolling(window=self.lookback, min_periods=self.lookback).mean()
        roll_std = close.rolling(window=self.lookback, min_periods=self.lookback).std()

        # Z-score: how many std devs away from rolling mean
        safe_std = roll_std.where(roll_std > 0, other=np.nan)
        zscore = (close - roll_mean) / safe_std

        n = len(close)
        z_arr = zscore.values
        signal_arr = np.full(n, np.nan)

        # State machine: track whether we are in a position
        # 0 = flat, 1 = long, -1 = short
        position = 0.0

        for i in range(n):
            z = z_arr[i]
            if np.isnan(z):
                signal_arr[i] = np.nan
                continue

            abs_z = abs(z)

            # Entry / exit logic
            if position == 0.0:
                # Flat: check for entry
                if z > self.entry_z:
                    position = -1.0  # short
                elif z < -self.entry_z:
                    position = 1.0  # long
                # else: stay flat
            elif position == 1.0:
                # Long: check for exit or reversal
                if abs_z < self.exit_z:
                    position = 0.0  # exit
                elif z > self.entry_z:
                    position = -1.0  # reverse to short
            elif position == -1.0:
                # Short: check for exit or reversal
                if abs_z < self.exit_z:
                    position = 0.0  # exit
                elif z < -self.entry_z:
                    position = 1.0  # reverse to long

            # Signal strength proportional to |Z|/entry_z, capped at 1.0
            if position == 0.0:
                signal_arr[i] = 0.0
            else:
                strength = min(abs_z / self.entry_z, 1.0)
                signal_arr[i] = position * strength

        signal = pd.Series(signal_arr, index=close.index)

        # Clip for safety
        signal = signal.clip(lower=-1.0, upper=1.0)

        return signal
