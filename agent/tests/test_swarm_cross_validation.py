"""Tests for swarm auto-cross-validation (Phase 2 of deep fusion)."""

from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from src.swarm.cross_validation import (
    _parse_cv_response,
    cross_validate,
    format_cross_validation_block,
    get_processing_rules,
    is_cross_validation_enabled,
    run_debate_round,
)


class TestParseResponse:
    def test_valid_json(self):
        raw = json.dumps({
            "contradictions": [
                {
                    "agent_a": "flow",
                    "claim_a": "liquidity tightening",
                    "agent_b": "news",
                    "claim_b": "policy easing",
                    "severity": "high",
                }
            ],
            "blind_spots": ["geopolitical risk"],
            "consensus": ["AI sector bullish"],
        })
        result = _parse_cv_response(raw)
        assert result is not None
        assert len(result["contradictions"]) == 1
        assert result["contradictions"][0]["severity"] == "high"
        assert "geopolitical risk" in result["blind_spots"]

    def test_json_with_markdown_fences(self):
        raw = '```json\n{"contradictions": [], "blind_spots": [], "consensus": ["all agree"]}\n```'
        result = _parse_cv_response(raw)
        assert result is not None
        assert result["consensus"] == ["all agree"]

    def test_json_embedded_in_text(self):
        raw = 'Here is my analysis:\n{"contradictions": [], "blind_spots": ["FX risk"], "consensus": []}\nEnd.'
        result = _parse_cv_response(raw)
        assert result is not None
        assert result["blind_spots"] == ["FX risk"]

    def test_invalid_json_returns_none(self):
        assert _parse_cv_response("not json at all") is None

    def test_empty_string_returns_none(self):
        assert _parse_cv_response("") is None

    def test_defaults_missing_fields(self):
        result = _parse_cv_response('{"contradictions": []}')
        assert result is not None
        assert result["blind_spots"] == []
        assert result["consensus"] == []


class TestCrossValidate:
    def test_skips_when_fewer_than_2_upstream(self):
        assert cross_validate({"only_one": "report"}) is None

    def test_skips_internal_keys(self):
        assert cross_validate({"_quality": "...", "only_one": "report"}) is None

    def test_calls_llm_and_parses(self):
        mock_llm = MagicMock()
        mock_llm.chat.return_value = MagicMock(
            content=json.dumps({
                "contradictions": [],
                "blind_spots": ["currency risk"],
                "consensus": ["growth is strong"],
            })
        )
        result = cross_validate(
            {"analyst_a": "report A content", "analyst_b": "report B content"},
            llm=mock_llm,
        )
        assert result is not None
        assert result["blind_spots"] == ["currency risk"]
        mock_llm.chat.assert_called_once()

    def test_returns_none_on_llm_failure(self):
        mock_llm = MagicMock()
        mock_llm.chat.side_effect = RuntimeError("API timeout")
        result = cross_validate(
            {"a": "report", "b": "report"},
            llm=mock_llm,
        )
        assert result is None


class TestRunDebateRound:
    def test_skips_when_no_high_severity(self):
        cv = {
            "contradictions": [
                {"agent_a": "a", "claim_a": "x", "agent_b": "b", "claim_b": "y", "severity": "low"}
            ]
        }
        assert run_debate_round(cv, {"a": "...", "b": "..."}) == []

    def test_runs_debate_for_high_severity(self):
        cv = {
            "contradictions": [
                {
                    "agent_a": "flow",
                    "claim_a": "outflow",
                    "agent_b": "news",
                    "claim_b": "easing",
                    "severity": "high",
                }
            ]
        }
        mock_llm = MagicMock()
        mock_llm.chat.return_value = MagicMock(content="I maintain my position because...")

        resolutions = run_debate_round(
            cv,
            {"flow": "flow report", "news": "news report"},
            llm=mock_llm,
        )
        assert len(resolutions) == 1
        assert resolutions[0]["agent_a"] == "flow"
        assert resolutions[0]["agent_b"] == "news"
        assert "maintain" in resolutions[0]["response_a"]
        assert mock_llm.chat.call_count == 2

    def test_caps_at_max_contradictions(self):
        cv = {
            "contradictions": [
                {"agent_a": f"a{i}", "claim_a": "x", "agent_b": f"b{i}", "claim_b": "y", "severity": "high"}
                for i in range(10)
            ]
        }
        mock_llm = MagicMock()
        mock_llm.chat.return_value = MagicMock(content="response")
        upstream = {f"a{i}": f"report {i}" for i in range(10)}
        upstream.update({f"b{i}": f"report {i}" for i in range(10)})

        resolutions = run_debate_round(cv, upstream, llm=mock_llm)
        assert len(resolutions) <= 3


