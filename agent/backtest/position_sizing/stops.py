"""Stop / exit management sizers.

Each sizer passes through the incoming signal weight unchanged and sets
the appropriate stop/exit fields in SizingResult.  Compose via
SizerPipeline to combine sizing + stops.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from backtest.position_sizing.models import SizingContext, SizingResult


# ── Helpers ──

def _calc_atr(history: pd.DataFrame | None, period: int) -> float:
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


# ── Fixed percentage stop ──

class FixedStopSizer:
    """Set stop-loss at a fixed percentage below entry."""

    def __init__(self, stop_pct: float = 0.05, profit_target_pct: float | None = None) -> None:
        self._stop_pct = stop_pct
        self._tp_pct = profit_target_pct

    def size(self, ctx: SizingContext) -> SizingResult:
        price = ctx.current_price
        direction = 1.0 if ctx.signal_weight > 0 else -1.0

        if direction > 0:
            sl = price * (1.0 - self._stop_pct)
            tp = price * (1.0 + self._tp_pct) if self._tp_pct else None
        else:
            sl = price * (1.0 + self._stop_pct)
            tp = price * (1.0 - self._tp_pct) if self._tp_pct else None

        return SizingResult(
            target_weight=ctx.signal_weight,
            stop_loss=sl,
            take_profit=tp,
            reason="fixed_stop",
        )


# ── ATR-based stop ──

class ATRStopSizer:
    """Set stop-loss at N × ATR from current price."""

    def __init__(
        self,
        atr_multiplier: float = 2.0,
        atr_period: int = 20,
        trailing: bool = False,
        profit_target_pct: float | None = None,
        time_exit_bars: int | None = None,
    ) -> None:
        self._mult = atr_multiplier
        self._period = atr_period
        self._trailing = trailing
        self._tp_pct = profit_target_pct
        self._time_bars = time_exit_bars

    def size(self, ctx: SizingContext) -> SizingResult:
        atr = _calc_atr(ctx.price_history, self._period)
        price = ctx.current_price
        direction = 1.0 if ctx.signal_weight > 0 else -1.0

        if atr > 0:
            stop_distance = atr * self._mult
            sl = price - direction * stop_distance
        else:
            sl = None
            stop_distance = None

        if self._tp_pct and price > 0:
            tp = price * (1.0 + direction * self._tp_pct)
        else:
            tp = None

        return SizingResult(
            target_weight=ctx.signal_weight,
            stop_loss=sl,
            take_profit=tp,
            trailing_stop_distance=stop_distance if self._trailing else None,
            exit_time_bars=self._time_bars,
            reason="atr_stop",
            metadata={"atr": atr, "stop_distance": stop_distance},
        )


# ── Trailing percentage stop ──

class TrailingStopSizer:
    """Set trailing stop as a fixed percentage from the high-water mark."""

    def __init__(self, trail_pct: float = 0.05) -> None:
        self._trail_pct = trail_pct

    def size(self, ctx: SizingContext) -> SizingResult:
        return SizingResult(
            target_weight=ctx.signal_weight,
            trailing_stop_distance=ctx.current_price * self._trail_pct,
            reason="trailing_pct_stop",
        )


# ── Time exit ──

class TimeExitSizer:
    """Force exit after N bars."""

    def __init__(self, max_bars: int = 20) -> None:
        self._max_bars = max_bars

    def size(self, ctx: SizingContext) -> SizingResult:
        return SizingResult(
            target_weight=ctx.signal_weight,
            exit_time_bars=self._max_bars,
            reason="time_exit",
        )


# ── Profit target ──

class ProfitTargetSizer:
    """Set take-profit at a fixed percentage above entry."""

    def __init__(self, target_pct: float = 0.15) -> None:
        self._target_pct = target_pct

    def size(self, ctx: SizingContext) -> SizingResult:
        price = ctx.current_price
        direction = 1.0 if ctx.signal_weight > 0 else -1.0
        tp = price * (1.0 + direction * self._target_pct)

        return SizingResult(
            target_weight=ctx.signal_weight,
            take_profit=tp,
            reason="profit_target",
        )


# ── Factory ──

_STOP_TYPES = {
    "fixed_pct": FixedStopSizer,
    "atr_stop": ATRStopSizer,
    "trailing_pct": TrailingStopSizer,
    "time_exit": TimeExitSizer,
    "profit_target": ProfitTargetSizer,
}


def create(stop_type: str = "atr_stop", **params) -> (
    FixedStopSizer | ATRStopSizer | TrailingStopSizer | TimeExitSizer | ProfitTargetSizer
):
    cls = _STOP_TYPES.get(stop_type)
    if cls is None:
        raise ValueError(f"Unknown stop type '{stop_type}', valid: {list(_STOP_TYPES)}")
    return cls(**params)
