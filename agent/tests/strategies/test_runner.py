"""Tests for strategy runner: run, compare, recommend."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.strategies.registry import StrategyRegistry
from src.strategies.runner import compare, recommend, run


@pytest.fixture()
def tmp_run_root(tmp_path: Path) -> Path:
    return tmp_path / "strategy_runs"


class TestRun:
    def test_run_creates_files(
        self, registry: StrategyRegistry, tmp_run_root: Path
    ) -> None:
        result = run(
            "trend_dual_ma",
            codes=["000001.SZ"],
            start_date="2020-01-01",
            end_date="2025-01-01",
            run_root=tmp_run_root,
            registry=registry,
        )
        assert result["status"] == "ready"
        run_dir = Path(result["run_dir"])
        assert (run_dir / "config.json").exists()
        assert (run_dir / "signal_engine.py").exists()
        assert (run_dir / "run_card.json").exists()

    def test_run_config_content(
        self, registry: StrategyRegistry, tmp_run_root: Path
    ) -> None:
        result = run(
            "mr_bollinger",
            codes=["600036.SH", "000651.SZ"],
            start_date="2022-01-01",
            end_date="2024-01-01",
            source="tushare",
            run_root=tmp_run_root,
            registry=registry,
        )
        config = json.loads(Path(result["run_dir"], "config.json").read_text())
        assert config["codes"] == ["600036.SH", "000651.SZ"]
        assert config["source"] == "tushare"
        assert config["start_date"] == "2022-01-01"

    def test_run_card_contains_strategy_id(
        self, registry: StrategyRegistry, tmp_run_root: Path
    ) -> None:
        result = run(
            "trend_turtle",
            codes=["BTC-USDT"],
            run_root=tmp_run_root,
            registry=registry,
        )
        card = json.loads(Path(result["run_dir"], "run_card.json").read_text())
        assert card["strategy_id"] == "trend_turtle"
        assert card["category"] == "trend"

    def test_run_with_custom_params(
        self, registry: StrategyRegistry, tmp_run_root: Path
    ) -> None:
        result = run(
            "trend_dual_ma",
            codes=["000001.SZ"],
            params={"fast_period": 10, "slow_period": 50},
            run_root=tmp_run_root,
            registry=registry,
        )
        card = json.loads(Path(result["run_dir"], "run_card.json").read_text())
        assert card["params"]["fast_period"] == 10
        assert card["params"]["slow_period"] == 50

    def test_run_nonexistent_strategy_raises(
        self, registry: StrategyRegistry, tmp_run_root: Path
    ) -> None:
        with pytest.raises(KeyError):
            run("nope", codes=["X"], run_root=tmp_run_root, registry=registry)


class TestCompare:
    def test_compare_creates_multiple_runs(
        self, registry: StrategyRegistry, tmp_run_root: Path
    ) -> None:
        results = compare(
            ["trend_dual_ma", "mr_bollinger"],
            codes=["000001.SZ"],
            run_root=tmp_run_root,
            registry=registry,
        )
        assert len(results) == 2
        assert results[0]["strategy_id"] == "trend_dual_ma"
        assert results[1]["strategy_id"] == "mr_bollinger"


class TestRecommend:
    def test_recommend_filters(self, registry: StrategyRegistry) -> None:
        recs = recommend(universe="equity_cn", risk="low", registry=registry)
        assert len(recs) > 0
        for meta in recs:
            assert "equity_cn" in meta["universe"]
            assert meta["risk_profile"] == "low"

    def test_recommend_empty(self, registry: StrategyRegistry) -> None:
        recs = recommend(universe="nonexistent", registry=registry)
        assert recs == []
