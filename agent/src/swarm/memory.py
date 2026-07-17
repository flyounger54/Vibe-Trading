"""Swarm cross-run memory: decision logging and historical context injection.

Phase A (immediate): After a run completes, record the decision/conclusion.
Phase B (deferred):  On the next run for the same target, fetch actual
                     returns, compute alpha vs benchmark, generate a
                     one-sentence reflection, then inject the history
                     into the terminal node's prompt.

Adapted from TradingAgent-Astock's TradingMemoryLog but generalised:
- Benchmark configurable via SWARM_MEMORY_BENCHMARK
- Works for any preset type (decision or analysis)
- Storage is append-only markdown with structured delimiters
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_ENTRY_DELIMITER = "<!-- ENTRY_END -->"
_MEMORY_DIR_NAME = "memory"
_MAX_HISTORY_ENTRIES = 5


def is_memory_enabled() -> bool:
    return os.getenv("SWARM_MEMORY", "off").lower() in ("on", "1", "true", "yes")


def _memory_dir(swarm_root: Path) -> Path:
    return swarm_root / _MEMORY_DIR_NAME


def _safe_key(text: str) -> str:
    """Sanitise a symbol/preset name for use as a filename."""
    return re.sub(r"[^a-zA-Z0-9._-]", "_", text)[:80]


def resolve_memory_key(
    user_vars: dict[str, str],
    preset_name: str,
) -> str:
    """Determine the memory key from user_vars and preset name.

    Priority: target variable > extracted symbol > preset+market > preset+date.
    """
    target = user_vars.get("target", "").strip()
    if target:
        return _safe_key(target)

    from src.swarm.grounding import extract_symbols_from_user_vars
    symbols = extract_symbols_from_user_vars(user_vars)
    if symbols:
        return _safe_key(symbols[0])

    market = user_vars.get("market", "").strip()
    if market:
        return _safe_key(f"{preset_name}_{market}")

    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    return _safe_key(f"{preset_name}_{date_str}")


def _is_decision_preset(final_report: str, agents_roles: list[str]) -> bool:
    """Detect if this is a decision-type preset (vs analysis-type)."""
    decision_keywords = {"manager", "officer", "decision", "allocator", "pm"}
    for role in agents_roles:
        if any(kw in role.lower() for kw in decision_keywords):
            return True
    return False


def _extract_rating(report: str) -> str:
    """Extract a rating from the final report text."""
    patterns = [
        r"\b(strong\s+buy|buy|overweight|hold|underweight|sell|strong\s+sell)\b",
        r"\b(long|short|wait|hedge)\b",
        r"(买入|增持|持有|减持|卖出|观望)",
        r"(做多|做空|等待|对冲)",
    ]
    text_lower = report.lower()
    for pattern in patterns:
        match = re.search(pattern, text_lower, re.IGNORECASE)
        if match:
            return match.group(0).strip()
    return "unrated"


def record_decision(
    swarm_root: Path,
    memory_key: str,
    preset_name: str,
    final_report: str,
    agents_roles: list[str],
) -> None:
    """Phase A: Record the run's decision immediately after completion."""
    mem_dir = _memory_dir(swarm_root)
    mem_dir.mkdir(parents=True, exist_ok=True)
    mem_file = mem_dir / f"{memory_key}.md"

    now = datetime.now(timezone.utc).isoformat()
    is_decision = _is_decision_preset(final_report, agents_roles)

    if is_decision:
        rating = _extract_rating(final_report)
        summary = final_report[:500].replace("\n", " ").strip()
        entry = (
            f"## {now}\n"
            f"- **preset**: {preset_name}\n"
            f"- **type**: decision\n"
            f"- **rating**: {rating}\n"
            f"- **summary**: {summary}\n"
            f"- **status**: pending\n"
        )
    else:
        summary = final_report[:500].replace("\n", " ").strip()
        entry = (
            f"## {now}\n"
            f"- **preset**: {preset_name}\n"
            f"- **type**: analysis\n"
            f"- **summary**: {summary}\n"
            f"- **status**: pending\n"
        )

    try:
        with open(mem_file, "a", encoding="utf-8") as f:
            f.write(f"\n{entry}\n{_ENTRY_DELIMITER}\n")
        logger.info("Memory recorded for %s in %s", memory_key, mem_file)
    except Exception:
        logger.warning("Failed to write memory for %s", memory_key, exc_info=True)


def load_history_context(
    swarm_root: Path,
    memory_key: str,
) -> str:
    """Load historical context for injection into terminal node prompts.

    Returns formatted markdown or empty string if no history exists.
    """
    mem_file = _memory_dir(swarm_root) / f"{memory_key}.md"
    if not mem_file.exists():
        return ""

    try:
        content = mem_file.read_text(encoding="utf-8")
    except Exception:
        logger.warning("Failed to read memory file %s", mem_file, exc_info=True)
        return ""

    entries = [
        e.strip() for e in content.split(_ENTRY_DELIMITER)
        if e.strip()
    ]

    if not entries:
        return ""

    recent = entries[-_MAX_HISTORY_ENTRIES:]

    lines = [
        "## Historical Context (from previous runs on this target)",
        "",
        f"You have analyzed this target {len(entries)} time(s) before. "
        f"Below are the most recent {len(recent)} entries:",
        "",
    ]
    lines.extend(recent)
    lines.append("")
    lines.append(
        "Use this history to avoid repeating past mistakes and to track "
        "whether your previous assessments played out correctly."
    )

    return "\n".join(lines)
