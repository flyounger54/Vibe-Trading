"""ATR-based position sizing (Turtle Trading style).

``unit_size = (equity × risk_per_unit) / ATR``
One ATR move equals ``risk_per_unit`` percent of equity.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from backtest.position_sizing.models import SizingContext, SizingResult


def _calc_atr(history: pd.DataFrame, period: int) -> float:
    """Compute ATR from OHLCV history."""
    if history is None or len(history) < period + 1:
        return 0.0
    high = history["high"].astype(float)
    low = history["low"].astype(float)
    close = history["close"].astype(float)
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr = tr.tail(period).mean()
    return float(atr) if np.isfinite(atr) else 0.0


class ATRSizer:
    """Turtle-style ATR position sizing."""

    def __init__(
        self,
        atr_period: int = 20,
        risk_per_unit: float = 0.01,
        max_position_pct: float = 0.25,
    ) -> None:
        if atr_period < 2:
            raise ValueError(f"atr_period must be >= 2, got {atr_period}")
        self._atr_period = atr_period
        self._risk_per_unit = risk_per_unit
        self._max_pct = max_position_pct

    def size(self, ctx: SizingContext) -> SizingResult:
        if abs(ctx.signal_weight) < 1e-9 or ctx.equity <= 0:
            return SizingResult(target_weight=0.0, reason="no_signal")

        atr = _calc_atr(ctx.price_history, self._atr_period)
        if atr <= 0:
            return SizingResult(
                target_weight=ctx.signal_weight,
                reason="atr_unavailable",
            )

        unit_notional = (ctx.equity * self._risk_per_unit) / atr * ctx.current_price
        raw_weight = unit_notional / ctx.equity if ctx.equity > 0 else 0.0
        weight = min(raw_weight, self._max_pct, abs(ctx.signal_weight))
        direction = 1.0 if ctx.signal_weight > 0 else -1.0

        return SizingResult(
            target_weight=direction * weight,
            reason="atr_sizing",
            metadata={"atr": atr, "raw_weight": raw_weight},
        )


def create(**params) -> ATRSizer:
    return ATRSizer(**params)
