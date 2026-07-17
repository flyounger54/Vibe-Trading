"""PositionSizer structural protocol."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from backtest.position_sizing.models import SizingContext, SizingResult


@runtime_checkable
class PositionSizer(Protocol):
    """Structural interface for all position sizing implementations."""

    def size(self, ctx: SizingContext) -> SizingResult: ...
