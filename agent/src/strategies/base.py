"""Strategy Zoo base types and protocol.

Mirrors the Alpha Zoo design (``src/factors/base.py``). Each strategy module
in ``zoo/<category>/<id>.py`` exposes a ``__strategy_meta__`` dict literal and
a ``SignalEngine`` class whose ``generate()`` method matches the backtest
engine contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Protocol, runtime_checkable

import pandas as pd


@dataclass(frozen=True, slots=True)
class Strategy:
    """Lightweight handle for a registered strategy (registry-owned)."""

    id: str
    category: str
    module_path: str
    meta: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class StrategyCompute(Protocol):
    """Structural protocol every strategy module's ``SignalEngine`` satisfies."""

    def generate(
        self, data_map: Dict[str, pd.DataFrame]
    ) -> Dict[str, pd.Series]: ...
