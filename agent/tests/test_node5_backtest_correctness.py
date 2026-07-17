"""Node 5 correctness contracts: immutable inputs, timeline, stops, ledger, metrics."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest.data_bundle import DataBundle
from backtest.engines.base import BaseEngine
from backtest.manifest import write_run_manifest
from backtest.lookahead import LookaheadBiasError, assert_no_lookahead
from backtest.models import Position, TradeRecord
from backtest.metrics import calc_metrics, win_rate_and_stats
from backtest.position_sizing.models import SizingResult, StopState
from backtest.runner import build_data_bundle


class _NoFrictionEngine(BaseEngine):
    def can_execute(self, symbol, direction, bar):
        return True

    def round_size(self, raw_size, price):
        return raw_size

    def calc_commission(self, size, price, direction, is_open):
        return float(self.config.get("commission", 0.0))

    def apply_slippage(self, price, direction):
        return float(price)


def _bars(
    *,
    opens=(10.0, 10.0, 10.0),
    highs=(10.0, 10.0, 10.0),
    lows=(10.0, 10.0, 10.0),
    closes=(10.0, 10.0, 10.0),
    currency="USD",
) -> pd.DataFrame:
    frame = pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": 1000.0},
        index=pd.date_range("2025-01-01", periods=len(opens), tz="UTC"),
    )
    frame.index.name = "trade_date"
    frame.attrs["vibe_metadata"] = {"currency": currency, "provider": "fixture"}
    return frame


def test_data_bundle_is_immutable_and_fingerprint_is_content_addressed() -> None:
    source = _bars()
    bundle = DataBundle.from_frames({"A.US": source}, base_currency="USD")
    original_hash = bundle.fingerprint

    source.iloc[0, source.columns.get_loc("close")] = 999.0
    exposed = bundle.frame("A.US")
    exposed.iloc[0, exposed.columns.get_loc("close")] = 777.0

    assert bundle.frame("A.US").iloc[0]["close"] == 10.0
    assert bundle.fingerprint == original_hash
    assert bundle.currency("A.US") == "USD"


def test_runner_builds_bundle_with_exactly_one_loader_fetch() -> None:
    class CountingLoader:
        name = "fixture"

        def __init__(self):
            self.calls = 0

        def fetch(self, *args, **kwargs):
            self.calls += 1
            return {"A.US": _bars()}

    loader = CountingLoader()
    bundle = build_data_bundle(
        {
            "codes": ["A.US"],
            "start_date": "2025-01-01",
            "end_date": "2025-01-03",
            "interval": "1D",
            "base_currency": "USD",
        },
        loader,
    )
    assert loader.calls == 1
    assert bundle.symbols == ("A.US",)


def test_sizing_context_cannot_see_execution_bar_close_or_intraday_values() -> None:
    bars = _bars(
        opens=(10.0, 11.0, 12.0),
        highs=(10.5, 999.0, 12.5),
        lows=(9.5, 1.0, 11.5),
        closes=(10.0, 500.0, 12.0),
    )
    dates = bars.index
    close_df = pd.DataFrame({"A.US": bars["close"]}, index=dates)
    targets = pd.DataFrame({"A.US": [0.0, 1.0, 0.0]}, index=dates)

    class Recorder:
        contexts = []

        def size(self, ctx):
            self.contexts.append(ctx)
            return SizingResult(target_weight=ctx.signal_weight)

    recorder = Recorder()
    engine = _NoFrictionEngine({"initial_cash": 1000.0})
    engine._position_sizer = recorder
    engine._execute_bars(dates, {"A.US": bars}, close_df, targets, ["A.US"])

    entry_context = recorder.contexts[0]
    assert entry_context.timestamp == dates[0]  # decision at t close
    assert entry_context.current_price == 10.0
    assert entry_context.bar["close"] == 10.0
    assert entry_context.price_history.index.max() == dates[0]
    assert engine.trades[0].entry_time == dates[1]  # fill at t+1 open
    assert engine.trades[0].entry_price == 11.0


def test_intraday_stop_executes_at_threshold_not_bar_open() -> None:
    bars = _bars(
        opens=(100.0,), highs=(110.0,), lows=(94.0,), closes=(105.0,)
    )
    ts = bars.index[0]
    engine = _NoFrictionEngine({"initial_cash": 1000.0})
    engine.capital = 900.0
    engine.positions["A.US"] = Position("A.US", 1, 100.0, ts - pd.Timedelta(days=1), 1.0)
    engine._stop_tracker["A.US"] = StopState(symbol="A.US", stop_loss=95.0)

    engine._check_all_stops(pd.DataFrame({"A.US": [105.0]}, index=[ts]), {"A.US": bars}, ts)

    assert engine.trades[0].exit_reason == "stop_loss"
    assert engine.trades[0].exit_price == 95.0


def test_gap_stop_executes_at_open_and_stop_wins_same_bar_collision() -> None:
    gap = _bars(opens=(90.0,), highs=(110.0,), lows=(85.0,), closes=(100.0,))
    ts = gap.index[0]
    engine = _NoFrictionEngine({"initial_cash": 1000.0, "stop_collision_policy": "stop_first"})
    engine.capital = 900.0
    engine.positions["A.US"] = Position("A.US", 1, 100.0, ts - pd.Timedelta(days=1), 1.0)
    engine._stop_tracker["A.US"] = StopState(
        symbol="A.US", stop_loss=95.0, take_profit=105.0
    )
    engine._check_all_stops(pd.DataFrame({"A.US": [100.0]}, index=[ts]), {"A.US": gap}, ts)
    assert engine.trades[0].exit_price == 90.0
    assert engine.trades[0].exit_reason == "stop_loss"


def test_final_liquidation_precedes_last_snapshot_and_includes_fees() -> None:
    bars = _bars(
        opens=(10.0, 10.0), highs=(10.0, 20.0), lows=(10.0, 10.0), closes=(10.0, 20.0)
    )
    dates = bars.index
    engine = _NoFrictionEngine({"initial_cash": 1000.0, "commission": 5.0})
    targets = pd.DataFrame({"A.US": [0.0, 1.0]}, index=dates)
    engine._execute_bars(
        dates,
        {"A.US": bars},
        pd.DataFrame({"A.US": bars["close"]}, index=dates),
        targets,
        ["A.US"],
    )
    final = engine.equity_snapshots[-1]
    assert engine.positions == {}
    assert final.positions == 0
    assert final.unrealized == 0.0
    assert final.equity == pytest.approx(engine.capital)
    assert final.equity == pytest.approx(1985.0)


def test_trade_metrics_use_net_pnl_and_real_calendar_holding_days() -> None:
    trade = TradeRecord(
        symbol="A.US",
        direction=1,
        entry_price=100.0,
        exit_price=101.0,
        entry_time=pd.Timestamp("2025-01-01", tz="UTC"),
        exit_time=pd.Timestamp("2025-01-06", tz="UTC"),
        size=100.0,
        leverage=1.0,
        pnl=80.0,
        gross_pnl=100.0,
        pnl_pct=0.8,
        exit_reason="signal",
        holding_bars=2,
        holding_days=5.0,
        commission=20.0,
    )
    equity = pd.Series(
        [1000.0, 1080.0], index=pd.to_datetime(["2025-01-01", "2025-01-06"], utc=True)
    )
    metrics = calc_metrics(equity, [trade], 1000.0, 252)
    assert metrics["net_profit"] == 80.0
    assert metrics["gross_profit"] == 100.0
    assert metrics["total_commission"] == 20.0
    assert metrics["avg_holding_bars"] == 2.0
    assert metrics["avg_holding_days"] == 5.0


def test_multi_currency_bundle_requires_fx_and_converts_at_each_timestamp() -> None:
    usd = _bars(currency="USD")
    cny = _bars(currency="CNY")
    with pytest.raises(ValueError, match="CNY.*USD"):
        DataBundle.from_frames({"A.US": usd, "600519.SH": cny}, base_currency="USD")

    bundle = DataBundle.from_frames(
        {"A.US": usd, "600519.SH": cny},
        base_currency="USD",
        fx_rates={"CNY": {"2025-01-01": 0.14, "2025-01-02": 0.15, "2025-01-03": 0.16}},
    )
    assert bundle.fx_rate("600519.SH", usd.index[0]) == pytest.approx(0.14)
    assert bundle.fx_rate("600519.SH", usd.index[2]) == pytest.approx(0.16)


def test_multi_currency_trade_ledger_converts_margin_pnl_and_fees_at_event_time() -> None:
    bars = _bars(
        opens=(100.0, 100.0, 110.0),
        highs=(100.0, 100.0, 110.0),
        lows=(100.0, 100.0, 110.0),
        closes=(100.0, 100.0, 110.0),
        currency="CNY",
    )
    bundle = DataBundle.from_frames(
        {"600519.SH": bars},
        base_currency="USD",
        fx_rates={"CNY": {"2025-01-01": 0.14, "2025-01-02": 0.15, "2025-01-03": 0.16}},
    )
    engine = _NoFrictionEngine({"initial_cash": 2000.0, "base_currency": "USD"})
    engine._data_bundle = bundle
    dates = bars.index
    engine._execute_bars(
        dates,
        {"600519.SH": bars},
        pd.DataFrame({"600519.SH": bars["close"]}, index=dates),
        pd.DataFrame({"600519.SH": [0.0, 0.5, 0.0]}, index=dates),
        ["600519.SH"],
    )

    # Decision sizing: USD 1,000 / 0.14 / CNY 100 = 71.428571 shares.
    # Entry margin: CNY 7,142.857 * 0.15; exit proceeds: CNY 7,857.143 * 0.16.
    assert engine.capital == pytest.approx(2185.7142857)
    trade = engine.trades[0]
    assert trade.gross_pnl == pytest.approx(185.7142857)
    assert trade.pnl == pytest.approx(185.7142857)
    assert trade.currency == "CNY"
    assert trade.base_currency == "USD"
    assert trade.fx_rate == pytest.approx(0.16)


def test_run_manifest_is_identical_for_repeated_frozen_runs(tmp_path) -> None:
    bars = _bars()
    bundle = DataBundle.from_frames({"A.US": bars}, base_currency="USD")
    config = {"codes": ["A.US"], "initial_cash": 1000.0, "commission": 1.0}

    manifests = []
    for name in ("run-a", "run-b"):
        run_dir = tmp_path / name
        (run_dir / "artifacts").mkdir(parents=True)
        (run_dir / "code").mkdir()
        (run_dir / "artifacts" / "equity.csv").write_text("equity\n1000\n")
        strategy_path = run_dir / "code" / "signal_engine.py"
        strategy_path.write_text("class SignalEngine:\n    pass\n")
        manifests.append(
            write_run_manifest(run_dir, config, bundle, strategy_path=strategy_path)
        )

    assert manifests[0]["run_hash"] == manifests[1]["run_hash"]
    assert manifests[0]["components"] == manifests[1]["components"]

    changed = bars.copy()
    changed.iloc[-1, changed.columns.get_loc("close")] = 11.0
    changed_bundle = DataBundle.from_frames({"A.US": changed}, base_currency="USD")
    changed_manifest = write_run_manifest(
        tmp_path / "run-a", config, changed_bundle,
        strategy_path=tmp_path / "run-a" / "code" / "signal_engine.py",
    )
    assert changed_manifest["run_hash"] != manifests[0]["run_hash"]


def test_suspension_blocks_fills_and_volume_cap_creates_partial_fill() -> None:
    suspended = _bars(opens=(10.0, 10.0, 10.0))
    suspended.loc[suspended.index[1], "volume"] = 0.0
    dates = suspended.index
    engine = _NoFrictionEngine({"initial_cash": 1000.0})
    engine._execute_bars(
        dates,
        {"A.US": suspended},
        pd.DataFrame({"A.US": suspended["close"]}, index=dates),
        pd.DataFrame({"A.US": [0.0, 1.0, 0.0]}, index=dates),
        ["A.US"],
    )
    assert engine.trades == []

    liquid = _bars(opens=(10.0, 10.0, 10.0))
    liquid["volume"] = 10.0
    capped = _NoFrictionEngine({
        "initial_cash": 1000.0,
        "max_volume_participation": 0.5,
    })
    capped._execute_bars(
        dates,
        {"A.US": liquid},
        pd.DataFrame({"A.US": liquid["close"]}, index=dates),
        pd.DataFrame({"A.US": [0.0, 1.0, 0.0]}, index=dates),
        ["A.US"],
    )
    assert capped.trades[0].size == pytest.approx(5.0)
    assert capped.capital == pytest.approx(1000.0)


def test_explicit_delisting_closes_at_last_published_close() -> None:
    bars = _bars(
        opens=(10.0, 10.0, 12.0),
        highs=(10.0, 10.0, 12.0),
        lows=(10.0, 10.0, 12.0),
        closes=(10.0, 10.0, 12.0),
    )
    bars.attrs["vibe_metadata"] = {
        "currency": "USD",
        "provider": "fixture",
        "delisting_date": "2025-01-03",
    }
    dates = bars.index
    engine = _NoFrictionEngine({"initial_cash": 1000.0})
    engine._execute_bars(
        dates,
        {"A.US": bars},
        pd.DataFrame({"A.US": bars["close"]}, index=dates),
        pd.DataFrame({"A.US": [0.0, 1.0, 1.0]}, index=dates),
        ["A.US"],
    )
    assert engine.trades[0].exit_reason == "delisting"
    assert engine.trades[0].exit_price == pytest.approx(12.0)
    assert engine.positions == {}


def test_lookahead_sentinel_accepts_causal_signals_and_rejects_future_leaks() -> None:
    bars = _bars(closes=(10.0, 11.0, 12.0))
    data_map = {"A.US": bars}

    class Causal:
        def generate(self, frames):
            close = frames["A.US"]["close"]
            return {"A.US": close.pct_change().fillna(0.0)}

    causal = Causal()
    assert_no_lookahead(causal, data_map, causal.generate(data_map), max_checks=0)

    class Leaking:
        def generate(self, frames):
            close = frames["A.US"]["close"]
            return {"A.US": pd.Series(float(close.iloc[-1] > 100), index=close.index)}

    leaking = Leaking()
    with pytest.raises(LookaheadBiasError, match="future-data mutation"):
        assert_no_lookahead(
            leaking, data_map, leaking.generate(data_map), max_checks=0,
        )


@pytest.mark.parametrize("seed", range(10))
def test_randomized_cash_position_equity_conservation(seed: int) -> None:
    rng = np.random.default_rng(seed)
    close = 10.0 + rng.uniform(-2.0, 2.0, 20)
    bars = _bars(
        opens=tuple(close),
        highs=tuple(close + 0.5),
        lows=tuple(close - 0.5),
        closes=tuple(close),
    )
    dates = bars.index
    targets = rng.choice([-0.5, 0.0, 0.5], size=len(dates))
    targets[0] = 0.0
    engine = _NoFrictionEngine({
        "initial_cash": 10_000.0,
        "commission": 0.25,
        "leverage": 2.0,
    })
    engine._execute_bars(
        dates,
        {"A.US": bars},
        pd.DataFrame({"A.US": bars["close"]}, index=dates),
        pd.DataFrame({"A.US": targets}, index=dates),
        ["A.US"],
    )

    assert engine.positions == {}
    assert engine.equity_snapshots[-1].positions == 0
    assert engine.equity_snapshots[-1].equity == pytest.approx(engine.capital)
    assert engine.capital == pytest.approx(
        engine.initial_capital + sum(trade.pnl for trade in engine.trades),
        abs=1e-8,
    )


def test_short_leverage_and_partial_close_golden_ledger() -> None:
    engine = _NoFrictionEngine({"initial_cash": 1000.0, "leverage": 2.0})
    ts0 = pd.Timestamp("2025-01-01", tz="UTC")
    ts1 = pd.Timestamp("2025-01-02", tz="UTC")
    engine.capital = 500.0
    engine.positions["A.US"] = Position(
        "A.US", -1, 10.0, ts0, 100.0,
        leverage=2.0,
        entry_margin_local=500.0,
        entry_margin_base=500.0,
    )
    engine._bar_idx = 1
    engine._close_position("A.US", 8.0, ts1, "partial_close", size=40.0)
    assert engine.capital == pytest.approx(780.0)  # margin 200 + short PnL 80
    assert engine.positions["A.US"].size == pytest.approx(60.0)
    engine._close_position("A.US", 8.0, ts1, "signal")
    assert engine.capital == pytest.approx(1200.0)
    assert sum(trade.pnl for trade in engine.trades) == pytest.approx(200.0)


def test_data_bundle_validation_edges_and_constant_fx() -> None:
    with pytest.raises(ValueError, match="at least one"):
        DataBundle.from_frames({}, base_currency="USD")
    with pytest.raises(ValueError, match="base_currency"):
        DataBundle.from_frames({"A": _bars()}, base_currency="")
    with pytest.raises(ValueError, match="non-empty"):
        DataBundle.from_frames({"A": pd.DataFrame()}, base_currency="USD")

    cny = _bars(currency="CNY")
    constant = DataBundle.from_frames(
        {"600519.SH": cny}, base_currency="USD", fx_rates={"CNY/USD": 0.14},
    )
    assert constant.fx_rate("600519.SH", cny.index[0]) == pytest.approx(0.14)
    with pytest.raises(ValueError, match="must be positive"):
        DataBundle.from_frames(
            {"600519.SH": cny}, base_currency="USD", fx_rates={"CNY": 0.0},
        )
    with pytest.raises(ValueError, match="non-empty and positive"):
        DataBundle.from_frames(
            {"600519.SH": cny}, base_currency="USD", fx_rates={"CNY": {}},
        )
    with pytest.raises(ValueError, match="unsupported FX"):
        DataBundle.from_frames(
            {"600519.SH": cny}, base_currency="USD", fx_rates={"CNY": [0.14]},
        )
    dated = DataBundle.from_frames(
        {"600519.SH": cny},
        base_currency="USD",
        fx_rates={"CNY": {"2025-01-02": 0.15}},
    )
    with pytest.raises(ValueError, match="at or before"):
        dated.fx_rate("600519.SH", pd.Timestamp("2025-01-01", tz="UTC"))
    with pytest.raises(KeyError, match="not present"):
        constant.frame("MISSING")
    assert DataBundle.from_frames(
        {"A.US": _bars()}, base_currency="USD",
    ).fx_rate("A.US", pd.Timestamp("2025-01-01")) == 1.0


def test_lookahead_sentinel_validation_and_sampling_edges() -> None:
    one = {"A": _bars(opens=(1.0,), highs=(1.0,), lows=(1.0,), closes=(1.0,))}

    class OneBar:
        def generate(self, frames):
            return {"A": pd.Series(0.0, index=frames["A"].index)}

    engine = OneBar()
    assert_no_lookahead(engine, one, engine.generate(one))

    bars = _bars(
        opens=tuple(range(1, 13)), highs=tuple(range(1, 13)),
        lows=tuple(range(1, 13)), closes=tuple(range(1, 13)),
    )[["close"]]
    baseline = {"A": pd.Series(0.0, index=bars.index)}

    class InvalidMapping:
        def generate(self, frames):
            return []

    with pytest.raises(LookaheadBiasError, match="mapping"):
        assert_no_lookahead(InvalidMapping(), {"A": bars}, baseline, max_checks=2)

    class InvalidSeries:
        def generate(self, frames):
            return {"A": [0.0]}

    with pytest.raises(LookaheadBiasError, match="invalid signal"):
        assert_no_lookahead(InvalidSeries(), {"A": bars}, baseline, max_checks=2)


def test_calendar_metrics_and_all_stop_reason_metrics() -> None:
    reasons = ["stop_loss", "trailing_stop", "take_profit", "time_exit", "partial_close"]
    trades = []
    for index, reason in enumerate(reasons):
        trades.append(TradeRecord(
            symbol="A.US",
            direction=1,
            entry_price=10.0,
            exit_price=9.0,
            entry_time=pd.Timestamp("2025-01-01", tz="UTC"),
            exit_time=pd.Timestamp("2025-01-06", tz="UTC"),
            size=1.0,
            leverage=1.0,
            pnl=-float(index + 1),
            pnl_pct=-1.0,
            exit_reason=reason,
            holding_bars=0,
            commission=0.5,
        ))
    equity = pd.Series(
        [100.0, 95.0, 90.0],
        index=pd.to_datetime(["2025-01-01", "2025-01-06", "2025-01-11"], utc=True),
    )
    metrics = calc_metrics(equity, trades, 100.0, bars_per_year=None)
    assert metrics["stop_hit_rate"] == 1.0
    assert metrics["max_single_loss_pct"] == pytest.approx(-0.05)
    for reason in reasons:
        assert metrics[f"{reason}_count"] == 1
    assert metrics["gross_profit"] == pytest.approx(
        sum(trade.pnl + trade.commission for trade in trades)
    )

    malformed = TradeRecord(
        symbol="A", direction=1, entry_price=1, exit_price=1,
        entry_time="bad", exit_time="bad", size=1, leverage=1,
        pnl=0, pnl_pct=0, exit_reason="signal", holding_bars=0, commission=0,
    )
    assert win_rate_and_stats([malformed])["avg_holding_days"] == 0.0
