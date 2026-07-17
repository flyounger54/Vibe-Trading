"""Equal-weight position sizing (1/N baseline).

Allocates equal weight to every active signal, capped by max_position_pct.
"""

from __future__ import annotations

from backtest.position_sizing.models import SizingContext, SizingResult


class EqualWeightSizer:
    """Divide equity equally among active positions."""

    def __init__(self, max_position_pct: float = 0.25) -> None:
        self._max_pct = max_position_pct

    def size(self, ctx: SizingContext) -> SizingResult:
        if abs(ctx.signal_weight) < 1e-9 or ctx.equity <= 0:
            return SizingResult(target_weight=0.0, reason="no_signal")

        n_active = sum(1 for p in ctx.all_positions if p is not None) + (
            1 if ctx.current_position is None else 0
        )
        n_active = max(n_active, 1)
        weight = min(1.0 / n_active, self._max_pct, abs(ctx.signal_weight))
        direction = 1.0 if ctx.signal_weight > 0 else -1.0

        return SizingResult(
            target_weight=direction * weight,
            reason="equal_weight",
            metadata={"n_active": n_active},
        )


def create(**params) -> EqualWeightSizer:
    return EqualWeightSizer(**params)
