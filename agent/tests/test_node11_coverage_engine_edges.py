"""Dense correctness branches for the shared event-driven backtest engine."""

from __future__ import annotations

import pandas as pd
import pytest

from backtest.engines import base
from backtest.models import Position
from backtest.position_sizing.models import StopState


pytestmark = pytest.mark.unit
_TS = pd.Timestamp("2026-01-02")


class Engine(base.BaseEngine):
    def __init__(self, config: dict | None = None) -> None:
        self.allow = True
        self.force_zero_size = False
        super().__init__({"initial_cash": 1_000, **(config or {})})

    def can_execute(self, symbol: str, direction: int, bar: pd.Series) -> bool:
        return self.allow

    def round_size(self, raw_size: float, price: float) -> float:
        return 0.0 if self.force_zero_size else raw_size

    def calc_commission(
        self, size: float, price: float, direction: int, is_open: bool
    ) -> float:
        return 1.0

    def apply_slippage(self, price: float, direction: int) -> float:
        return price


def _bar(open_price: float = 10.0, **kwargs: object) -> pd.Series:
    return pd.Series({"open": open_price, "high": 12.0, "low": 8.0, "close": 10.0, "volume": 100.0, **kwargs}, name=_TS)


def _frame(bar: pd.Series | None = None) -> pd.DataFrame:
    value = bar if bar is not None else _bar()
    return pd.DataFrame([value.to_dict()], index=[_TS])


def _position(direction: int = 1, size: float = 10.0) -> Position:
    return Position(
        "A", direction, 10.0, _TS - pd.Timedelta(days=2), size,
        entry_bar_idx=0, entry_commission=1.0,
    )


def test_source_market_fundamental_event_and_cashflow_edges() -> None:
    assert base._run_card_data_sources({"_run_card_effective_sources": ["a", " "]}, object()) == ["a"]
    assert base._run_card_data_sources({"_run_card_effective_sources": " b "}, object()) == ["b"]
    assert base._run_card_data_sources({}, type("Loader", (), {"name": "loader"})()) == ["loader"]
    assert base._run_card_data_sources({"source": "global"}, object()) == ["global"]
    assert base._run_card_data_sources({}, object()) == []
    assert base._detect_market_for_align("BTC-USDT") == "crypto"
    assert base._detect_market_for_align("EUR/USD") == "forex"
    assert base._detect_market_for_align("AAPL") == "equity"

    assert base._normalise_fundamental_fields({}) == {}
    with pytest.raises(ValueError):
        base._normalise_fundamental_fields({"fundamental_fields": []})
    with pytest.raises(ValueError):
        base._normalise_fundamental_fields({"fundamental_fields": {1: ["x"]}})
    assert base._normalise_fundamental_fields({"fundamental_fields": {"daily": None}}) == {}
    with pytest.raises(ValueError):
        base._normalise_fundamental_fields({"fundamental_fields": {"daily": "pe"}})
    assert base._normalise_fundamental_fields({"fundamental_fields": {"daily": []}}) == {}
    with pytest.raises(ValueError):
        base._normalise_fundamental_fields({"fundamental_fields": {"daily": [""]}})
    assert base._normalise_fundamental_fields({"fundamental_fields": {" daily ": ["pe"]}}) == {"daily": ["pe"]}

    assert base._event_feed_specs({}) == []
    with pytest.raises(ValueError):
        base._event_feed_specs({"event_feeds": "bad"})
    assert base._parse_cash_flows({}) == {}
    flows = base._parse_cash_flows(
        {"position_sizing": {"cash_flows": [{"date": "2026-01-01", "amount": 1}, {"date": "2026-01-01T00:00:00Z", "amount": 2}]}}
    )
    assert list(flows.values()) == [3.0]
    for bad in ({"amount": 1}, {"date": "bad", "amount": 1}, {"date": "2026-01-01", "amount": float("nan")}):
        with pytest.raises(ValueError, match="invalid cash_flow"):
            base._parse_cash_flows({"position_sizing": {"cash_flows": [bad]}})


def test_engine_init_tradeability_and_fill_caps() -> None:
    for participation in (0, 1.1):
        with pytest.raises(ValueError, match="participation"):
            Engine({"max_volume_participation": participation})
    engine = Engine()
    assert not engine._bar_is_tradeable(_bar(is_suspended=True))
    assert not engine._bar_is_tradeable(_bar(volume=0))
    engine.config["zero_volume_is_suspension"] = False
    assert engine._bar_is_tradeable(_bar(volume=0))
    assert engine._cap_fill_size(5, _bar()) == 5

    engine = Engine({"max_volume_participation": 0.1})
    assert engine._cap_fill_size(50, _bar(volume=100)) == 10
    for volume in (None, float("nan"), -1):
        with pytest.raises(base.BacktestExecutionError, match="non-negative"):
            engine._cap_fill_size(5, _bar(volume=volume))


