"""Swarm auto-cross-validation for fan-in merge nodes.

When a downstream task has ≥2 upstream sources, a single LLM call
identifies contradictions, blind spots, and consensus across the
upstream reports. High-severity contradictions trigger a focused
debate round where each side responds to the other's claim.

All operations are best-effort: LLM failures skip cross-validation
and let the merge node proceed with unaugmented context (graceful
degradation to pre-enhancement behaviour).
"""

from __future__ import annotations

import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from src.providers.chat import ChatLLM

logger = logging.getLogger(__name__)

_MAX_DEBATE_CONTRADICTIONS = 3
_DEBATE_MAX_TOKENS = 300


def is_cross_validation_enabled() -> bool:
    return os.getenv("SWARM_CROSS_VALIDATION", "on").lower() in (
        "on", "1", "true", "yes",
    )


def _cv_model_name() -> str | None:
    """Resolve model for cross-validation calls."""
    return (
        os.getenv("SWARM_CROSS_VALIDATION_MODEL")
        or os.getenv("SWARM_QUICK_MODEL")
        or None
    )


_CROSS_VALIDATION_PROMPT = """\
You are a senior research quality reviewer. Below are {count} independent \
analyst reports on the same subject. Your job is to compare them and \
identify issues that a downstream synthesizer must address.

{reports_block}

Respond ONLY with valid JSON (no markdown fences, no commentary) matching \
this schema exactly:

{{
  "contradictions": [
    {{
      "agent_a": "<context_key of first agent>",
      "claim_a": "<the specific claim, one sentence>",
      "agent_b": "<context_key of second agent>",
      "claim_b": "<the opposing claim, one sentence>",
      "severity": "high" | "medium" | "low"
    }}
  ],
  "blind_spots": ["<important dimension no report covers>"],
  "consensus": ["<conclusion all reports agree on with evidence>"]
}}

IMPORTANT: Two analysts discussing DIFFERENT time horizons (short-term \
vs long-term), DIFFERENT aspects (price vs volume), or DIFFERENT scope \
(sector vs individual stock) are NOT contradicting each other. Only flag \
as "high" severity when two analysts make OPPOSITE claims about the SAME \
metric in the SAME time frame.

The upstream reports may be in Chinese or English. Analyze them as-is. \
Respond in the same language as the majority of the reports.\
"""

_DEBATE_PROMPT = """\
You are {agent_id}, the analyst who wrote the report below.

## Your Original Report
{original_report}

## Challenge
Another analyst ({opponent_id}) makes this claim that contradicts yours:
"{opponent_claim}"

Respond in under 200 words with specific data from your original report \
to support or refine your position. If you now agree with the other side, \
say so explicitly. Do not introduce new data you did not already cover.\
"""


def cross_validate(
    upstream_summaries: dict[str, str],
    llm: ChatLLM | None = None,
) -> dict[str, Any] | None:
    """Run cross-validation on upstream reports. Returns parsed result or None on failure."""
    reports = {k: v for k, v in upstream_summaries.items() if not k.startswith("_")}
    if len(reports) < 2:
        return None

    if llm is None:
        llm = ChatLLM(model_name=_cv_model_name())

    reports_block = "\n\n".join(
        f"### Report: {key}\n{summary[:3000]}"
        for key, summary in reports.items()
    )

    prompt = _CROSS_VALIDATION_PROMPT.format(
        count=len(reports),
        reports_block=reports_block,
    )

    try:
        response = llm.chat(
            messages=[{"role": "user", "content": prompt}],
            tools=None,
        )
    except Exception:
        logger.warning("Cross-validation LLM call failed", exc_info=True)
        return None

    return _parse_cv_response(response.content or "")


