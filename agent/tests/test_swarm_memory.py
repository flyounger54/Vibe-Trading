"""Tests for swarm cross-run memory (Phase 4 of deep fusion)."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.swarm.memory import (
    _extract_rating,
    _safe_key,
    is_memory_enabled,
    load_history_context,
    record_decision,
    resolve_memory_key,
)


class TestSafeKey:
    def test_simple_symbol(self):
        assert _safe_key("600519.SH") == "600519.SH"

    def test_strips_special_chars(self):
        assert "/" not in _safe_key("BTC/USDT")

    def test_truncates_long_input(self):
        assert len(_safe_key("A" * 200)) <= 80


class TestResolveMemoryKey:
    def test_target_takes_priority(self):
        key = resolve_memory_key(
            {"target": "600519.SH", "market": "A-shares"},
            "investment_committee",
        )
        assert "600519" in key

    def test_falls_back_to_preset_market(self):
        key = resolve_memory_key(
            {"market": "A-shares"},
            "sentiment_intelligence_team",
        )
        assert "sentiment" in key
        assert "A-shares" in key

    def test_falls_back_to_preset_date(self):
        key = resolve_memory_key({}, "risk_committee")
        assert "risk_committee" in key


class TestExtractRating:
    def test_english_buy(self):
        assert _extract_rating("We recommend a Buy on this stock") == "buy"

    def test_english_hold(self):
        assert _extract_rating("Our stance remains Hold") == "hold"

    def test_chinese_rating(self):
        assert _extract_rating("综合评级：买入") == "买入"

    def test_long_short(self):
        assert _extract_rating("Final decision: long the position") == "long"

    def test_no_rating(self):
        assert _extract_rating("Analysis complete.") == "unrated"


class TestRecordAndLoad:
    def test_record_creates_file(self, tmp_path):
        record_decision(
            swarm_root=tmp_path,
            memory_key="600519.SH",
            preset_name="investment_committee",
            final_report="Final PM decision: Buy at 1800. Strong fundamentals.",
            agents_roles=["Portfolio Manager", "Risk Officer"],
        )
        mem_file = tmp_path / "memory" / "600519.SH.md"
        assert mem_file.exists()
        content = mem_file.read_text()
        assert "investment_committee" in content
        assert "buy" in content.lower()
        assert "ENTRY_END" in content

    def test_load_returns_empty_for_no_history(self, tmp_path):
        assert load_history_context(tmp_path, "AAPL.US") == ""

    def test_record_then_load(self, tmp_path):
        record_decision(
            swarm_root=tmp_path,
            memory_key="AAPL.US",
            preset_name="investment_committee",
            final_report="Buy AAPL at 180. Strong AI tailwind.",
            agents_roles=["Portfolio Manager"],
        )
        ctx = load_history_context(tmp_path, "AAPL.US")
        assert "Historical Context" in ctx
        assert "AAPL" in ctx or "investment_committee" in ctx
        assert "1 time(s)" in ctx

    def test_multiple_records_accumulate(self, tmp_path):
        for i in range(3):
            record_decision(
                swarm_root=tmp_path,
                memory_key="BTC-USDT",
                preset_name="crypto_trading_desk",
                final_report=f"Decision {i}: Hold position.",
                agents_roles=["Desk Risk Manager"],
            )
        ctx = load_history_context(tmp_path, "BTC-USDT")
        assert "3 time(s)" in ctx

    def test_analysis_type_preset(self, tmp_path):
        record_decision(
            swarm_root=tmp_path,
            memory_key="A-shares",
            preset_name="sentiment_intelligence_team",
            final_report="Composite sentiment: +45, moderately bullish.",
            agents_roles=["Signal Synthesizer"],
        )
        mem_file = tmp_path / "memory" / "A-shares.md"
        content = mem_file.read_text()
        assert "analysis" in content

    def test_max_history_entries_capped(self, tmp_path):
        for i in range(10):
            record_decision(
                swarm_root=tmp_path,
                memory_key="test_cap",
                preset_name="test",
                final_report=f"Decision {i}",
                agents_roles=["Manager"],
            )
        ctx = load_history_context(tmp_path, "test_cap")
        assert "10 time(s)" in ctx
        assert "most recent 5" in ctx


class TestIsMemoryEnabled:
    def test_default_off(self, monkeypatch):
        monkeypatch.delenv("SWARM_MEMORY", raising=False)
        assert is_memory_enabled() is False

    def test_explicit_on(self, monkeypatch):
        monkeypatch.setenv("SWARM_MEMORY", "on")
        assert is_memory_enabled() is True

    def test_explicit_off(self, monkeypatch):
        monkeypatch.setenv("SWARM_MEMORY", "off")
        assert is_memory_enabled() is False
