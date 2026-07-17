"""Benchmark fetching must use the consolidated loader architecture."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd

from backtest import benchmark


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        {"close": [100.0, 101.0]},
        index=pd.to_datetime(["2024-01-01", "2024-01-02"]),
    )


def test_bare_us_benchmark_is_normalized_for_global_loader() -> None:
    loader = MagicMock()
    loader.fetch.return_value = {"SPY.US": _frame()}
    with patch.object(benchmark, "GlobalStockLoader", return_value=loader):
        result = benchmark._fetch_benchmark("SPY", "2024-01-01", "2024-01-31", "1D")
    assert result.equals(_frame())
    loader.fetch.assert_called_once_with(
        ["SPY.US"], "2024-01-01", "2024-01-31", interval="1D"
    )


def test_hk_legacy_benchmark_symbol_is_normalized() -> None:
    loader = MagicMock()
    loader.fetch.return_value = {"03100.HK": _frame()}
    with patch.object(benchmark, "GlobalStockLoader", return_value=loader):
        result = benchmark._fetch_benchmark(
            "HK.03100", "2024-01-01", "2024-01-31", "1D"
        )
    assert not result.empty
    assert loader.fetch.call_args.args[0] == ["03100.HK"]


def test_a_share_benchmark_uses_astock_loader() -> None:
    loader = MagicMock()
    loader.fetch.return_value = {"000300.SH": _frame()}
    with patch.object(benchmark, "AStockLoader", return_value=loader):
        result = benchmark._fetch_benchmark(
            "000300.SH", "2024-01-01", "2024-01-31", "1D"
        )
    assert not result.empty
    loader.fetch.assert_called_once()


def test_crypto_benchmark_uses_registry_market_resolution() -> None:
    loader = MagicMock()
    loader.fetch.return_value = {"BTC-USDT": _frame()}
    with patch.object(benchmark, "resolve_loader", return_value=loader) as resolve:
        result = benchmark._fetch_benchmark(
            "BTC-USDT", "2024-01-01", "2024-01-31", "1D"
        )
    assert not result.empty
    resolve.assert_called_once_with("crypto")
