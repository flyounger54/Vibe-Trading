"""Node 12B shared paper/live market and account risk checks."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.live import paths as live_paths
from src.live.enforcement import OrderIntent
from src.live.execution_risk import (
    MarketSnapshot,
    check_execution_risk,
    normalize_account_equity_usd,
    normalize_order_notional,
    normalize_quote,
    observe_daily_loss,
    open_order_reservations_usd,
)
from src.live.mandate.model import ExecutionControls, InstrumentType

pytestmark = pytest.mark.unit

NOW = datetime(2026, 7, 18, 2, 0, tzinfo=timezone.utc)


def _controls(**overrides) -> ExecutionControls:
    values = {
        "max_daily_loss_usd": 500.0,
        "max_price_deviation_bps": 100.0,
        "max_quote_age_seconds": 30.0,
        "max_clock_drift_seconds": 5.0,
    }
    values.update(overrides)
    return ExecutionControls(**values)


def _intent(**overrides) -> OrderIntent:
    values = {
        "symbol": "AAPL",
        "side": "buy",
        "notional_usd": None,
        "quantity": 2.0,
        "instrument_type": InstrumentType.EQUITY,
        "client_order_id": "vt_order_1001",
        "order_type": "limit",
        "limit_price": 101.0,
    }
    values.update(overrides)
    return OrderIntent(**values)


def _snapshot(**overrides) -> MarketSnapshot:
    values = {
        "symbol": "AAPL",
        "bid_usd": 99.0,
        "ask_usd": 101.0,
        "last_usd": 100.0,
        "source_ts": NOW - timedelta(seconds=2),
        "observed_ts": NOW,
        "source_currency": "USD",
        "fx_rate_to_usd": 1.0,
    }
    values.update(overrides)
    return MarketSnapshot(**values)


def test_quote_normalization_requires_source_time_and_fx() -> None:
    quote = normalize_quote(
        {
            "status": "ok",
            "symbol": "700.HK",
            "quote": {
                "bid": 349.8,
                "ask": 350.2,
                "last": 350.0,
                "time": "2026-07-18T01:59:58Z",
                "currency": "HKD",
                "fx_rate_to_usd": 0.1275,
                "fx_time": "2026-07-18T01:59:57Z",
            },
        },
        symbol="700.HK",
        observed_at=NOW,
    )
    assert quote is not None
    assert quote.last_usd == pytest.approx(44.625)

    assert normalize_quote(
        {"status": "ok", "quote": {"last": 350.0, "currency": "HKD"}},
        symbol="700.HK",
        observed_at=NOW,
    ) is None


@pytest.mark.parametrize(
    ("snapshot", "expected"),
    [
        (_snapshot(source_ts=NOW - timedelta(seconds=31)), "stale_market_data"),
        (_snapshot(source_ts=NOW + timedelta(seconds=6)), "clock_drift"),
        (_snapshot(), None),
    ],
)
def test_stale_market_and_clock_drift(snapshot: MarketSnapshot, expected: str | None) -> None:
    breach = check_execution_risk(_controls(), _intent(), snapshot, now=NOW)
    assert (breach.code if breach else None) == expected


def test_limit_deviation_and_market_spread_are_bounded() -> None:
    limit_breach = check_execution_risk(
        _controls(max_price_deviation_bps=50),
        _intent(limit_price=102.0),
        _snapshot(),
        now=NOW,
    )
    assert limit_breach is not None and limit_breach.code == "price_deviation"

    market_breach = check_execution_risk(
        _controls(max_price_deviation_bps=50),
        _intent(order_type="market", limit_price=None),
        _snapshot(bid_usd=99.0, ask_usd=101.0),
        now=NOW,
    )
    assert market_breach is not None and market_breach.code == "price_deviation"


def test_notional_and_open_order_reservations_are_usd_normalized() -> None:
    normalized = normalize_order_notional(_intent(), _snapshot())
    assert normalized is not None and normalized.notional_usd == pytest.approx(202.0)

    reserved = open_order_reservations_usd(
        {
            "status": "ok",
            "open_orders": [
                {"symbol": "AAPL", "remaining_quantity": 3, "limit_price": 10},
                {"remaining_notional_usd": 20},
            ],
        }
    )
    assert reserved == pytest.approx(50.0)
    assert open_order_reservations_usd({"open_orders": [{"quantity": 1}]}) is None

    hkd_reserved = open_order_reservations_usd(
        {
            "open_orders": [
                {
                    "symbol": "700.HK",
                    "remaining_quantity": 10,
                    "limit_price": 350,
                    "fx_rate_to_usd": 0.1275,
                    "fx_time": "2026-07-18T01:59:58Z",
                }
            ]
        },
        now=NOW,
    )
    assert hkd_reserved == pytest.approx(446.25)
    assert open_order_reservations_usd(
        {
            "open_orders": [
                {
                    "symbol": "700.HK",
                    "remaining_quantity": 10,
                    "limit_price": 350,
                    "fx_rate_to_usd": 0.1275,
                }
            ]
        },
        now=NOW,
    ) is None


def test_daily_loss_state_persists_across_restart(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(live_paths, "get_runtime_root", lambda: tmp_path)
    first = observe_daily_loss("alpaca", "live:acct-1", 10_000.0, now=NOW)
    later = observe_daily_loss("alpaca", "live:acct-1", 9_400.0, now=NOW + timedelta(hours=2))

    assert first == 0.0
    assert later == pytest.approx(600.0)


def test_daily_loss_and_account_equity_fail_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(live_paths, "get_runtime_root", lambda: tmp_path)
    equity = normalize_account_equity_usd(
        {"status": "ok", "account": {"equity": "9500", "currency": "USD"}}
    )
    assert equity == 9500.0
    assert normalize_account_equity_usd(
        {"status": "ok", "account": {"equity": "9500", "currency": "HKD"}}
    ) is None

    observe_daily_loss("alpaca", "live:acct-1", 10_000.0, now=NOW)
    loss = observe_daily_loss("alpaca", "live:acct-1", equity, now=NOW + timedelta(hours=1))
    breach = check_execution_risk(_controls(max_daily_loss_usd=400), _intent(), _snapshot(), now=NOW, daily_loss_usd=loss)
    assert breach is not None and breach.code == "max_daily_loss_usd"
    at_limit = check_execution_risk(
        _controls(max_daily_loss_usd=500),
        _intent(),
        _snapshot(),
        now=NOW,
        daily_loss_usd=500,
    )
    assert at_limit is not None and at_limit.code == "max_daily_loss_usd"