def _parse_cv_response(text: str) -> dict[str, Any] | None:
    """Parse the structured JSON response from cross-validation."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        cleaned = "\n".join(lines)

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            try:
                data = json.loads(cleaned[start : end + 1])
            except json.JSONDecodeError:
                logger.warning("Cross-validation response not valid JSON")
                return None
        else:
            logger.warning("Cross-validation response contains no JSON object")
            return None

    if not isinstance(data, dict):
        return None

    data.setdefault("contradictions", [])
    data.setdefault("blind_spots", [])
    data.setdefault("consensus", [])
    return data


def run_debate_round(
    cv_result: dict[str, Any],
    upstream_summaries: dict[str, str],
    llm: ChatLLM | None = None,
) -> list[dict[str, str]]:
    """Run debate responses for high-severity contradictions.

    Returns a list of resolved contradiction dicts, each with
    agent_a/b ids, claims, and their debate responses.
    """
    contradictions = cv_result.get("contradictions", [])
    high_severity = [
        c for c in contradictions
        if isinstance(c, dict) and c.get("severity") == "high"
    ]

    if not high_severity:
        return []

    high_severity = high_severity[:_MAX_DEBATE_CONTRADICTIONS]

    if llm is None:
        llm = ChatLLM(model_name=_cv_model_name())

    resolutions: list[dict[str, str]] = []

    def _get_response(agent_id: str, opponent_id: str,
                      opponent_claim: str, original_report: str) -> str:
        prompt = _DEBATE_PROMPT.format(
            agent_id=agent_id,
            original_report=original_report[:2000],
            opponent_id=opponent_id,
            opponent_claim=opponent_claim,
        )
        try:
            resp = llm.chat(
                messages=[{"role": "user", "content": prompt}],
                tools=None,
            )
            return resp.content or "(no response)"
        except Exception:
            logger.warning("Debate response failed for %s", agent_id, exc_info=True)
            return "(debate response failed)"

    with ThreadPoolExecutor(max_workers=4) as executor:
        for contradiction in high_severity:
            agent_a = contradiction.get("agent_a", "unknown_a")
            agent_b = contradiction.get("agent_b", "unknown_b")
            claim_a = contradiction.get("claim_a", "")
            claim_b = contradiction.get("claim_b", "")
            report_a = upstream_summaries.get(agent_a, "")
            report_b = upstream_summaries.get(agent_b, "")

            future_a = executor.submit(
                _get_response, agent_a, agent_b, claim_b, report_a,
            )
            future_b = executor.submit(
                _get_response, agent_b, agent_a, claim_a, report_b,
            )

            resolutions.append({
                "topic": f"{agent_a} vs {agent_b}",
                "agent_a": agent_a,
                "claim_a": claim_a,
                "response_a": future_a.result(timeout=120),
                "agent_b": agent_b,
                "claim_b": claim_b,
                "response_b": future_b.result(timeout=120),
            })

    return resolutions


def format_cross_validation_block(
    cv_result: dict[str, Any],
    resolutions: list[dict[str, str]] | None = None,
) -> str:
    """Render cross-validation results + debate as markdown for upstream_context injection."""
    lines = ["## Cross-Validation Analysis"]

    contradictions = cv_result.get("contradictions", [])
    if resolutions:
        lines.append("")
        lines.append("### Contradictions Resolved")
        for res in resolutions:
            lines.append(f"**{res['topic']}**")
            lines.append(f"- {res['agent_a']}: \"{res['claim_a']}\"")
            lines.append(f"- {res['agent_b']}: \"{res['claim_b']}\"")
            lines.append(f"- {res['agent_a']} responds: {res['response_a']}")
            lines.append(f"- {res['agent_b']} responds: {res['response_b']}")
            lines.append("")

    unresolved = [
        c for c in contradictions
        if isinstance(c, dict) and c.get("severity") != "high"
    ]
    if unresolved:
        lines.append("")
        lines.append("### Other Contradictions (not debated)")
        for c in unresolved:
            sev = c.get("severity", "?")
            lines.append(
                f"- [{sev}] {c.get('agent_a', '?')}: \"{c.get('claim_a', '')}\" "
                f"vs {c.get('agent_b', '?')}: \"{c.get('claim_b', '')}\""
            )

    blind_spots = cv_result.get("blind_spots", [])
    if blind_spots:
        lines.append("")
        lines.append("### Blind Spots")
        for spot in blind_spots:
            lines.append(f"- {spot}")

    consensus = cv_result.get("consensus", [])
    if consensus:
        lines.append("")
        lines.append("### Consensus (High Confidence)")
        for point in consensus:
            lines.append(f"- {point}")

    return "\n".join(lines)


_PROCESSING_RULES = """\
## Cross-Validation Processing Rules (HARD RULE)

Your upstream context contains a Cross-Validation Analysis with quality \
grades, contradictions, and debate records. You MUST process them as follows:

1. **Quality-Weighted Synthesis**: Weight each upstream source by its \
quality grade. A/B sources are high-trust. C sources should be used with \
caveats. D/F sources should only be cited for directional hints, never \
for specific numbers — explicitly note their low reliability.

2. **Contradiction Resolution**: For each resolved contradiction with \
debate records, you MUST take an explicit stance:
   - State which side's argument you find more convincing AND WHY
   - If both sides have merit, explain the CONDITIONS under which each \
holds (e.g., "short-term vs medium-term", "if policy X materializes vs \
doesn't")
   - You may NOT ignore a contradiction or average the claims — pick a \
side or explicitly decompose by scenario

3. **Blind Spot Acknowledgment**: For each blind spot identified, either:
   - Fill it with data from your own tool calls (preferred), or
   - Explicitly flag it as an unresolved risk in your output

4. **Consensus Anchoring**: High-confidence consensus points should form \
the foundation of your analysis. Challenge them only if you have strong \
contrary evidence from your own tool calls.\
"""


def get_processing_rules() -> str:
    """Return the cross-validation processing rules prompt block."""
    return _PROCESSING_RULES
