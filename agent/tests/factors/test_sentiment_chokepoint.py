"""Contract tests for the pure, local-only chokepoint sentiment alpha."""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.factors.registry import Registry


def test_chokepoint_alpha_declares_and_uses_only_its_explicit_volume_input() -> None:
    registry = Registry()
    alpha = registry.get("sentiment_chokepoint_attn")
    assert alpha.meta["columns_required"] == ["volume"]

    index = pd.date_range("2024-01-01", periods=80, freq="D")
    volume = pd.DataFrame(
        {
            "AAA": np.linspace(1_000.0, 4_000.0, len(index)),
            "BBB": np.linspace(4_000.0, 1_000.0, len(index)),
        },
        index=index,
    )
    output = registry.compute("sentiment_chokepoint_attn", {"volume": volume})

    assert output.index.equals(volume.index)
    assert output.columns.equals(volume.columns)
    assert not np.isinf(output.to_numpy(dtype=float, na_value=np.nan)).any()
