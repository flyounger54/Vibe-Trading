"""Fixed Fractional position sizing.

Classic Van Tharp method: risk a fixed percentage of current equity per trade.
``size = (equity × risk_per_trade) / (entry_price - stop_loss_price)``

When no explicit stop is set upstream, falls back to
``entry_price × default_stop_pct`` as the assumed risk distance.
"""

from __future__ import annotations

from backtest.position_sizing.models import SizingContext, SizingResult


class FixedFractionalSizer:
    """Risk a constant fraction of equity on each trade."""

    def __init__(
        self,
        risk_per_trade: float = 0.02,
        max_position_pct: float = 0.25,
        default_stop_pct: float = 0.05,
    ) -> None:
        if not 0 < risk_per_trade <= 1:
            raise ValueError(f"risk_per_trade must be in (0, 1], got {risk_per_trade}")
        if not 0 < max_position_pct <= 1:
            raise ValueError(f"max_position_pct must be in (0, 1], got {max_position_pct}")
        self._risk_pct = risk_per_trade
        self._max_pct = max_position_pct
        self._default_stop = default_stop_pct

    def size(self, ctx: SizingContext) -> SizingResult:
        if abs(ctx.signal_weight) < 1e-9 or ctx.equity <= 0:
            return SizingResult(target_weight=0.0, reason="no_signal")

        risk_distance = ctx.current_price * self._default_stop
        risk_amount = ctx.equity * self._risk_pct
        raw_notional = risk_amount / risk_distance if risk_distance > 0 else 0.0
        raw_weight = raw_notional / ctx.equity if ctx.equity > 0 else 0.0

        weight = min(raw_weight, self._max_pct, abs(ctx.signal_weight))
        direction = 1.0 if ctx.signal_weight > 0 else -1.0

        return SizingResult(
            target_weight=direction * weight,
            reason="fixed_fractional",
            metadata={
                "risk_amount": risk_amount,
                "risk_distance": risk_distance,
                "raw_weight": raw_weight,
            },
        )


def create(**params) -> FixedFractionalSizer:
    return FixedFractionalSizer(**params)
