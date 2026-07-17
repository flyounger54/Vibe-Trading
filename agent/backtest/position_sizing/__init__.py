"""Position sizing / money management module.

Provides a composable pipeline of PositionSizer implementations that sit
between signal generation and order execution, dynamically adjusting
position weights based on equity, volatility, drawdown, and stop rules.
"""

from backtest.position_sizing.models import SizingContext, SizingResult, StopState
from backtest.position_sizing.pipeline import SizerPipeline
from backtest.position_sizing.protocol import PositionSizer

__all__ = [
    "PositionSizer",
    "SizingContext",
    "SizingResult",
    "StopState",
    "SizerPipeline",
]
