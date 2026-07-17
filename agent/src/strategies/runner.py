"""Strategy runner: bridge between strategy registry and backtest engine.

Provides ``run()`` to execute a single strategy backtest and ``compare()``
to run multiple strategies on the same data and produce a metrics comparison.
"""

from __future__ import annotations

import json
import logging
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.strategies.registry import StrategyRegistry, get_default_registry

logger = logging.getLogger(__name__)


def _default_run_root() -> Path:
    return Path.home() / ".vibe-trading" / "strategy_runs"


def _write_config(run_dir: Path, config: dict[str, Any]) -> Path:
    path = run_dir / "config.json"
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _write_signal_engine_shim(
    run_dir: Path,
    strategy_id: str,
    params: dict[str, Any],
) -> Path:
    """Write a ``signal_engine.py`` shim that delegates to the zoo strategy."""
    params_repr = repr(params)
    code = textwrap.dedent(f"""\
        from src.strategies.registry import get_default_registry

        _registry = get_default_registry()
        _engine = _registry.load({strategy_id!r}, **{params_repr})


        class SignalEngine:
            def generate(self, data_map):
                return _engine.generate(data_map)
    """)
    path = run_dir / "signal_engine.py"
    path.write_text(code, encoding="utf-8")
    return path


def run(
    strategy_id: str,
    *,
    codes: list[str],
    start_date: str | None = None,
    end_date: str | None = None,
    params: dict[str, Any] | None = None,
    source: str = "auto",
    initial_cash: int = 1_000_000,
    commission: float = 0.001,
    run_root: Path | None = None,
    registry: StrategyRegistry | None = None,
) -> dict[str, Any]:
    """Run a strategy from the zoo on the given instruments.

    Returns:
        Dict with ``run_dir``, ``strategy_id``, ``config``, and ``status``.
        The actual backtest is delegated to the existing backtest engine.
    """
    reg = registry or get_default_registry()
    strategy = reg.get(strategy_id)
    meta = strategy.meta

    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    effective_end = end_date or today
    effective_start = start_date or str(int(today[:4]) - 10) + today[4:]

    # Validate prerequisites before creating any run artifacts.  In particular,
    # model-backed strategies must never produce a run directory that only fails
    # later when the generated shim is imported by the backtest worker.
    merged_params = reg.validate_params(strategy_id, **(params or {}))

    root = run_root or _default_run_root()
    run_dir = root / f"{strategy_id}_{now.strftime('%Y%m%d_%H%M%S')}"
    run_dir.mkdir(parents=True, exist_ok=True)

    config = {
        "source": source,
        "codes": codes,
        "start_date": effective_start,
        "end_date": effective_end,
        "interval": meta.get("frequency", ["1D"])[0],
        "initial_cash": initial_cash,
        "commission": commission,
        "extra_fields": None,
        "fundamental_fields": None,
        "optimizer": None,
        "optimizer_params": {},
        "engine": "daily",
        "validation": None,
    }

    _write_config(run_dir, config)
    _write_signal_engine_shim(run_dir, strategy_id, merged_params)

    run_card = {
        "strategy_id": strategy_id,
        "strategy_nickname": meta.get("nickname", strategy_id),
        "category": strategy.category,
        "params": merged_params,
        "config": config,
        "run_dir": str(run_dir),
        "created_at": now.isoformat(),
    }
    (run_dir / "run_card.json").write_text(
        json.dumps(run_card, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    return {
        "run_dir": str(run_dir),
        "strategy_id": strategy_id,
        "config": config,
        "status": "ready",
    }


def compare(
    strategy_ids: list[str],
    *,
    codes: list[str],
    start_date: str | None = None,
    end_date: str | None = None,
    source: str = "auto",
    initial_cash: int = 1_000_000,
    commission: float = 0.001,
    run_root: Path | None = None,
    registry: StrategyRegistry | None = None,
) -> list[dict[str, Any]]:
    """Run multiple strategies on the same instruments for comparison."""
    results = []
    for sid in strategy_ids:
        result = run(
            sid,
            codes=codes,
            start_date=start_date,
            end_date=end_date,
            source=source,
            initial_cash=initial_cash,
            commission=commission,
            run_root=run_root,
            registry=registry,
        )
        results.append(result)
    return results


def recommend(
    *,
    universe: str | None = None,
    risk: str | None = None,
    registry: StrategyRegistry | None = None,
) -> list[dict[str, Any]]:
    """Recommend strategies matching universe and risk preference."""
    reg = registry or get_default_registry()
    ids = reg.list(universe=universe, risk=risk, directly_runnable=True)
    return [reg.get(sid).meta for sid in ids]
