"""Contract tests for the consolidated A-share loader and its providers."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from backtest.loaders import astock_loader
from backtest.loaders.astock_loader import DataLoader
from backtest.loaders.providers import astock as provider


@pytest.fixture(autouse=True)
def bypass_loader_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep unit tests deterministic and away from the persistent cache."""
    monkeypatch.setattr(
        astock_loader,
        "cached_loader_fetch",
        lambda **kwargs: kwargs["fetch"](),
    )


def _rows(close: float = 11.0) -> list[dict]:
    return [
        {
            "date": "2024-01-03",
            "open": 10,
            "high": 12,
            "low": 9,
            "close": close,
            "volume": 1000,
        }
    ]


def test_loader_metadata() -> None:
    loader = DataLoader()
    assert loader.name == "astock"
    assert loader.markets == {"a_share"}
    assert loader.requires_auth is False
    assert loader.is_available() is True


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("600519.SH", True),
        ("000001.sz", True),
        ("830001.BJ", True),
        ("600519", True),
        ("AAPL.US", False),
        ("BTC-USDT", False),
    ],
)
def test_market_guard(code: str, expected: bool) -> None:
    assert astock_loader._is_a_share(code) is expected


def test_rows_are_normalized_and_sorted() -> None:
    rows = _rows() + [
        {
            "date": "2024-01-02",
            "open": "9",
            "high": "10",
            "low": "8",
            "close": "9.5",
            "volume": "500",
        }
    ]
    frame = astock_loader._rows_to_dataframe(rows)
    assert frame is not None
    assert list(frame.columns) == ["open", "high", "low", "close", "volume"]
    assert frame.index.name == "trade_date"
    assert frame.index.is_monotonic_increasing
    assert frame.iloc[0]["close"] == pytest.approx(9.5)


def test_empty_and_non_market_inputs_do_not_touch_providers() -> None:
    with patch.object(astock_loader, "mootdx_kline") as mootdx, patch.object(
        astock_loader, "tencent_kline"
    ) as tencent:
        assert DataLoader().fetch([], "2024-01-01", "2024-01-31") == {}
        assert DataLoader().fetch(["AAPL.US"], "2024-01-01", "2024-01-31") == {}
    mootdx.assert_not_called()
    tencent.assert_not_called()


def test_primary_mootdx_path_returns_canonical_frame() -> None:
    with patch.object(astock_loader, "mootdx_kline", return_value=_rows()) as mootdx, patch.object(
        astock_loader, "tencent_kline"
    ) as tencent:
        result = DataLoader().fetch(["600519.SH"], "2024-01-01", "2024-01-31")
    assert list(result) == ["600519.SH"]
    assert result["600519.SH"].iloc[0]["close"] == pytest.approx(11.0)
    mootdx.assert_called_once_with("600519", "2024-01-01", "2024-01-31", "1D")
    tencent.assert_not_called()


@pytest.mark.parametrize("primary", [[], RuntimeError("tcp unavailable")])
def test_tencent_is_used_when_mootdx_cannot_serve(primary: object) -> None:
    primary_patch = (
        patch.object(astock_loader, "mootdx_kline", return_value=primary)
        if isinstance(primary, list)
        else patch.object(astock_loader, "mootdx_kline", side_effect=primary)
    )
    with primary_patch, patch.object(
        astock_loader, "tencent_kline", return_value=_rows(12.5)
    ) as tencent:
        result = DataLoader().fetch(["600519.SH"], "2024-01-01", "2024-01-31")
    assert result["600519.SH"].iloc[0]["close"] == pytest.approx(12.5)
    tencent.assert_called_once_with("600519", "2024-01-01", "2024-01-31")


def test_one_failed_symbol_does_not_abort_batch() -> None:
    def fake_mootdx(symbol: str, *_args: object) -> list[dict]:
        if symbol == "000001":
            raise RuntimeError("boom")
        return _rows()

    with patch.object(astock_loader, "mootdx_kline", side_effect=fake_mootdx), patch.object(
        astock_loader, "tencent_kline", return_value=[]
    ):
        result = DataLoader().fetch(
            ["000001.SZ", "600519.SH"], "2024-01-01", "2024-01-31"
        )
    assert set(result) == {"600519.SH"}


def test_invalid_date_range_is_rejected_before_fetch() -> None:
    with patch.object(astock_loader, "mootdx_kline") as mootdx:
        with pytest.raises(ValueError):
            DataLoader().fetch(["600519.SH"], "2024-02-01", "2024-01-01")
    mootdx.assert_not_called()


def test_symbol_helpers_are_deterministic() -> None:
    assert provider.normalize_code("SH600519") == "600519"
    assert provider.normalize_code("000001.sz") == "000001"
    assert provider.get_prefix("600519") == "sh"
    assert provider.get_prefix("830001") == "bj"
    assert provider.get_prefix("000001") == "sz"


def test_tencent_provider_parses_qfq_payload_without_network() -> None:
    payload = {
        "data": {
            "sh600519": {
                "qfqday": [["2024-01-02", "10", "11", "12", "9", "1000"]]
            }
        }
    }
    response = MagicMock()
    response.read.return_value = json.dumps(payload).encode()
    response.__enter__.return_value = response
    with patch.object(provider.urllib.request, "urlopen", return_value=response) as urlopen:
        rows = provider.tencent_kline("600519", "2024-01-01", "2024-01-31")
    assert rows == [
        {
            "date": "2024-01-02",
            "open": 10.0,
            "high": 12.0,
            "low": 9.0,
            "close": 11.0,
            "volume": 1000.0,
        }
    ]
    assert "sh600519" in urlopen.call_args.args[0].full_url


def test_mootdx_daily_provider_normalizes_dataframe() -> None:
    client = MagicMock()
    client.get_k_data.return_value = pd.DataFrame(
        {"open": [10], "high": [12], "low": [9], "close": [11], "vol": [1000]},
        index=["2024-01-02"],
    )
    with patch.object(provider, "tdx_client", return_value=client):
        rows = provider.mootdx_kline("600519", "2024-01-01", "2024-01-31")
    assert rows[0]["close"] == pytest.approx(11.0)
    client.get_k_data.assert_called_once_with(
        code="600519", start_date="2024-01-01", end_date="2024-01-31"
    )


def test_mootdx_rejects_unknown_interval_without_connecting() -> None:
    with patch.object(provider, "tdx_client") as client:
        with pytest.raises(ValueError, match="Unsupported mootdx interval"):
            provider.mootdx_kline("600519", "2024-01-01", "2024-01-31", "3m")
    client.assert_not_called()
