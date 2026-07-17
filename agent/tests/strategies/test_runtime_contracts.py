"""Runtime safety contracts for every default-runnable strategy."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pandas.testing as pdt

from src.strategies.registry import StrategyRegistry

from .test_signals import _pick_data_map


_FUTURE_PROBE = 260
_FUTURE_MUTATION_START = 280


def _copy_data_map(data_map: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    return {code: frame.copy(deep=True) for code, frame in data_map.items()}


def _mutate_future(data_map: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Poison only future rows; output through the probe must not change."""
    mutated = _copy_data_map(data_map)
    for frame in mutated.values():
        numeric_columns = frame.select_dtypes(include="number").columns
        frame.loc[frame.index[_FUTURE_MUTATION_START:], numeric_columns] = 1e12
    return mutated


def _assert_signal_shape_range_and_finiteness(
    strategy_id: str,
    signals: dict[str, pd.Series],
    data_map: dict[str, pd.DataFrame],
    min_bars: int,
) -> None:
    assert set(signals) == set(data_map), f"{strategy_id}: missing instrument signals"
    for code, signal in signals.items():
        assert isinstance(signal, pd.Series), f"{strategy_id}/{code}: not a Series"
        assert signal.index.equals(data_map[code].index), f"{strategy_id}/{code}: index drift"
        values = signal.to_numpy(dtype=np.float64, na_value=np.nan)
        assert not np.isinf(values).any(), f"{strategy_id}/{code}: contains +/-inf"
        # NaN is acceptable only during the declared warm-up period.  This turns
        # min_bars into an operational guarantee rather than display metadata.
        assert not signal.iloc[min_bars:].isna().any(), (
            f"{strategy_id}/{code}: NaN after declared min_bars={min_bars}"
        )
        valid = signal.dropna()
        assert (valid >= -1.0 - 1e-9).all(), f"{strategy_id}/{code}: signal below -1"
        assert (valid <= 1.0 + 1e-9).all(), f"{strategy_id}/{code}: signal above 1"


def test_default_runnable_strategies_are_deterministic_and_causal(
    registry: StrategyRegistry,
    data_map_single: dict[str, pd.DataFrame],
    data_map_pair: dict[str, pd.DataFrame],
    data_map_multi: dict[str, pd.DataFrame],
) -> None:
    """Fresh engines must agree, and future data must not alter past signals."""
    for strategy_id in registry.list_default_runnable():
        data_map = _pick_data_map(strategy_id, data_map_single, data_map_pair, data_map_multi)
        first = registry.load(strategy_id).generate(_copy_data_map(data_map))
        second = registry.load(strategy_id).generate(_copy_data_map(data_map))
        future_mutated = registry.load(strategy_id).generate(_mutate_future(data_map))

        assert set(first) == set(second) == set(future_mutated), strategy_id
        for code, signal in first.items():
            pdt.assert_series_equal(signal, second[code], obj=f"{strategy_id}/{code}: nondeterministic")
            pdt.assert_series_equal(
                signal.iloc[: _FUTURE_PROBE + 1],
                future_mutated[code].iloc[: _FUTURE_PROBE + 1],
                obj=f"{strategy_id}/{code}: look-ahead leak",
            )


def test_default_runnable_strategies_honor_output_contracts_on_normal_and_short_history(
    registry: StrategyRegistry,
    data_map_single: dict[str, pd.DataFrame],
    data_map_pair: dict[str, pd.DataFrame],
    data_map_multi: dict[str, pd.DataFrame],
) -> None:
    """Every default engine handles short history and returns bounded finite signals."""
    for strategy_id in registry.list_default_runnable():
        data_map = _pick_data_map(strategy_id, data_map_single, data_map_pair, data_map_multi)
        min_bars = registry.get(strategy_id).meta["min_bars"]
        signals = registry.load(strategy_id).generate(_copy_data_map(data_map))
        _assert_signal_shape_range_and_finiteness(strategy_id, signals, data_map, min_bars)

        short_size = max(1, min_bars - 1)
        short_data = {code: frame.iloc[:short_size].copy() for code, frame in data_map.items()}
        short_signals = registry.load(strategy_id).generate(short_data)
        _assert_signal_shape_range_and_finiteness(
            strategy_id,
            short_signals,
            short_data,
            len(short_data[next(iter(short_data))]),
        )
