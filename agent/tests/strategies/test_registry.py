"""Tests for strategy registry: scanning, filtering, loading, health."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.strategies.base import Strategy
from src.strategies.registry import (
    StrategyMeta,
    StrategyRegistry,
    get_default_registry,
    load_strategy_meta_from_py,
    reset_default_registry,
)


class TestRegistryScan:
    def test_loaded_count_matches_discovered_strategies(self, registry: StrategyRegistry) -> None:
        assert registry.health()["loaded"] == len(registry.list())
        assert registry.list()

    def test_zero_failures(self, registry: StrategyRegistry) -> None:
        assert registry.health()["failed"] == 0

    def test_all_discovered_categories_are_present_in_manifest(self, registry: StrategyRegistry) -> None:
        cats = {registry.get(sid).category for sid in registry.list()}
        manifest_categories = {item["category"] for item in registry.export_manifest()["categories"]}
        assert cats == manifest_categories

    def test_list_returns_sorted(self, registry: StrategyRegistry) -> None:
        ids = registry.list()
        assert ids == sorted(ids)


class TestRegistryFilter:
    def test_filter_by_category(self, registry: StrategyRegistry) -> None:
        trend = registry.list(category="trend")
        assert trend
        assert all(registry.get(s).category == "trend" for s in trend)

    def test_filter_by_universe(self, registry: StrategyRegistry) -> None:
        crypto = registry.list(universe="crypto")
        assert len(crypto) > 0
        for sid in crypto:
            assert "crypto" in registry.get(sid).meta["universe"]

    def test_filter_by_risk(self, registry: StrategyRegistry) -> None:
        low = registry.list(risk="low")
        assert len(low) > 0
        for sid in low:
            assert registry.get(sid).meta["risk_profile"] == "low"

    def test_combined_filter(self, registry: StrategyRegistry) -> None:
        result = registry.list(category="trend", risk="low")
        assert "trend_dual_ma" in result

    def test_empty_filter_returns_all(self, registry: StrategyRegistry) -> None:
        assert registry.list() == registry.list(category=None, universe=None, risk=None)


class TestRegistryGet:
    def test_get_existing(self, registry: StrategyRegistry) -> None:
        s = registry.get("trend_dual_ma")
        assert isinstance(s, Strategy)
        assert s.id == "trend_dual_ma"
        assert s.category == "trend"

    def test_get_nonexistent_raises(self, registry: StrategyRegistry) -> None:
        with pytest.raises(KeyError, match="not in registry"):
            registry.get("nonexistent_strategy")

    def test_get_source(self, registry: StrategyRegistry) -> None:
        source = registry.get_source("trend_dual_ma")
        assert "SignalEngine" in source
        assert "__strategy_meta__" in source


class TestRegistryLoad:
    def test_load_default_params(self, registry: StrategyRegistry) -> None:
        engine = registry.load("trend_dual_ma")
        assert hasattr(engine, "generate")

    def test_load_custom_params(self, registry: StrategyRegistry) -> None:
        engine = registry.load("trend_dual_ma", fast_period=10, slow_period=30)
        assert engine.fast_period == 10
        assert engine.slow_period == 30

    def test_load_nonexistent_raises(self, registry: StrategyRegistry) -> None:
        with pytest.raises(KeyError):
            registry.load("nonexistent_strategy")


class TestRegistryManifest:
    def test_export_manifest_structure(self, registry: StrategyRegistry) -> None:
        m = registry.export_manifest()
        assert "generated_at" in m
        assert "categories" in m
        assert "health" in m
        assert m["health"]["loaded"] == len(registry.list())

    def test_manifest_categories_complete(self, registry: StrategyRegistry) -> None:
        m = registry.export_manifest()
        cat_ids = {c["category"] for c in m["categories"]}
        assert cat_ids == {registry.get(sid).category for sid in registry.list()}


class TestSingleton:
    def test_singleton_returns_same_instance(self) -> None:
        reset_default_registry()
        r1 = get_default_registry()
        r2 = get_default_registry()
        assert r1 is r2
        reset_default_registry()


class TestMetaAstParsing:
    def test_all_metas_are_ast_parseable(self) -> None:
        zoo_root = Path(__file__).resolve().parents[2] / "src" / "strategies" / "zoo"
        for cat_dir in sorted(zoo_root.iterdir()):
            if not cat_dir.is_dir() or cat_dir.name.startswith("_"):
                continue
            for py_file in sorted(cat_dir.glob("*.py")):
                if py_file.name.startswith("_"):
                    continue
                meta = load_strategy_meta_from_py(py_file)
                assert isinstance(meta, StrategyMeta), f"{py_file.name}: not StrategyMeta"
