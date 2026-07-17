
# ============================================================
# 中文名称: ETF折溢价套利策略
# 简要说明: 使用 ETF 价格与滚动均线的偏差作为折溢价代理。计算
#           (price - MA) / rolling_std 的 Z-score，信号 = -Z（卖溢价、
#           买折价），在 entry_z 和 exit_z 之间控制仓位。
#           信号值 ∈ [-1, 1]，NaN 安全，无前瞻偏差。
# 典型用途: A 股 ETF 套利，利用 ETF 相对 NAV 的折溢价均值回归。
# ============================================================
"""ETF Premium/Discount Arbitrage (sa_etf_arb).

ETF vs underlying NAV arbitrage proxy. Uses the ETF price deviation
from its rolling MA as a proxy for premium/discount. Z-score of
(price - MA) / rolling_std -> signal = -Z (sell premium, buy discount),
with entry/exit thresholds. Capped at [-1, 1].
"""

from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd

__strategy_meta__ = {
    "id": "sa_etf_arb",
    "nickname": "ETF折溢价套利",
    "category": "stat_arb",
    "description": (
        "ETF premium/discount arbitrage proxy. Computes Z-score of price "
        "deviation from rolling MA as a proxy for ETF premium/discount. "
        "Signal = -Z: sell premium (short when price above MA), buy "
        "discount (long when price below MA). Uses volume filter for "
        "liquidity confirmation."
    ),
    "universe": ["equity_cn"],
    "frequency": ["1D"],
    "columns_required": ["close", "volume"],
    "default_params": {"ma_period": 20, "entry_z": 1.5, "exit_z": 0.3},
    "risk_profile": "low",
    "min_bars": 25,
    "reference": "ETF折溢价套利",
    "factors_used": [],
}


class SignalEngine:
    """ETF折溢价套利信号引擎。"""

    def __init__(self, **params: Any) -> None:
        self.ma_period: int = int(params.get("ma_period", 20))
        self.entry_z: float = float(params.get("entry_z", 1.5))
        self.exit_z: float = float(params.get("exit_z", 0.3))
        if self.ma_period < 2:
            raise ValueError(f"ma_period ({self.ma_period}) must be >= 2")
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
        """为每个ETF标的生成折溢价套利信号。

        Returns:
            Dict mapping ticker -> pd.Series of signals in [-1.0, 1.0].
        """
        signals: Dict[str, pd.Series] = {}

        for code, df in data_map.items():
            if "close" not in df.columns or "volume" not in df.columns:
                continue
            signals[code] = self._generate_one(df)

        return signals

    def _generate_one(self, df: pd.DataFrame) -> pd.Series:
        """对单个ETF标的生成折溢价套利信号。

        1. Compute rolling MA and std of close price.
        2. Z-score = (close - MA) / rolling_std.
        3. Signal = -Z (contrarian: sell premium, buy discount).
        4. Apply entry/exit thresholds via state machine.
        5. Use volume as liquidity filter: suppress signal on low-volume bars.
        """
        close: pd.Series = df["close"].astype(float)
        volume: pd.Series = df["volume"].astype(float)

        window = self.ma_period

        # Rolling statistics
        roll_ma = close.rolling(window=window, min_periods=window).mean()
        roll_std = close.rolling(window=window, min_periods=window).std()

        # Z-score of deviation from MA
        safe_std = roll_std.where(roll_std > 0, other=np.nan)
        zscore = (close - roll_ma) / safe_std

        # Volume filter: suppress signals when volume is below 20% of
        # rolling median (indicates illiquid / abnormal trading)
        vol_median = volume.rolling(window=window, min_periods=window).median()
        vol_ok = volume >= (vol_median * 0.2)

        # State machine for entry/exit
        n = len(close)
        z_arr = zscore.values
        vol_ok_arr = vol_ok.values
        signal_arr = np.full(n, np.nan)

        position = 0.0  # 0=flat, +1=long (discount), -1=short (premium)

        for i in range(n):
            z = z_arr[i]

            if np.isnan(z):
                signal_arr[i] = np.nan
                continue

            abs_z = abs(z)

            # Volume filter: if illiquid, flatten
            if not vol_ok_arr[i]:
                position = 0.0
                signal_arr[i] = 0.0
                continue

            if position == 0.0:
                # Flat: check for entry
                if z > self.entry_z:
                    # Premium: short the ETF
                    position = -1.0
                elif z < -self.entry_z:
                    # Discount: long the ETF
                    position = 1.0
            else:
                # In position: check for exit
                if abs_z < self.exit_z:
                    position = 0.0
                # Check for reversal
                elif position > 0 and z > self.entry_z:
                    position = -1.0  # reverse to short
                elif position < 0 and z < -self.entry_z:
                    position = 1.0  # reverse to long

            # Signal strength proportional to |Z| / entry_z, capped at 1.0
            if position == 0.0:
                signal_arr[i] = 0.0
            else:
                strength = min(abs_z / self.entry_z, 1.0)
                signal_arr[i] = position * strength

        signal = pd.Series(signal_arr, index=close.index)
        signal = signal.clip(lower=-1.0, upper=1.0)

        return signal
