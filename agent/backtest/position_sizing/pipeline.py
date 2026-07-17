"""Composable sizer pipeline — chains multiple PositionSizer instances."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Sequence

from backtest.position_sizing.models import SizingContext, SizingResult
from backtest.position_sizing.protocol import PositionSizer


def _tighter(a: float | None, b: float | None, *, prefer_higher: bool) -> float | None:
    """Return the tighter (more conservative) of two stop levels."""
    if a is None:
        return b
    if b is None:
        return a
    return max(a, b) if prefer_higher else min(a, b)


def _merge_results(base: SizingResult, overlay: SizingResult) -> SizingResult:
    """Merge two SizingResults: take overlay's weight, tightest stops win."""
    return SizingResult(
        target_weight=overlay.target_weight,
        stop_loss=_tighter(base.stop_loss, overlay.stop_loss, prefer_higher=True),
        take_profit=_tighter(base.take_profit, overlay.take_profit, prefer_higher=False),
        trailing_stop_distance=_tighter(
            base.trailing_stop_distance, overlay.trailing_stop_distance, prefer_higher=False,
        ),
        exit_time_bars=_tighter(base.exit_time_bars, overlay.exit_time_bars, prefer_higher=False),
        max_add_times=min(
            base.max_add_times if base.max_add_times else 999,
            overlay.max_add_times if overlay.max_add_times else 999,
        ),
        reason=overlay.reason or base.reason,
        metadata={**base.metadata, **overlay.metadata},
    )


class SizerPipeline:
    """Chain of PositionSizer instances applied sequentially.

    Each sizer receives the previous sizer's target_weight as the new
    signal_weight in its SizingContext.  Stop fields are merged by
    taking the tightest (most conservative) value.
    """

    def __init__(self, sizers: Sequence[PositionSizer]) -> None:
        if not sizers:
            raise ValueError("SizerPipeline requires at least one sizer")
        self._sizers = list(sizers)

    def size(self, ctx: SizingContext) -> SizingResult:
        accumulated: SizingResult | None = None
        current_ctx = ctx

        for sizer in self._sizers:
            result = sizer.size(current_ctx)

            if accumulated is None:
                accumulated = result
            else:
                accumulated = _merge_results(accumulated, result)

            current_ctx = SizingContext(
                timestamp=ctx.timestamp,
                symbol=ctx.symbol,
                signal_weight=result.target_weight,
                current_price=ctx.current_price,
                bar=ctx.bar,
                equity=ctx.equity,
                capital=ctx.capital,
                initial_equity=ctx.initial_equity,
                current_position=ctx.current_position,
                current_weight=ctx.current_weight,
                all_positions=ctx.all_positions,
                total_exposure=ctx.total_exposure,
                recent_trades=ctx.recent_trades,
                peak_equity=ctx.peak_equity,
                bar_idx=ctx.bar_idx,
                price_history=ctx.price_history,
            )

        assert accumulated is not None
        return accumulated
