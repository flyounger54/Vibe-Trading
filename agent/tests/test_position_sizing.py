"""Tests for the position_sizing module: models, protocol, pipeline, sizers, stops."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest.models import Position, TradeRecord
from backtest.position_sizing.models import SizingContext, SizingResult, StopState
from backtest.position_sizing.protocol import PositionSizer
from backtest.position_sizing.pipeline import SizerPipeline
from backtest.position_sizing.fixed_fractional import FixedFractionalSizer
from backtest.position_sizing.atr_sizing import ATRSizer
from backtest.position_sizing.equal_weight import EqualWeightSizer
from backtest.position_sizing.stops import (
    ATRStopSizer,
    FixedStopSizer,
    TrailingStopSizer,
    TimeExitSizer,
    ProfitTargetSizer,
)
from backtest.position_sizing.loader import load_position_sizer


# ── Helpers ──

def _make_history(n: int = 30, base_price: float = 100.0) -> pd.DataFrame:
    dates = pd.bdate_range("2025-01-01", periods=n)
    close = np.linspace(base_price, base_price * 1.1, n)
    return pd.DataFrame({
        "open": close * 0.999,
        "high": close * 1.01,
        "low": close * 0.99,
        "close": close,
        "volume": np.ones(n) * 1000,
    }, index=dates)


def _make_ctx(
    signal_weight: float = 0.5,
    equity: float = 100_000,
    price: float = 50.0,
    n_positions: int = 0,
    history: pd.DataFrame | None = None,
) -> SizingContext:
    ts = pd.Timestamp("2025-06-01")
    bar = pd.Series({"open": price, "high": price * 1.01, "low": price * 0.99, "close": price, "volume": 1000})
    positions = tuple(
        Position(symbol=f"SYM{i}", direction=1, entry_price=price, entry_time=ts, size=100)
        for i in range(n_positions)
    )
    return SizingContext(
        timestamp=ts,
        symbol="TEST",
        signal_weight=signal_weight,
        current_price=price,
        bar=bar,
        equity=equity,
        capital=equity * 0.5,
        initial_equity=100_000,
        current_position=None,
        current_weight=0.0,
        all_positions=positions,
        total_exposure=0.0,
        recent_trades=(),
        peak_equity=equity,
        bar_idx=10,
        price_history=history if history is not None else _make_history(),
    )


# ── Protocol compliance ──

class TestProtocol:
    def test_fixed_fractional_satisfies_protocol(self) -> None:
        assert isinstance(FixedFractionalSizer(), PositionSizer)

    def test_atr_sizer_satisfies_protocol(self) -> None:
        assert isinstance(ATRSizer(), PositionSizer)

    def test_equal_weight_satisfies_protocol(self) -> None:
        assert isinstance(EqualWeightSizer(), PositionSizer)

    def test_pipeline_satisfies_protocol(self) -> None:
        pipeline = SizerPipeline([FixedFractionalSizer()])
        assert isinstance(pipeline, PositionSizer)

    def test_stop_sizers_satisfy_protocol(self) -> None:
        for cls in (ATRStopSizer, FixedStopSizer, TrailingStopSizer, TimeExitSizer, ProfitTargetSizer):
            assert isinstance(cls(), PositionSizer)


# ── Fixed Fractional ──

class TestFixedFractional:
    def test_basic_sizing(self) -> None:
        sizer = FixedFractionalSizer(risk_per_trade=0.02, max_position_pct=0.25)
        ctx = _make_ctx(signal_weight=1.0, equity=100_000, price=50.0)
        result = sizer.size(ctx)

        assert result.target_weight > 0
        assert abs(result.target_weight) <= 0.25

    def test_zero_signal_returns_zero(self) -> None:
        sizer = FixedFractionalSizer()
        ctx = _make_ctx(signal_weight=0.0)
        result = sizer.size(ctx)
        assert result.target_weight == 0.0

    def test_respects_max_position(self) -> None:
        sizer = FixedFractionalSizer(risk_per_trade=0.5, max_position_pct=0.10)
        ctx = _make_ctx(signal_weight=1.0, equity=100_000)
        result = sizer.size(ctx)
        assert abs(result.target_weight) <= 0.10 + 1e-9

    def test_short_signal(self) -> None:
        sizer = FixedFractionalSizer()
        ctx = _make_ctx(signal_weight=-0.8)
        result = sizer.size(ctx)
        assert result.target_weight < 0

    def test_invalid_params(self) -> None:
        with pytest.raises(ValueError):
            FixedFractionalSizer(risk_per_trade=0)
        with pytest.raises(ValueError):
            FixedFractionalSizer(max_position_pct=1.5)


# ── ATR Sizing ──

class TestATRSizing:
    def test_with_history(self) -> None:
        sizer = ATRSizer(atr_period=20, risk_per_unit=0.01)
        ctx = _make_ctx(signal_weight=1.0, equity=100_000)
        result = sizer.size(ctx)
        assert result.target_weight > 0
        assert "atr" in result.metadata

    def test_no_history(self) -> None:
        sizer = ATRSizer(atr_period=20)
        ctx = _make_ctx(signal_weight=1.0, history=pd.DataFrame())
        result = sizer.size(ctx)
        assert result.target_weight == 1.0  # falls through unchanged

    def test_invalid_period(self) -> None:
        with pytest.raises(ValueError):
            ATRSizer(atr_period=1)


# ── Equal Weight ──

class TestEqualWeight:
    def test_single_position(self) -> None:
        sizer = EqualWeightSizer(max_position_pct=0.25)
        ctx = _make_ctx(signal_weight=1.0, n_positions=0)
        result = sizer.size(ctx)
        assert abs(result.target_weight) <= 0.25 + 1e-9

    def test_multiple_positions(self) -> None:
        sizer = EqualWeightSizer(max_position_pct=1.0)
        ctx = _make_ctx(signal_weight=1.0, n_positions=3)
        result = sizer.size(ctx)
        assert abs(result.target_weight) <= 0.25 + 1e-9  # 1/(3+1) = 0.25


# ── Stop Sizers ──

class TestStops:
    def test_fixed_stop_long(self) -> None:
        sizer = FixedStopSizer(stop_pct=0.05, profit_target_pct=0.10)
        ctx = _make_ctx(signal_weight=0.5, price=100.0)
        result = sizer.size(ctx)
        assert result.stop_loss == pytest.approx(95.0)
        assert result.take_profit == pytest.approx(110.0)
        assert result.target_weight == 0.5

    def test_fixed_stop_short(self) -> None:
        sizer = FixedStopSizer(stop_pct=0.05, profit_target_pct=0.10)
        ctx = _make_ctx(signal_weight=-0.5, price=100.0)
        result = sizer.size(ctx)
        assert result.stop_loss == pytest.approx(105.0)
        assert result.take_profit == pytest.approx(90.0)

    def test_atr_stop_sets_distance(self) -> None:
        sizer = ATRStopSizer(atr_multiplier=2.0, trailing=True)
        ctx = _make_ctx(signal_weight=0.5, price=100.0)
        result = sizer.size(ctx)
        assert result.stop_loss is not None
        assert result.trailing_stop_distance is not None

    def test_time_exit(self) -> None:
        sizer = TimeExitSizer(max_bars=15)
        ctx = _make_ctx(signal_weight=0.5)
        result = sizer.size(ctx)
        assert result.exit_time_bars == 15

    def test_profit_target(self) -> None:
        sizer = ProfitTargetSizer(target_pct=0.20)
        ctx = _make_ctx(signal_weight=0.5, price=100.0)
        result = sizer.size(ctx)
        assert result.take_profit == pytest.approx(120.0)


# ── Pipeline ──

class TestPipeline:
    def test_chains_weight_through_sizers(self) -> None:
        s1 = FixedFractionalSizer(risk_per_trade=0.02, max_position_pct=0.20)
        s2 = FixedStopSizer(stop_pct=0.05)
        pipeline = SizerPipeline([s1, s2])
        ctx = _make_ctx(signal_weight=1.0, equity=100_000)
        result = pipeline.size(ctx)

        assert abs(result.target_weight) <= 0.20 + 1e-9
        assert result.stop_loss is not None

    def test_tightest_stop_wins(self) -> None:
        s1 = FixedStopSizer(stop_pct=0.03)  # stop at 97
        s2 = FixedStopSizer(stop_pct=0.05)  # stop at 95
        pipeline = SizerPipeline([s1, s2])
        ctx = _make_ctx(signal_weight=0.5, price=100.0)
        result = pipeline.size(ctx)
        assert result.stop_loss == pytest.approx(97.0)  # tighter wins

    def test_empty_pipeline_raises(self) -> None:
        with pytest.raises(ValueError):
            SizerPipeline([])


# ── Loader ──

class TestLoader:
    def test_no_config_returns_none(self) -> None:
        assert load_position_sizer({}) is None
        assert load_position_sizer({"position_sizing": {}}) is None

    def test_single_sizer(self) -> None:
        config = {
            "position_sizing": {
                "sizers": [{"type": "fixed_fractional", "risk_per_trade": 0.02}],
            }
        }
        sizer = load_position_sizer(config)
        assert sizer is not None
        assert isinstance(sizer, PositionSizer)

    def test_pipeline_from_config(self) -> None:
        config = {
            "position_sizing": {
                "sizers": [
                    {"type": "fixed_fractional", "risk_per_trade": 0.02},
                    {"type": "equal_weight"},
                ],
                "stops": {"type": "atr_stop", "atr_multiplier": 2.0},
            }
        }
        sizer = load_position_sizer(config)
        assert sizer is not None
        assert isinstance(sizer, SizerPipeline)


# ── StopState evaluation ──

class TestStopEvaluation:
    def test_stop_loss_triggers_long(self) -> None:
        from backtest.engines.base import BaseEngine
        pos = Position(symbol="X", direction=1, entry_price=100, entry_time=pd.Timestamp("2025-01-01"), size=10)
        stop = StopState(symbol="X", stop_loss=95.0)
        assert BaseEngine._evaluate_stop(stop, pos, 94.0, 5) == "stop_loss"
        assert BaseEngine._evaluate_stop(stop, pos, 96.0, 5) is None

    def test_take_profit_triggers_long(self) -> None:
        from backtest.engines.base import BaseEngine
        pos = Position(symbol="X", direction=1, entry_price=100, entry_time=pd.Timestamp("2025-01-01"), size=10)
        stop = StopState(symbol="X", take_profit=110.0)
        assert BaseEngine._evaluate_stop(stop, pos, 111.0, 5) == "take_profit"
        assert BaseEngine._evaluate_stop(stop, pos, 109.0, 5) is None

    def test_time_exit_triggers(self) -> None:
        from backtest.engines.base import BaseEngine
        pos = Position(symbol="X", direction=1, entry_price=100, entry_time=pd.Timestamp("2025-01-01"), size=10)
        stop = StopState(symbol="X", exit_bar=10)
        assert BaseEngine._evaluate_stop(stop, pos, 100.0, 10) == "time_exit"
        assert BaseEngine._evaluate_stop(stop, pos, 100.0, 9) is None

    def test_stop_loss_triggers_short(self) -> None:
        from backtest.engines.base import BaseEngine
        pos = Position(symbol="X", direction=-1, entry_price=100, entry_time=pd.Timestamp("2025-01-01"), size=10)
        stop = StopState(symbol="X", stop_loss=105.0)
        assert BaseEngine._evaluate_stop(stop, pos, 106.0, 5) == "stop_loss"
        assert BaseEngine._evaluate_stop(stop, pos, 104.0, 5) is None
