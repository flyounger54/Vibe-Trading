"""CLI handlers for strategy zoo commands.

Provides ``handle_strategy_list``, ``handle_strategy_run``, and
``handle_strategy_compare`` for integration with the agent CLI.
"""

from __future__ import annotations

import json
from typing import Any

from src.strategies.registry import get_default_registry
from src.strategies.runner import compare, recommend, run


def handle_strategy_list(
    *,
    category: str | None = None,
    universe: str | None = None,
    risk: str | None = None,
) -> str:
    """List strategies matching filters. Returns formatted text."""
    reg = get_default_registry()
    ids = reg.list(category=category, universe=universe, risk=risk)

    if not ids:
        return "No strategies found matching the given filters."

    lines = [f"Found {len(ids)} strategies:\n"]
    for sid in ids:
        s = reg.get(sid)
        meta = s.meta
        markets = ", ".join(meta.get("universe", []))
        risk_level = meta.get("risk_profile", "?")
        readiness = "ready" if meta.get("directly_runnable", True) else "configuration required"
        lines.append(
            f"  {sid:<28s} {meta.get('nickname', ''):<16s} "
            f"[{s.category}] {markets}  risk={risk_level}  {readiness}"
        )

    health = reg.health()
    lines.append(f"\nRegistry: {health['loaded']} loaded, {health['failed']} failed")
    return "\n".join(lines)


def handle_strategy_info(strategy_id: str) -> str:
    """Show detailed info for a single strategy."""
    reg = get_default_registry()
    s = reg.get(strategy_id)
    meta = s.meta
    return json.dumps(meta, ensure_ascii=False, indent=2)


def handle_strategy_run(
    strategy_id: str,
    *,
    codes: list[str],
    start_date: str | None = None,
    end_date: str | None = None,
    params: dict[str, Any] | None = None,
    source: str = "auto",
) -> str:
    """Run a strategy backtest. Returns formatted result."""
    result = run(
        strategy_id,
        codes=codes,
        start_date=start_date,
        end_date=end_date,
        params=params,
        source=source,
    )
    return (
        f"Strategy: {strategy_id}\n"
        f"Run dir:  {result['run_dir']}\n"
        f"Status:   {result['status']}\n"
        f"Config:   {json.dumps(result['config'], ensure_ascii=False, indent=2)}"
    )


def handle_strategy_compare(
    strategy_ids: list[str],
    *,
    codes: list[str],
    start_date: str | None = None,
    end_date: str | None = None,
    source: str = "auto",
) -> str:
    """Compare multiple strategies. Returns formatted results."""
    results = compare(
        strategy_ids,
        codes=codes,
        start_date=start_date,
        end_date=end_date,
        source=source,
    )
    lines = [f"Comparison of {len(results)} strategies:\n"]
    for r in results:
        lines.append(f"  {r['strategy_id']:<28s} -> {r['run_dir']}")
    return "\n".join(lines)


def handle_strategy_recommend(
    *,
    universe: str | None = None,
    risk: str | None = None,
) -> str:
    """Recommend strategies matching criteria."""
    recs = recommend(universe=universe, risk=risk)
    if not recs:
        return "No recommendations found for the given criteria."
    lines = [f"Recommended {len(recs)} strategies:\n"]
    for meta in recs:
        lines.append(
            f"  {meta['id']:<28s} {meta.get('nickname', ''):<16s} "
            f"risk={meta.get('risk_profile', '?')}  {meta.get('description', '')[:60]}"
        )
    return "\n".join(lines)
