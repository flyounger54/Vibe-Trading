"""Tests for strategy zoo CLI handlers."""

from __future__ import annotations

from src.strategies.cli_handlers import (
    handle_strategy_info,
    handle_strategy_list,
    handle_strategy_recommend,
)
from src.strategies.registry import StrategyRegistry


class TestHandleStrategyList:
    def test_list_all(self) -> None:
        out = handle_strategy_list()
        assert f"Found {len(StrategyRegistry().list())} strategies" in out
        assert "trend_dual_ma" in out

    def test_list_filtered(self) -> None:
        out = handle_strategy_list(category="trend")
        assert "Found" in out
        assert "trend_dual_ma" in out
        assert "mr_bollinger" not in out

    def test_list_no_results(self) -> None:
        out = handle_strategy_list(universe="nonexistent")
        assert "No strategies found" in out


class TestHandleStrategyInfo:
    def test_info_returns_json(self) -> None:
        out = handle_strategy_info("trend_dual_ma")
        assert '"id": "trend_dual_ma"' in out
        assert '"nickname"' in out


class TestHandleStrategyRecommend:
    def test_recommend(self) -> None:
        out = handle_strategy_recommend(universe="equity_cn", risk="low")
        assert "Recommended" in out

    def test_recommend_no_match(self) -> None:
        out = handle_strategy_recommend(universe="nonexistent")
        assert "No recommendations" in out
