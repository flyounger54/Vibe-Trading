"""Dynamic loading of position sizers from backtest config."""

from __future__ import annotations

import importlib
import logging
from typing import Any, Dict, Optional

from backtest.position_sizing.pipeline import SizerPipeline
from backtest.position_sizing.protocol import PositionSizer

logger = logging.getLogger(__name__)


def load_position_sizer(config: Dict[str, Any]) -> Optional[PositionSizer]:
    """Build a PositionSizer (or pipeline) from the ``position_sizing`` config block.

    Returns None when the config has no ``position_sizing`` key, preserving
    full backward compatibility with existing backtests.
    """
    ps_config = config.get("position_sizing")
    if not ps_config:
        return None

    sizer_defs = ps_config.get("sizers") or []
    stop_def = ps_config.get("stops")

    sizers: list[PositionSizer] = []

    for sdef in sizer_defs:
        sdef = dict(sdef)
        sizer_type = sdef.pop("type")
        sizer = _load_one(sizer_type, sdef)
        if sizer is not None:
            sizers.append(sizer)

    if stop_def:
        stop_def = dict(stop_def)
        stop_type = stop_def.pop("type", "atr_stop")
        stop_sizer = _load_one("stops", {"stop_type": stop_type, **stop_def})
        if stop_sizer is not None:
            sizers.append(stop_sizer)

    if not sizers:
        return None
    if len(sizers) == 1:
        return sizers[0]
    return SizerPipeline(sizers)


def _load_one(module_name: str, params: Dict[str, Any]) -> Optional[PositionSizer]:
    """Import ``backtest.position_sizing.<module_name>`` and call its ``create(**params)``."""
    try:
        mod = importlib.import_module(f"backtest.position_sizing.{module_name}")
        factory = getattr(mod, "create")
        return factory(**params)
    except (ImportError, AttributeError) as exc:
        logger.warning("Failed to load position sizer '%s': %s", module_name, exc)
        return None