def test_stop_trigger_gap_intraday_collision_and_direction_matrix() -> None:
    engine = Engine()
    long = _position(1)
    short = _position(-1)
    assert engine._stop_trigger(StopState("A", exit_bar=2), long, _bar(), 2, "gap") == ("time_exit", 10.0)
    assert engine._stop_trigger(StopState("A", stop_loss=11), long, _bar(), 1, "gap")[0] == "stop_loss"
    assert engine._stop_trigger(StopState("A", stop_loss=9), short, _bar(), 1, "gap")[0] == "stop_loss"
    assert engine._stop_trigger(StopState("A", take_profit=9), long, _bar(), 1, "gap")[0] == "take_profit"
    assert engine._stop_trigger(StopState("A", take_profit=11), short, _bar(), 1, "gap")[0] == "take_profit"
    assert engine._stop_trigger(StopState("A", stop_loss=7), long, _bar(), 1, "gap") is None

    collision = StopState("A", stop_loss=9, take_profit=11)
    engine.config["stop_collision_policy"] = "take_profit_first"
    assert engine._stop_trigger(collision, long, _bar(), 1, "intraday") == ("take_profit", 11.0)
    engine.config["stop_collision_policy"] = "stop_first"
    assert engine._stop_trigger(collision, long, _bar(), 1, "intraday") == ("stop_loss", 9.0)
    engine.config["stop_collision_policy"] = "invalid"
    with pytest.raises(ValueError, match="collision"):
        engine._stop_trigger(collision, long, _bar(), 1, "intraday")
    assert engine._stop_trigger(StopState("A", stop_loss=9), long, _bar(), 1, "intraday")[0] == "stop_loss"
    assert engine._stop_trigger(StopState("A", take_profit=11), long, _bar(), 1, "intraday")[0] == "take_profit"
    assert engine._stop_trigger(StopState("A"), long, _bar(), 1, "intraday") is None


def test_rebalance_guard_incremental_and_opening_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = Engine()
    engine._rebalance("A", 0, _frame(), _TS, 1_000)
    engine._rebalance("A", 1, None, _TS, 1_000)
    engine._rebalance("A", 1, _frame(), _TS + pd.Timedelta(days=1), 1_000)
    engine._rebalance("A", 1, _frame(_bar(is_suspended=True)), _TS, 1_000)
    engine.allow = False
    engine._rebalance("A", 1, _frame(), _TS, 1_000)
    engine.allow = True
    engine._rebalance("A", 1, _frame(_bar(open_price=0)), _TS, 1_000)
    engine.force_zero_size = True
    engine._rebalance("A", 1, _frame(), _TS, 1_000)
    engine.force_zero_size = False
    engine._rebalance("A", 0.5, _frame(), _TS, 1_000)
    assert "A" in engine.positions

    engine.allow = False
    engine._rebalance("A", 0, _frame(), _TS, 1_000)
    assert "A" in engine.positions
    engine.allow = True
    engine._position_sizer = object()
    added: list[float] = []
    reduced: list[float] = []
    monkeypatch.setattr(engine, "_add_to_position", lambda symbol, delta, *args, **kwargs: added.append(delta))
    monkeypatch.setattr(engine, "_reduce_position", lambda symbol, delta, *args, **kwargs: reduced.append(delta))
    engine._rebalance("A", 0.8, _frame(), _TS, 1_000)
    engine._rebalance("A", 0.2, _frame(), _TS, 1_000)
    current_weight = engine._position_notional_base(engine.positions["A"], 10, _TS) / 1_000
    engine._rebalance("A", current_weight, _frame(), _TS, 1_000)
    assert added and reduced


def test_add_and_reduce_position_guard_and_terminal_branches() -> None:
    engine = Engine()
    engine._add_to_position("A", 0.1, _bar(), _TS, 1_000)
    engine.positions["A"] = _position()
    engine.allow = False
    engine._add_to_position("A", 0.1, _bar(), _TS, 1_000)
    engine.allow = True
    engine._add_to_position("A", 0.1, _bar(open_price=0), _TS, 1_000)
    engine.force_zero_size = True
    engine._add_to_position("A", 0.1, _bar(), _TS, 1_000)
    engine.force_zero_size = False
    engine.capital = 0
    engine._add_to_position("A", 0.1, _bar(), _TS, 1_000)
    engine.capital = 1_000
    before = engine.positions["A"].size
    engine._add_to_position("A", 0.1, _bar(), _TS, 1_000, decision_ts=_TS)
    assert engine.positions["A"].size > before

    engine._reduce_position("missing", 0.1, _bar(), _TS, 1_000)
    engine.allow = False
    engine._reduce_position("A", 0.1, _bar(), _TS, 1_000)
    engine.allow = True
    engine._reduce_position("A", 0.1, _bar(open_price=0), _TS, 1_000)
    engine.force_zero_size = True
    engine._reduce_position("A", 0.1, _bar(), _TS, 1_000)
    engine.force_zero_size = False
    engine._reduce_position("A", 0.05, _bar(), _TS, 1_000, decision_ts=_TS)
    assert engine.trades[-1].exit_reason == "partial_close"
    engine._reduce_position("A", 100, _bar(), _TS, 1_000)
    assert "A" not in engine.positions
