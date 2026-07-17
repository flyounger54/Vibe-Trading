"""Contract tests for the consolidated US/HK loader and Yahoo/Sina providers."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from backtest.loaders import global_loader
from backtest.loaders.global_loader import DataLoader
from backtest.loaders.providers import global_stock as provider


@pytest.fixture(autouse=True)
def bypass_loader_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        global_loader,
        "cached_loader_fetch",
        lambda **kwargs: kwargs["fetch"](),
    )


def _rows(close: float = 190.0) -> list[dict]:
    return [
        {
            "date": "2024-01-03",
            "open": 185,
            "high": 192,
            "low": 184,
            "close": close,
            "volume": 1000,
        }
    ]


def test_loader_metadata() -> None:
    loader = DataLoader()
    assert loader.name == "global"
    assert loader.markets == {"us_equity", "hk_equity"}
    assert loader.requires_auth is False
    assert loader.is_available() is True


@pytest.mark.parametrize(
    ("code", "us", "hk"),
    [
        ("AAPL.US", True, False),
        ("00700.hk", False, True),
        ("600519.SH", False, False),
        ("BTC-USDT", False, False),
    ],
)
def test_market_guards(code: str, us: bool, hk: bool) -> None:
    assert global_loader._is_us(code) is us
    assert global_loader._is_hk(code) is hk


def test_symbol_normalization() -> None:
    assert provider.normalize_yahoo_symbol("AAPL.US") == "AAPL"
    assert provider.normalize_yahoo_symbol("00700.HK") == "0700.HK"
    assert provider.normalize_sina_ticker("TSLA.US") == "TSLA"


@pytest.mark.parametrize("code", ["AAPL.US", "00700.HK"])
def test_yahoo_primary_path_for_supported_markets(code: str) -> None:
    with patch.object(global_loader, "stock_kline_yahoo", return_value=_rows()) as yahoo, patch.object(
        global_loader, "us_stock_kline_sina"
    ) as sina:
        result = DataLoader().fetch([code], "2024-01-01", "2024-01-31")
    assert result[code].iloc[0]["close"] == pytest.approx(190.0)
    yahoo.assert_called_once()
    sina.assert_not_called()


def test_sina_fallback_is_us_daily_only() -> None:
    with patch.object(global_loader, "stock_kline_yahoo", return_value=[]), patch.object(
        global_loader, "us_stock_kline_sina", return_value=_rows(188.0)
    ) as sina:
        result = DataLoader().fetch(["AAPL.US"], "2024-01-01", "2024-01-31")
    assert result["AAPL.US"].iloc[0]["close"] == pytest.approx(188.0)
    sina.assert_called_once_with("AAPL", "2024-01-01", "2024-01-31")


@pytest.mark.parametrize(
    ("code", "interval"), [("00700.HK", "1D"), ("AAPL.US", "5m")]
)
def test_sina_is_not_used_for_hk_or_intraday(code: str, interval: str) -> None:
    with patch.object(global_loader, "stock_kline_yahoo", return_value=[]), patch.object(
        global_loader, "us_stock_kline_sina"
    ) as sina:
        result = DataLoader().fetch(
            [code], "2024-01-01", "2024-01-31", interval=interval
        )
    assert result == {}
    sina.assert_not_called()


def test_unsupported_market_and_empty_batch_do_not_fetch() -> None:
    with patch.object(global_loader, "stock_kline_yahoo") as yahoo:
        assert DataLoader().fetch([], "2024-01-01", "2024-01-31") == {}
        assert DataLoader().fetch(["600519.SH"], "2024-01-01", "2024-01-31") == {}
    yahoo.assert_not_called()


def test_one_failed_symbol_does_not_abort_batch() -> None:
    def fake_yahoo(symbol: str, *_args: object) -> list[dict]:
        if symbol == "BAD":
            raise RuntimeError("boom")
        return _rows()

    with patch.object(global_loader, "stock_kline_yahoo", side_effect=fake_yahoo), patch.object(
        global_loader, "us_stock_kline_sina", return_value=[]
    ):
        result = DataLoader().fetch(
            ["BAD.US", "AAPL.US"], "2024-01-01", "2024-01-31"
        )
    assert set(result) == {"AAPL.US"}


def test_invalid_date_range_is_rejected_before_fetch() -> None:
    with patch.object(global_loader, "stock_kline_yahoo") as yahoo:
        with pytest.raises(ValueError):
            DataLoader().fetch(["AAPL.US"], "2024-02-01", "2024-01-01")
    yahoo.assert_not_called()


def test_yahoo_provider_uses_shared_throttled_client_and_exclusive_end() -> None:
    timestamp = int(
        datetime(2024, 1, 2, 0, 0, tzinfo=timezone.utc).timestamp()
    )
    with patch.object(
        provider.yahoo_client,
        "get_chart",
        return_value=[
            {
                "trade_date": timestamp,
                "open": 10.123,
                "high": 12.456,
                "low": 9.111,
                "close": 11.999,
                "volume": 1234.0,
            }
        ],
    ) as chart:
        rows = provider.stock_kline_yahoo(
            "AAPL", "2024-01-01", "2024-01-31", interval="1D"
        )
    assert rows == [
        {
            "date": "2024-01-02",
            "open": 10.123,
            "high": 12.456,
            "low": 9.111,
            "close": 11.999,
            "volume": 1234,
        }
    ]
    assert chart.call_args.kwargs["interval"] == "1d"
    assert chart.call_args.kwargs["period2"] == int(
        datetime(2024, 2, 1, tzinfo=timezone.utc).timestamp()
    )


def test_sina_provider_parses_jsonp_and_filters_dates() -> None:
    response = MagicMock()
    response.text = (
        'callback([{"d":"2024-01-02","o":"10","h":"12","l":"9",'
        '"c":"11","v":"1000"},{"d":"2023-12-31","o":"1",'
        '"h":"1","l":"1","c":"1","v":"1"}])'
    )
    with patch.object(provider.requests, "get", return_value=response):
        rows = provider.us_stock_kline_sina(
            "AAPL", "2024-01-01", "2024-01-31"
        )
    assert rows == [
        {
            "date": "2024-01-02",
            "open": 10.0,
            "high": 12.0,
            "low": 9.0,
            "close": 11.0,
            "volume": 1000,
        }
    ]