class TestFormatBlock:
    def test_basic_format(self):
        cv = {
            "contradictions": [
                {"agent_a": "a", "claim_a": "bull", "agent_b": "b", "claim_b": "bear", "severity": "low"}
            ],
            "blind_spots": ["FX risk"],
            "consensus": ["growth strong"],
        }
        block = format_cross_validation_block(cv)
        assert "## Cross-Validation Analysis" in block
        assert "Blind Spots" in block
        assert "FX risk" in block
        assert "Consensus" in block
        assert "growth strong" in block

    def test_with_resolutions(self):
        cv = {
            "contradictions": [
                {"agent_a": "flow", "claim_a": "out", "agent_b": "news", "claim_b": "in", "severity": "high"}
            ],
            "blind_spots": [],
            "consensus": [],
        }
        resolutions = [{
            "topic": "flow vs news",
            "agent_a": "flow",
            "claim_a": "outflow",
            "response_a": "data supports outflow",
            "agent_b": "news",
            "claim_b": "easing",
            "response_b": "policy is clear",
        }]
        block = format_cross_validation_block(cv, resolutions)
        assert "Contradictions Resolved" in block
        assert "flow vs news" in block
        assert "data supports outflow" in block

    def test_empty_cv(self):
        block = format_cross_validation_block(
            {"contradictions": [], "blind_spots": [], "consensus": []}
        )
        assert "## Cross-Validation Analysis" in block


class TestProcessingRules:
    def test_contains_key_rules(self):
        rules = get_processing_rules()
        assert "Quality-Weighted Synthesis" in rules
        assert "Contradiction Resolution" in rules
        assert "Blind Spot" in rules
        assert "Consensus Anchoring" in rules
        assert "HARD RULE" in rules


class TestIsEnabled:
    def test_default_on(self, monkeypatch):
        monkeypatch.delenv("SWARM_CROSS_VALIDATION", raising=False)
        assert is_cross_validation_enabled() is True

    def test_off(self, monkeypatch):
        monkeypatch.setenv("SWARM_CROSS_VALIDATION", "off")
        assert is_cross_validation_enabled() is False


class TestWorkerPromptIntegration:
    """Verify that build_worker_prompt correctly handles _cross_validation key."""

    def test_cv_block_injected_before_upstream(self):
        from src.swarm.models import SwarmAgentSpec
        from src.swarm.worker import build_worker_prompt

        agent = SwarmAgentSpec(
            id="synthesizer",
            role="Synthesizer",
            system_prompt="Synthesize: {upstream_context}",
        )
        prompt = build_worker_prompt(
            agent,
            upstream_summaries={
                "analyst_a": "Report A content here",
                "analyst_b": "Report B content here",
                "_cross_validation": "## Cross-Validation Analysis\n### Consensus\n- All agree",
                "_quality_summary": "## Data Quality Summary\n| Source | Grade |\n|---|---|\n| a | A |",
            },
            skill_descriptions="",
        )
        cv_pos = prompt.find("Cross-Validation Analysis")
        quality_pos = prompt.find("Data Quality Summary")
        upstream_pos = prompt.find("Upstream Context")
        assert cv_pos < quality_pos < upstream_pos
        assert "Cross-Validation Processing Rules" in prompt

    def test_no_cv_block_no_processing_rules(self):
        from src.swarm.models import SwarmAgentSpec
        from src.swarm.worker import build_worker_prompt

        agent = SwarmAgentSpec(
            id="synthesizer",
            role="Synthesizer",
            system_prompt="Synthesize: {upstream_context}",
        )
        prompt = build_worker_prompt(
            agent,
            upstream_summaries={"analyst_a": "Report A"},
            skill_descriptions="",
        )
        assert "Cross-Validation Processing Rules" not in prompt
