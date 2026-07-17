"""Strategy Zoo with runtime-discovered quantitative trading strategies.

Mirrors the Alpha Zoo architecture. Each strategy exposes a ``SignalEngine``
class compatible with the existing backtest engine contract.
"""

from src.strategies.base import Strategy, StrategyCompute
from src.strategies.registry import (
    StrategyMeta,
    StrategyConfigurationError,
    StrategyRegistry,
    get_default_registry,
    reset_default_registry,
)

__all__ = [
    "Strategy",
    "StrategyCompute",
    "StrategyMeta",
    "StrategyConfigurationError",
    "StrategyRegistry",
    "get_default_registry",
    "reset_default_registry",
]
