"""Dense branch coverage for factor operators and chart-pattern detectors."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import src.factors.base as factors
import src.tools.pattern_tool as patterns


pytestmark = pytest.mark.unit


def _frame(values: list[object]) -> pd.DataFrame:
    return pd.DataFrame({"A": values})


def test_factor_window_validation_and_nan_inner_functions() -> None:
    invalid_calls = [
        lambda: factors.ts_rank(_frame([1.0]), 0),
        lambda: factors.ts_corr(_frame([1.0]), _frame([1.0]), 1),
        lambda: factors.ts_cov(_frame([1.0]), _frame([1.0]), 1),
        lambda: factors.ts_mean(_frame([1.0]), 0),
        lambda: factors.ts_std(_frame([1.0]), 1),
        lambda: factors.ts_max(_frame([1.0]), 0),
        lambda: factors.ts_min(_frame([1.0]), 0),
        lambda: factors.ts_argmax(_frame([1.0]), 0),
        lambda: factors.ts_argmin(_frame([1.0]), 0),
        lambda: factors.delta(_frame([1.0]), 0),
        lambda: factors.decay_linear(_frame([1.0]), 0),
    ]
    for call in invalid_calls:
        with pytest.raises(ValueError):
            call()

    assert np.isnan(factors._argmax_last(np.array([np.nan, np.nan])))
    assert np.isnan(factors._argmin_last(np.array([np.nan, np.nan])))
    assert factors._argmax_last(np.array([np.nan, 2.0, 1.0])) == 1.0
    assert factors._argmin_last(np.array([np.nan, 2.0, 1.0])) == 2.0

    ranked = factors.ts_rank(_frame([np.nan]), 1)
    assert np.isnan(ranked.iloc[0, 0])
    ranked = factors.ts_rank(_frame([1.0, np.nan]), 2)
    assert np.isnan(ranked.iloc[-1, 0])
    decayed = factors.decay_linear(_frame([1.0, np.nan]), 2)
    assert np.isnan(decayed.iloc[-1, 0])


def test_factor_float_conversion_and_vwap_branch_matrix() -> None:
    floating = _frame([1.0])
    assert factors._as_float(floating) is floating
    converted = factors._as_float(_frame([1]))
    assert converted.dtypes.iloc[0] == np.float64

    direct = _frame([9.0])
    assert factors.vwap({"vwap": direct}, "crypto") is direct
    with pytest.raises(KeyError, match="equity_cn"):
        factors.vwap({"amount": direct}, factors.Market.EQUITY_CN)
    with pytest.raises(KeyError, match="missing"):
        factors.vwap({"open": direct}, factors.Market.EQUITY_US)


def test_support_resistance_cluster_empty_short_flat_and_split(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    series = pd.Series([np.nan, 5.0, 5.0, 5.0, 8.0, 20.0])
    monkeypatch.setattr(
        patterns,
        "find_peaks_valleys",
        lambda close, window: {"peaks": [0, 1, 2, 3], "valleys": []},
    )
    result = patterns.support_resistance(series, window=1, num_levels=2)
    assert result["support"] == [] and result["resistance"] == [5.0]

    monkeypatch.setattr(
        patterns,
        "find_peaks_valleys",
        lambda close, window: {"peaks": [1, 4, 5], "valleys": [1]},
    )
    result = patterns.support_resistance(series, window=1, num_levels=5)
    assert result["support"] == [5.0] and len(result["resistance"]) == 3

    result = patterns.support_resistance(series, window=1, num_levels=1)
    assert len(result["resistance"]) == 1


def test_pattern_nan_and_rejection_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    slopes = patterns.trend_line_slope(pd.Series([1.0, np.nan, 3.0]), window=2)
    assert slopes.isna().all()

    monkeypatch.setattr(
        patterns,
        "find_peaks_valleys",
        lambda close, window: {"peaks": [0, 1, 2], "valleys": []},
    )
    assert patterns.head_and_shoulders(pd.Series([np.nan, 3.0, 1.0]), 1).sum() == 0
    assert patterns.head_and_shoulders(pd.Series([3.0, 2.0, 1.0]), 1).sum() == 0
    assert patterns.head_and_shoulders(pd.Series([-1.0, 2.0, 1.0]), 1).sum() == 0
    assert patterns.head_and_shoulders(pd.Series([1.0, 3.0, 2.0]), 1).sum() == 0

    monkeypatch.setattr(
        patterns,
        "find_peaks_valleys",
        lambda close, window: {"peaks": [0, 1], "valleys": [2, 3]},
    )
    out = patterns.double_top_bottom(pd.Series([np.nan, 1.0, np.nan, 1.0]), 1)
    assert not out.any()
    out = patterns.double_top_bottom(pd.Series([-1.0, 1.0, -1.0, 1.0]), 1)
    assert not out.any()

    monkeypatch.setattr(
        patterns,
        "find_peaks_valleys",
        lambda close, window: {"peaks": [0, 2], "valleys": [1, 2]},
    )
    out = patterns.double_top_bottom(pd.Series([5.0, 1.0, 5.0]), 1)
    assert out.iloc[2] == 1


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ([10.0, 5.0, 10.0, 7.0, 9.0], 1),
        ([10.0, 5.0, 8.0, 5.0, 9.0], -1),
        ([5.0, 5.0, 5.0, 5.0, 5.0], 0),
        ([10.0, 5.0, 9.0, 5.5, 8.0], 0),
    ],
)
def test_triangle_outcome_matrix(
    values: list[float], expected: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        patterns,
        "find_peaks_valleys",
        lambda close, window: {"peaks": [0, 2], "valleys": [1, 3]},
    )
    result = patterns.triangle(pd.Series(values), window=4)
    assert result.iloc[-1] == expected


def test_triangle_and_broadening_insufficient_and_outcome_edges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        patterns,
        "find_peaks_valleys",
        lambda close, window: {"peaks": [0], "valleys": [1]},
    )
    values = pd.Series([1.0, 2.0, 1.0, 2.0, 1.0])
    assert patterns.triangle(values, 4).iloc[-1] == 0
    assert patterns.broadening(values, 4).iloc[-1] == 0

    monkeypatch.setattr(
        patterns,
        "find_peaks_valleys",
        lambda close, window: {"peaks": [0, 2], "valleys": [1, 3]},
    )
    widening = pd.Series([8.0, 5.0, 10.0, 3.0, 7.0])
    assert patterns.broadening(widening, 4).iloc[-1] == 1
    narrowing = pd.Series([10.0, 3.0, 8.0, 5.0, 7.0])
    assert patterns.broadening(narrowing, 4).iloc[-1] == 0


def test_pattern_all_empty_csv_and_tool_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VIBE_TRADING_ALLOWED_RUN_ROOTS", str(tmp_path))
    run_dir = tmp_path / "run"
    artifacts = run_dir / "artifacts"
    artifacts.mkdir(parents=True)
    empty = artifacts / "ohlcv_EMPTY.csv"
    empty.write_text("date,open,high,low,close\n", encoding="utf-8")
    result = json.loads(patterns.run_pattern(str(run_dir), patterns="all", window=2))
    assert result["status"] == "ok" and result["results"] == {}

    calls: list[tuple[str, str, int]] = []
    monkeypatch.setattr(
        patterns,
        "run_pattern",
        lambda run_dir, patterns, window: calls.append((run_dir, patterns, window)) or "{}",
    )
    assert patterns.PatternTool().execute(run_dir="run") == "{}"
    assert calls == [("run", "all", 10)]
