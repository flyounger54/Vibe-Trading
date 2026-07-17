"""Tests for swarm auto-quality scoring (Phase 1 of deep fusion)."""

import os

import pytest

from src.swarm.quality import (
    build_quality_feedback,
    format_quality_summary,
    is_quality_scoring_enabled,
    score_report,
)


class TestScoreReport:
    def test_empty_report_gets_f(self):
        grade, issues = score_report("")
        assert grade == "F"

    def test_very_short_report_gets_f(self):
        grade, issues = score_report("too short")
        assert grade == "F"

    def test_short_report_without_table_gets_b_or_c(self):
        report = "This is a basic analysis " * 15
        grade, issues = score_report(report)
        assert grade in ("B", "C")
        assert any("table" in i for i in issues)

    def test_good_report_gets_a(self):
        report = (
            "# Macro Analysis Report\n\n"
            "GDP growth rate is 5.2%, CPI at 2.1%.\n\n"
            "| Indicator | Value | Trend |\n"
            "|-----------|-------|-------|\n"
            "| GDP | 5.2% | Up |\n"
            "| CPI | 2.1% | Stable |\n"
            "| PMI | 51.3 | Expanding |\n\n"
            "The macro environment remains supportive with solid growth "
            "and contained inflation. Monetary policy is expected to stay "
            "accommodative through Q3 2026."
        )
        grade, issues = score_report(report)
        assert grade == "A"
        assert issues == []

    def test_failure_markers_detected(self):
        report = (
            "Attempted to analyze the data. unable to fetch data from the API. "
            "tool error encountered. rate limited by server. "
            "The analysis is incomplete due to access denied errors." * 2
        )
        grade, issues = score_report(report)
        assert any("fetch failure" in i or "data fetch" in i for i in issues)

    def test_multiple_failures_and_short_gets_d(self):
        report = "unable to fetch data. tool error occurred. rate limited."
        grade, _ = score_report(report)
        assert grade in ("D", "F")

    def test_missing_data_markers_counted(self):
        report = (
            "Analysis of the sector:\n"
            "| Metric | Value |\n|---|---|\n"
            "| PE | [数据缺失] |\n"
            "| PB | [数据缺失] |\n"
            "| ROE | [数据缺失] |\n"
            "Overall the sector shows mixed signals." * 5
        )
        grade, issues = score_report(report)
        assert any("missing" in i for i in issues)

    def test_quant_agent_lenient_on_tables(self):
        report = (
            "Backtest results: Sharpe 1.5, MaxDD -12.3%, "
            "Win rate 58.2%, Profit factor 2.1. "
            "Total trades: 342 over 252 trading days. "
            "Alpha vs benchmark: 8.7% annualized. " * 3
        )
        grade_quant, issues_quant = score_report(report, agent_id="backtester")
        grade_regular, issues_regular = score_report(report, agent_id="macro_analyst")
        assert "no summary table" not in " ".join(issues_quant)
        assert "no summary table" in " ".join(issues_regular) or "table" in " ".join(issues_regular)

    def test_quant_agent_short_threshold_lower(self):
        report = "Sharpe=1.2 MaxDD=-15% WinRate=55% Trades=200 Alpha=5.3%"
        grade_quant, issues_quant = score_report(report, agent_id="factor_miner")
        grade_regular, issues_regular = score_report(report, agent_id="macro_analyst")
        assert len(issues_regular) > len(issues_quant)


class TestFormatQualitySummary:
    def test_empty_grades(self):
        assert format_quality_summary({}) == ""

    def test_all_a_grades(self):
        grades = {
            "macro": ("A", []),
            "technical": ("A", []),
        }
        result = format_quality_summary(grades)
        assert "## Data Quality Summary" in result
        assert "macro" in result
        assert "⚠️" not in result

    def test_d_grade_triggers_warning(self):
        grades = {
            "macro": ("A", []),
            "flow": ("D", ["report too short"]),
        }
        result = format_quality_summary(grades)
        assert "⚠️" in result
        assert "flow" in result
        assert "reduce its weight" in result


class TestBuildQualityFeedback:
    def test_includes_grade_and_issues(self):
        feedback = build_quality_feedback("D", ["report too short", "no table"])
        assert "D" in feedback
        assert "report too short" in feedback
        assert "no table" in feedback
        assert "summary table" in feedback


class TestIsQualityScoringEnabled:
    def test_default_is_on(self, monkeypatch):
        monkeypatch.delenv("SWARM_QUALITY_SCORING", raising=False)
        assert is_quality_scoring_enabled() is True

    def test_explicit_on(self, monkeypatch):
        monkeypatch.setenv("SWARM_QUALITY_SCORING", "on")
        assert is_quality_scoring_enabled() is True

    def test_explicit_off(self, monkeypatch):
        monkeypatch.setenv("SWARM_QUALITY_SCORING", "off")
        assert is_quality_scoring_enabled() is False

    def test_numeric_true(self, monkeypatch):
        monkeypatch.setenv("SWARM_QUALITY_SCORING", "1")
        assert is_quality_scoring_enabled() is True
