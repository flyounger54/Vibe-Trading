"""Historical analysis run HTTP routes."""

from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.responses import JSONResponse


def register_run_routes(
    app: FastAPI,
    *,
    require_auth: Callable,
    runs_dir: Path,
    run_response_model: type,
    run_info_model: type,
    validate_path: Callable[[str, str], None],
    build_response: Callable[..., Any],
    response_payload: Callable[[Any], dict[str, Any]],
    load_json_file: Callable[[Path], dict[str, Any] | None],
    load_run_context: Callable[[Path], dict[str, Any]],
) -> None:
    """Register the isolated historical-run route boundary."""

    @app.get("/runs/{run_id}/code", dependencies=[Depends(require_auth)])
    async def get_run_code(run_id: str):
        validate_path(run_id, "run_id")
        code_dir = runs_dir / run_id / "code"
        if not code_dir.exists():
            raise HTTPException(status_code=404, detail=f"Code directory for run {run_id} not found")
        return {
            name: (code_dir / name).read_text(encoding="utf-8")
            for name in ["signal_engine.py"]
            if (code_dir / name).exists()
        }

    @app.get("/runs/{run_id}/pine", dependencies=[Depends(require_auth)])
    async def get_run_pine(run_id: str):
        validate_path(run_id, "run_id")
        pine_path = runs_dir / run_id / "artifacts" / "strategy.pine"
        if not pine_path.exists():
            return {"exists": False, "content": None}
        return {"exists": True, "content": pine_path.read_text(encoding="utf-8")}

    @app.get(
        "/runs/{run_id}",
        response_model=run_response_model,
        dependencies=[Depends(require_auth)],
    )
    async def get_run_result(
        run_id: str,
        chart_symbol: str | None = Query(None, description="Opt in to chart payloads for a single symbol"),
        chart_payload: str | None = Query(
            None,
            description="Optional chart payload mode. Use 'summary' to omit chart rows and trade markers.",
        ),
    ):
        validate_path(run_id, "run_id")
        if chart_payload not in (None, "summary"):
            raise HTTPException(status_code=400, detail="invalid chart_payload")
        run_dir = runs_dir / run_id
        if not run_dir.exists():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Run {run_id} not found")

        wants_chart_meta = bool(chart_payload or chart_symbol)
        chart_symbols: list[str] = []
        response = build_response(
            run_dir,
            elapsed=0.0,
            include_analysis=True,
            chart_symbol=chart_symbol,
            chart_payload=chart_payload or "full",
            chart_symbols_out=chart_symbols if wants_chart_meta else None,
        )
        if wants_chart_meta:
            payload = response_payload(response)
            payload["chart_symbols"] = chart_symbols
            return JSONResponse(payload)
        return response

    @app.get("/runs", response_model=list[run_info_model], dependencies=[Depends(require_auth)])
    async def list_runs(limit: int = 20):
        limit = min(max(1, limit), 100)
        if not runs_dir.exists():
            return []
        directories = sorted(
            [directory for directory in runs_dir.iterdir() if directory.is_dir()],
            key=lambda item: item.name,
            reverse=True,
        )
        results = []
        for directory in directories[:limit]:
            run_id = directory.name
            state_file = load_json_file(directory / "state.json")
            if state_file:
                status_value = str(state_file.get("status") or "unknown").lower()
            elif (directory / "artifacts" / "equity.csv").exists() or (directory / "review_report.json").exists():
                status_value = "success"
            else:
                status_value = "unknown"

            created_at = _created_at(run_id, directory)
            prompt = _prompt(directory)
            total_return, sharpe = _metrics(directory)
            context = load_run_context(directory)
            results.append(
                run_info_model(
                    run_id=run_id,
                    status=status_value,
                    created_at=created_at,
                    prompt=prompt or "Manual Analysis",
                    total_return=total_return,
                    sharpe=sharpe,
                    codes=context.get("codes") or [],
                    start_date=context.get("start_date"),
                    end_date=context.get("end_date"),
                )
            )
        return results


def _created_at(run_id: str, directory: Path) -> str:
    parts = run_id.removeprefix("run_").split("_")
    if len(parts) >= 2 and len(parts[0]) == 8 and len(parts[1]) == 6:
        day, clock = parts[0], parts[1]
        return f"{day[:4]}-{day[4:6]}-{day[6:8]} {clock[:2]}:{clock[2:4]}:{clock[4:6]}"
    return datetime.fromtimestamp(directory.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")


def _prompt(directory: Path) -> str | None:
    for filename, keys in (
        ("req.json", ("prompt",)),
        ("planner_output.json", ("user_goal", "goal")),
    ):
        path = directory / filename
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        for key in keys:
            if payload.get(key):
                return str(payload[key])
    path = directory / "user_prompt.txt"
    return path.read_text(encoding="utf-8").strip() if path.exists() else None


def _metrics(directory: Path) -> tuple[float | None, float | None]:
    path = directory / "artifacts" / "metrics.csv"
    if not path.exists():
        return None, None
    try:
        with path.open("r", encoding="utf-8") as handle:
            row = next(csv.DictReader(handle), None)
        if row is None:
            return None, None
        return float(row.get("total_return", 0) or 0), float(row.get("sharpe", 0) or 0)
    except (OSError, ValueError):
        return None, None
