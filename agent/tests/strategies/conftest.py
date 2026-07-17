"""Test fixtures for strategy zoo tests."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.strategies.registry import StrategyRegistry


@pytest.fixture(scope="session")
def registry() -> StrategyRegistry:
    return StrategyRegistry()


@pytest.fixture(scope="session")
def synthetic_dates() -> pd.DatetimeIndex:
    return pd.date_range("2020-01-01", periods=300, freq="B")


@pytest.fixture(scope="session")
def synthetic_ohlcv(synthetic_dates: pd.DatetimeIndex) -> pd.DataFrame:
    np.random.seed(42)
    n = len(synthetic_dates)
    base = 100 + np.cumsum(np.random.randn(n) * 0.5)
    return pd.DataFrame(
        {
            "open": base + np.random.randn(n) * 0.2,
            "high": base + np.abs(np.random.randn(n) * 0.5),
            "low": base - np.abs(np.random.randn(n) * 0.5),
            "close": base,
            "volume": np.random.randint(1_000_000, 5_000_000, n).astype(float),
            "pe_ttm": 15 + np.random.randn(n) * 3,
            "roe": 0.12 + np.random.randn(n) * 0.03,
        },
        index=synthetic_dates,
    )


@pytest.fixture(scope="session")
def data_map_single(synthetic_ohlcv: pd.DataFrame) -> dict[str, pd.DataFrame]:
    return {"000001.SZ": synthetic_ohlcv.copy()}


@pytest.fixture(scope="session")
def data_map_pair(synthetic_ohlcv: pd.DataFrame) -> dict[str, pd.DataFrame]:
    np.random.seed(99)
    df2 = synthetic_ohlcv.copy()
    df2["close"] = df2["close"] * (1 + np.random.randn(len(df2)) * 0.005)
    return {"000001.SZ": synthetic_ohlcv.copy(), "601318.SH": df2}


@pytest.fixture(scope="session")
def data_map_multi(synthetic_ohlcv: pd.DataFrame) -> dict[str, pd.DataFrame]:
    np.random.seed(123)
    codes = ["000001.SZ", "600036.SH", "000651.SZ", "601318.SH", "600519.SH"]
    dm: dict[str, pd.DataFrame] = {}
    for i, code in enumerate(codes):
        df = synthetic_ohlcv.copy()
        df["close"] = df["close"] * (1 + (i - 2) * 0.002)
        dm[code] = df
    return dm
