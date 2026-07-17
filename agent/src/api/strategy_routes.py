"""Strategy Zoo HTTP routes for the Web UI.

Mounted by ``agent/api_server.py`` via ``register_strategy_routes(app)``.

Routes:
- ``GET  /strategy/list``              — list strategies with optional filters
- ``GET  /strategy/{strategy_id}``     — single strategy meta + source
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Query

from src.strategies.registry import RegistryError, get_default_registry

logger = logging.getLogger(__name__)


def register_strategy_routes(app: FastAPI) -> None:
    """Attach strategy zoo endpoints to the FastAPI app."""

    @app.get("/strategy/list")
    async def strategy_list(
        category: str | None = Query(None),
        universe: str | None = Query(None),
        risk: str | None = Query(None),
        limit: int = Query(1000, ge=1, le=5000),
    ) -> dict[str, Any]:
        reg = get_default_registry()
        ids = reg.list(category=category, universe=universe, risk=risk)
        strategies = []
        for sid in ids[:limit]:
            s = reg.get(sid)
            meta = s.meta
            strategies.append({
                "id": s.id,
                "category": s.category,
                "nickname": meta.get("nickname", ""),
                "description": meta.get("description", ""),
                "universe": meta.get("universe", []),
                "frequency": meta.get("frequency", []),
                "risk_profile": meta.get("risk_profile", ""),
                "min_bars": meta.get("min_bars", 0),
                "reference": meta.get("reference", ""),
                "default_params": meta.get("default_params", {}),
                "required_params": meta.get("required_params", []),
                "required_any_of": meta.get("required_any_of", []),
                "configuration_requirements": meta.get("configuration_requirements", []),
                "directly_runnable": meta.get("directly_runnable", True),
                "columns_required": meta.get("columns_required", []),
                "factors_used": meta.get("factors_used", []),
            })
        return {
            "strategies": strategies,
            "total": len(ids),
            "health": reg.health(),
        }

    @app.get("/strategy/{strategy_id}")
    async def strategy_detail(strategy_id: str) -> dict[str, Any]:
        reg = get_default_registry()
        try:
            s = reg.get(strategy_id)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"strategy {strategy_id!r} not found")
        try:
            source = reg.get_source(strategy_id)
        except RegistryError as exc:
            source = f"(source unavailable: {exc})"
        return {
            "strategy": {
                "id": s.id,
                "category": s.category,
                "module_path": s.module_path,
                "meta": s.meta,
            },
            "source_code": source,
        }
