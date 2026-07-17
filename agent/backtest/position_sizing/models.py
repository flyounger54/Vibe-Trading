"""Immutable data models for the position sizing module."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from backtest.models import Position, TradeRecord


@dataclass(frozen=True)
class SizingContext:
    """Read-only snapshot passed to each PositionSizer on every bar.

    All sizing decisions are based on *current* equity, not initial capital.
    """

    timestamp: pd.Timestamp
    symbol: str
    signal_weight: float
    current_price: float
    bar: pd.Series

    # Dynamic capital state
    equity: float
    capital: float
    initial_equity: float

    # Position awareness (for incremental add/reduce decisions)
    current_position: Position | None
    current_weight: float
    all_positions: tuple[Position, ...]
    total_exposure: float

    recent_trades: tuple[TradeRecord, ...]
    peak_equity: float
    bar_idx: int
    price_history: pd.DataFrame | None


@dataclass(frozen=True)
class SizingResult:
    """Output of a PositionSizer — adjusted weight + stop/exit parameters."""

    target_weight: float
    stop_loss: float | None = None
    take_profit: float | None = None
    trailing_stop_distance: float | None = None
    exit_time_bars: int | None = None
    max_add_times: int = 0
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StopState:
    """Per-position stop tracking state carried across bars."""

    symbol: str
    stop_loss: float | None = None
    take_profit: float | None = None
    trailing_stop_distance: float | None = None
    trailing_stop_high: float = 0.0
    exit_bar: int | None = None
