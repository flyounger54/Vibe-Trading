
# ============================================================
# 中文名称: 网格交易策略
# 简要说明: 以滚动均线为中心、ATR 为间距划分 N 条网格线。价格触及下方
#           网格线时发出买入信号，触及上方网格线时发出卖出信号。越偏离
#           中心信号越强。
# 典型用途: 震荡行情中的高抛低吸自动交易。
# ============================================================
"""Crypto Grid Trading (crypto_grid).

Divide the price range into N grid levels centered on a rolling MA with
ATR-based spacing. Generate buy signals when price drops to a lower grid
level and sell signals when price rises to an upper grid level. Signal
strength increases with distance from center.
"""

from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd

__strategy_meta__ = {
    "id": "crypto_grid",
    "nickname": "网格交易",
    "category": "crypto",
    "description": (
        "Grid trading: divide price range into N levels around rolling MA. "
        "Buy at lower grids, sell at upper grids. Grid spacing is ATR-based. "
        "Signal strength proportional to distance from center."
    ),
    "universe": ["crypto"],
    "frequency": ["1D"],
    "columns_required": ["open", "high", "low", "close"],
    "default_params": {
        "grid_count": 10,
        "ma_period": 50,
        "atr_period": 14,
        "atr_mult": 3.0,
    },
    "risk_profile": "medium",
    "min_bars": 55,
    "reference": "经典网格交易策略",
    "factors_used": [],
}


class SignalEngine:
    """网格交易信号引擎。"""

    def __init__(self, **params: Any) -> None:
        self.grid_count: int = int(params.get("grid_count", 10))
        self.ma_period: int = int(params.get("ma_period", 50))
        self.atr_period: int = int(params.get("atr_period", 14))
        self.atr_mult: float = float(params.get("atr_mult", 3.0))
        if self.grid_count < 2:
            raise ValueError(f"grid_count must be >= 2, got {self.grid_count}")
        if self.ma_period < 1:
            raise ValueError(f"ma_period must be >= 1, got {self.ma_period}")
        if self.atr_period < 1:
            raise ValueError(f"atr_period must be >= 1, got {self.atr_period}")
        if self.atr_mult <= 0:
            raise ValueError(f"atr_mult must be > 0, got {self.atr_mult}")

    def generate(
        self, data_map: Dict[str, pd.DataFrame]
    ) -> Dict[str, pd.Series]:
        """Generate grid trading signals.

        Returns:
            Dict mapping ticker -> pd.Series of signals in [-1.0, 1.0].
        """
        signals: Dict[str, pd.Series] = {}

        for code, df in data_map.items():
            required = {"open", "high", "low", "close"}
            if not required.issubset(df.columns):
                continue

            high = df["high"].astype(float)
            low = df["low"].astype(float)
            close = df["close"].astype(float)

            # Rolling MA as grid center
            ma = close.rolling(
                window=self.ma_period, min_periods=self.ma_period
            ).mean()

            # ATR for grid spacing
            prev_close = close.shift(1)
            tr = pd.concat(
                [
                    high - low,
                    (high - prev_close).abs(),
                    (low - prev_close).abs(),
                ],
                axis=1,
            ).max(axis=1)
            atr = tr.rolling(
                window=self.atr_period, min_periods=self.atr_period
            ).mean()

            # Grid half-range = atr_mult * ATR
            half_range = self.atr_mult * atr

            # Deviation from center, normalized to [-1, 1]
            # Positive deviation (price above MA) -> sell signal (negative)
            # Negative deviation (price below MA) -> buy signal (positive)
            deviation = close - ma
            safe_half = half_range.replace(0, np.nan)
            normalized = deviation / safe_half

            # Quantize to grid levels
            half_grids = self.grid_count // 2
            if half_grids > 0:
                # Snap to nearest grid level, then scale back
                grid_step = 1.0 / half_grids
                quantized = (normalized / grid_step).round() * grid_step
            else:
                quantized = normalized

            # Invert: below MA -> buy (+), above MA -> sell (-)
            raw = -quantized

            # Clamp to [-1, 1]
            raw = raw.clip(-1.0, 1.0)

            # NaN where indicators are not ready
            ready = ma.notna() & atr.notna()
            raw = raw.where(ready, other=np.nan)

            signals[code] = raw

        return signals
