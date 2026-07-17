"""Mutation-based sentinel for detecting future-data dependent signals."""

from __future__ import annotations

from typing import Any, Mapping

import numpy as np
import pandas as pd


class LookaheadBiasError(ValueError):
    """Raised when future-bar mutation changes an already-known signal."""


def assert_no_lookahead(
    signal_engine: Any,
    data_map: Mapping[str, pd.DataFrame],
    baseline: Mapping[str, pd.Series],
    *,
    max_checks: int = 8,
) -> None:
    """Mutate future suffixes and assert all earlier signals stay identical.

    The sentinel checks every eligible cutoff when ``max_checks <= 0``;
    otherwise it samples evenly across the unified timeline. It mutates all
    symbols after each cutoff so cross-sectional strategies are covered too.
    """
    timeline = pd.DatetimeIndex(sorted({ts for frame in data_map.values() for ts in frame.index}))
    if len(timeline) < 2:
        return
    cutoffs = timeline[:-1]
    if max_checks > 0 and len(cutoffs) > max_checks:
        positions = np.linspace(0, len(cutoffs) - 1, max_checks, dtype=int)
        cutoffs = cutoffs[np.unique(positions)]

    for cutoff in cutoffs:
        mutated = {
            symbol: _mutate_future(frame, cutoff)
            for symbol, frame in data_map.items()
        }
        candidate = signal_engine.generate(mutated)
        if not isinstance(candidate, Mapping):
            raise LookaheadBiasError("lookahead sentinel requires mapping signal output")
        for symbol, expected in baseline.items():
            actual = candidate.get(symbol)
            if not isinstance(actual, pd.Series):
                raise LookaheadBiasError(
                    f"lookahead sentinel received invalid signal for {symbol}"
                )
            known_index = expected.index[expected.index <= cutoff]
            left = expected.reindex(known_index)
            right = actual.reindex(known_index)
            if not left.equals(right):
                changed = known_index[~(left.eq(right) | (left.isna() & right.isna()))]
                first = changed[0] if len(changed) else cutoff
                raise LookaheadBiasError(
                    f"future-data mutation after {cutoff} changed {symbol} signal at {first}"
                )


def _mutate_future(frame: pd.DataFrame, cutoff: pd.Timestamp) -> pd.DataFrame:
    clone = frame.copy(deep=True)
    clone.attrs = frame.attrs.copy()
    future = clone.index > cutoff
    for column in ("open", "high", "low", "close", "volume"):
        if column in clone.columns:
            clone.loc[future, column] = clone.loc[future, column].astype(float) * 37.0 + 17.0
    return clone
