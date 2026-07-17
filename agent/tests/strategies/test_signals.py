"""Signal validation for the complete strategy zoo: range, NaN safety, types."""

from __future__ import annotations

import numpy as np
import pandas as pd
from src.strategies.registry import StrategyRegistry

PAIR_STRATEGIES = {"sa_coint_pair", "sa_ah_premium"}
MULTI_STRATEGIES = {
    "mom_cross_section", "mf_quality_value", "mf_fundamental",
    "mf_factor_rotation", "mf_alpha_combo", "sa_market_neutral",
    "alloc_black_litterman", "crypto_defi_yield", "mom_dual",
}


def _pick_data_map(
    sid: str,
    dm_single: dict[str, pd.DataFrame],
    dm_pair: dict[str, pd.DataFrame],
    dm_multi: dict[str, pd.DataFrame],
) -> dict[str, pd.DataFrame]:
    if sid in PAIR_STRATEGIES:
        return dm_pair
    if sid in MULTI_STRATEGIES:
        return dm_multi
    return dm_single


class TestAllStrategiesSignalRange:
    """Every strategy must produce signals in [-1.0, 1.0]."""

    def test_all_signals_in_range(
        self,
        registry: StrategyRegistry,
        data_map_single: dict[str, pd.DataFrame],
        data_map_pair: dict[str, pd.DataFrame],
        data_map_multi: dict[str, pd.DataFrame],
    ) -> None:
        for sid in registry.list_default_runnable():
            engine = registry.load(sid)
            dm = _pick_data_map(sid, data_map_single, data_map_pair, data_map_multi)
            signals = engine.generate(dm)

            for code, sig in signals.items():
                valid = sig.dropna()
                if len(valid) == 0:
                    continue
                assert valid.min() >= -1.0 - 1e-9, (
                    f"{sid}/{code}: min={valid.min():.6f} < -1.0"
                )
                assert valid.max() <= 1.0 + 1e-9, (
                    f"{sid}/{code}: max={valid.max():.6f} > 1.0"
                )


class TestAllStrategiesReturnType:
    """Every strategy must return Dict[str, pd.Series]."""

    def test_return_dict_of_series(
        self,
        registry: StrategyRegistry,
        data_map_single: dict[str, pd.DataFrame],
        data_map_pair: dict[str, pd.DataFrame],
        data_map_multi: dict[str, pd.DataFrame],
    ) -> None:
        for sid in registry.list_default_runnable():
            engine = registry.load(sid)
            dm = _pick_data_map(sid, data_map_single, data_map_pair, data_map_multi)
            signals = engine.generate(dm)

            assert isinstance(signals, dict), f"{sid}: not dict"
            for code, sig in signals.items():
                assert isinstance(sig, pd.Series), (
                    f"{sid}/{code}: not Series, got {type(sig)}"
                )


class TestAllStrategiesNoInf:
    """Signals must not contain +/- inf."""

    def test_no_inf_in_signals(
        self,
        registry: StrategyRegistry,
        data_map_single: dict[str, pd.DataFrame],
        data_map_pair: dict[str, pd.DataFrame],
        data_map_multi: dict[str, pd.DataFrame],
    ) -> None:
        for sid in registry.list_default_runnable():
            engine = registry.load(sid)
            dm = _pick_data_map(sid, data_map_single, data_map_pair, data_map_multi)
            signals = engine.generate(dm)

            for code, sig in signals.items():
                arr = sig.to_numpy(dtype=np.float64, na_value=np.nan)
                assert not np.isinf(arr).any(), f"{sid}/{code}: contains inf"


class TestAllStrategiesProduceNonZero:
    """At least some strategies should produce non-zero signals (sanity)."""

    def test_majority_produce_nonzero(
        self,
        registry: StrategyRegistry,
        data_map_single: dict[str, pd.DataFrame],
        data_map_pair: dict[str, pd.DataFrame],
        data_map_multi: dict[str, pd.DataFrame],
    ) -> None:
        active_count = 0
        default_runnable = registry.list_default_runnable()
        for sid in default_runnable:
            engine = registry.load(sid)
            dm = _pick_data_map(sid, data_map_single, data_map_pair, data_map_multi)
            signals = engine.generate(dm)
            total_nz = sum(s.fillna(0).ne(0).sum() for s in signals.values())
            if total_nz > 0:
                active_count += 1
        total = len(default_runnable)
        assert active_count >= int(total * 0.70), (
            f"Only {active_count}/{total} strategies produced non-zero signals"
        )


class TestEmptyDataMap:
    """Strategies should handle empty data gracefully."""

    def test_empty_data_map(self, registry: StrategyRegistry) -> None:
        for sid in registry.list_default_runnable():
            engine = registry.load(sid)
            signals = engine.generate({})
            assert isinstance(signals, dict)
            assert len(signals) == 0
