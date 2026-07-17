"""Swarm auto-quality scoring for worker outputs.

Deterministic quality checks run after each worker completes. Grades
(A–F) are attached to task summaries and propagated to downstream
agents via upstream_context, letting synthesizers weight sources by
reliability.

Adapted from TradingAgent-Astock's quality_gate.py but generalised:
no Chinese-only markers, no hardcoded analyst types, role-aware
scoring for quant/backtest agents.
"""

from __future__ import annotations

import os
import re

FAILURE_MARKERS = [
    "unable to fetch",
    "no data available",
    "tool error",
    "access denied",
    "rate limited",
    "empty response",
    "timed out",
    "无法获取",
    "工具调用失败",
    "数据获取失败",
    "访问被拒绝",
]

_QUANT_AGENT_KEYWORDS = frozenset({
    "backtest", "factor", "quant", "ml", "model",
    "screener", "scanner", "optimizer", "engineer",
})

_MIN_REPORT_LENGTH = 200
_MIN_REPORT_LENGTH_SHORT = 80


def _is_quant_agent(agent_id: str) -> bool:
    """Detect quant/backtest agents that produce non-prose output."""
    lower = agent_id.lower()
    return any(kw in lower for kw in _QUANT_AGENT_KEYWORDS)


def _count_numbers(text: str) -> int:
    """Count numeric tokens (prices, percentages, etc.) in text."""
    return len(re.findall(r"\d+\.?\d*%?", text))


def score_report(
    report: str,
    agent_id: str = "",
) -> tuple[str, list[str]]:
    """Score a worker's output report on quality. No LLM call.

    Args:
        report: The worker's summary/report text.
        agent_id: Agent identifier for role-aware scoring.

    Returns:
        (grade, issues) where grade is A/B/C/D/F and issues is a list
        of human-readable problem descriptions.
    """
    if not report or len(report.strip()) < 50:
        return ("F", ["report is empty or extremely short"])

    text = report.strip()
    issues: list[str] = []
    is_quant = _is_quant_agent(agent_id)

    min_length = _MIN_REPORT_LENGTH_SHORT if is_quant else _MIN_REPORT_LENGTH
    if len(text) < min_length:
        issues.append(f"report too short ({len(text)} chars < {min_length})")

    fail_count = sum(
        1 for marker in FAILURE_MARKERS if marker.lower() in text.lower()
    )
    if fail_count >= 2:
        issues.append(f"{fail_count} data fetch failures detected")

    has_table = "|" in text and "---" in text
    if not has_table and not is_quant:
        issues.append("no summary table found")

    if is_quant and not has_table:
        num_count = _count_numbers(text)
        if num_count < 5:
            issues.append("insufficient numeric results for quant output")

    missing_markers = (
        text.count("[数据缺失")
        + text.count("[missing")
        + text.count("[data unavailable")
    )
    na_count = len(re.findall(r"\bN/?A\b", text))
    total_missing = missing_markers + (na_count if na_count >= 3 else 0)
    if total_missing >= 3:
        issues.append(f"{total_missing} missing-data markers")

    if fail_count >= 2 and len(text) < min_length:
        return ("D", issues)
    if fail_count >= 3:
        return ("D", issues)
    if len(issues) >= 3:
        return ("C", issues)
    if len(issues) >= 1:
        return ("B", issues)
    return ("A", [])


def format_quality_summary(
    grades: dict[str, tuple[str, list[str]]],
) -> str:
    """Render a quality summary table for injection into upstream_context.

    Args:
        grades: Mapping of context_key -> (grade, issues).

    Returns:
        Markdown block with quality table and warnings.
    """
    if not grades:
        return ""

    lines = [
        "## Data Quality Summary",
        "| Source | Grade | Issues |",
        "|--------|-------|--------|",
    ]

    warnings: list[str] = []
    for key, (grade, issues) in grades.items():
        issue_text = "; ".join(issues) if issues else "—"
        lines.append(f"| {key} | {grade} | {issue_text} |")
        if grade in ("D", "F"):
            warnings.append(
                f"⚠️ {key} quality is low ({grade}) — reduce its weight "
                f"or flag uncertainty when citing its data."
            )

    if warnings:
        lines.append("")
        lines.extend(warnings)

    return "\n".join(lines)


def build_quality_feedback(
    grade: str,
    issues: list[str],
) -> str:
    """Build feedback text for a quality-triggered retry.

    Args:
        grade: The quality grade (D or F).
        issues: List of specific quality issues.

    Returns:
        Feedback string to inject into the retry prompt.
    """
    issue_list = "\n".join(f"  - {issue}" for issue in issues)
    return (
        f"Your previous analysis was rated **{grade}** due to the "
        f"following quality issues:\n{issue_list}\n\n"
        "Please specifically address these gaps in your revised output. "
        "Ensure your report includes:\n"
        "  - Concrete data with specific numbers, dates, and sources\n"
        "  - A summary table with key metrics\n"
        "  - Traceable citations from tool call results\n"
        "  - At least 200+ characters of substantive analysis"
    )


def is_quality_scoring_enabled() -> bool:
    """Check if quality scoring is enabled via environment variable."""
    return os.getenv("SWARM_QUALITY_SCORING", "on").lower() in ("on", "1", "true", "yes")
