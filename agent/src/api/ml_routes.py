"""ML Training HTTP routes for the Web UI.

Mounted by ``agent/api_server.py`` via ``register_ml_routes(app)``.

Routes:
- ``GET  /ml/models``              — list all trained models
- ``GET  /ml/models/{model_id}``   — single model metadata
- ``DELETE /ml/models/{model_id}`` — delete a model
- ``POST /ml/train``               — start training (returns job_id)
- ``GET  /ml/train/{job_id}/stream`` — SSE: progress / result / done / error
- ``GET  /ml/profiles``            — list feature profiles
- ``POST /ml/profiles``            — create a feature profile
- ``POST /ml/compare``             — compare models by CV metrics
- ``POST /ml/ensemble``            — create an ensemble
- ``POST /ml/health``              — check model health
- ``GET  /ml/experiments``         — list experiment configs
"""

from __future__ import annotations

import asyncio
import json
import logging
import traceback
import uuid
from typing import Any, Awaitable, Callable

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from src.ml.jobs import TrainingJobStore
from src.security.boundaries import resolve_within_root, validate_identifier

logger = logging.getLogger(__name__)

# Durable storage survives API restarts. The old in-memory dictionary lost
# both audit history and cancellation requests on every restart.
_TRAIN_JOB_STORE = TrainingJobStore()
_RUNNING_TASKS: set[asyncio.Task[Any]] = set()
MAX_CONCURRENT_TRAINS = 2

AuthDep = Callable[..., Awaitable[None]]


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------

class TrainRequest(BaseModel):
    universe: str = "csi300"
    period: str
    zoo: str = "qlib158"
    feature_profile_id: str | None = None
    model_type: str = "lightgbm"
    model_params: dict[str, Any] | None = None
    label_horizon: int = 5
    label_type: str = "binary"
    benchmark: str | None = None
    cost_bps: float = 0
    n_splits: int = 5
    model_id: str | None = None
    pit_universe: bool = True
    calibrate_proba: bool = True
    random_seed: int = 42


class SelectFeaturesRequest(BaseModel):
    universe: str = "csi300"
    period: str
    zoo: str = "qlib158"
    methods: list[str] = ["ic_filter", "corr_dedup"]
    profile_id: str | None = None


class CompareRequest(BaseModel):
    model_ids: list[str]


class EnsembleRequest(BaseModel):
    model_ids: list[str]
    method: str = "ic_weighted"
    weights: list[float] | None = None
    ensemble_id: str | None = None


class HealthRequest(BaseModel):
    model_id: str | None = None
    recent_period: str
    base_id: str | None = None


# ---------------------------------------------------------------------------
# Route registration
# ---------------------------------------------------------------------------

def register_ml_routes(
    app: FastAPI,
    require_auth: AuthDep | None = None,
    require_event_stream_auth: AuthDep | None = None,
) -> None:
    if require_auth is None or require_event_stream_auth is None:
        import sys as _sys
        host = _sys.modules.get("api_server") or _sys.modules.get("agent.api_server")
        if host is None:
            raise RuntimeError("register_ml_routes: api_server not in sys.modules")
        if require_auth is None:
            require_auth = host.require_auth
        if require_event_stream_auth is None:
            require_event_stream_auth = host.require_event_stream_auth

    # -------------------------------------------------------------------
    # GET /ml/models
    # -------------------------------------------------------------------
    @app.get("/ml/models", dependencies=[Depends(require_auth)])
    async def list_models_api(sort_by: str = Query("created_at")):
        from src.ml.storage import list_models
        models = list_models(sort_by=sort_by)
        return {"status": "ok", "models": models, "total": len(models)}

    # -------------------------------------------------------------------
    # GET /ml/models/{model_id}
    # -------------------------------------------------------------------
    @app.get("/ml/models/{model_id}", dependencies=[Depends(require_auth)])
    async def get_model_api(model_id: str):
        from pathlib import Path

        models_dir = Path.home() / ".vibe-trading" / "models"
        try:
            model_dir = resolve_within_root(models_dir, model_id, kind="model_id")
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        meta_path = model_dir / "metadata.json"
        if not meta_path.exists():
            raise HTTPException(404, f"Model {model_id} not found")
        meta = json.loads(meta_path.read_text(encoding="utf-8"))

        log_path = model_dir / "train_log.jsonl"
        train_log = []
        if log_path.exists():
            for line in log_path.read_text(encoding="utf-8").strip().split("\n"):
                if line.strip():
                    train_log.append(json.loads(line))

        return {"status": "ok", "metadata": meta, "train_log": train_log}

    # -------------------------------------------------------------------
    # DELETE /ml/models/{model_id}
    # -------------------------------------------------------------------
    @app.delete("/ml/models/{model_id}", dependencies=[Depends(require_auth)])
    async def delete_model_api(model_id: str):
        import shutil
        from pathlib import Path

        models_dir = Path.home() / ".vibe-trading" / "models"
        try:
            model_dir = resolve_within_root(models_dir, model_id, kind="model_id")
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if not model_dir.exists():
            raise HTTPException(404, f"Model {model_id} not found")
        shutil.rmtree(model_dir)
        return {"status": "ok", "deleted": model_id}

    # -------------------------------------------------------------------
    # POST /ml/train → returns job_id, training runs in background
    # -------------------------------------------------------------------
    @app.post("/ml/train", dependencies=[Depends(require_auth)])
    async def start_train_api(req: TrainRequest):
        running = sum(
            1 for job in _TRAIN_JOB_STORE.list() if job.get("status") in {"running", "cancelling"}
        )
        if running >= MAX_CONCURRENT_TRAINS:
            raise HTTPException(429, f"Max {MAX_CONCURRENT_TRAINS} concurrent training jobs")

        job_id = uuid.uuid4().hex[:12]
        _TRAIN_JOB_STORE.create(job_id, req.model_dump())

        async def _run():
            try:
                result = await asyncio.to_thread(_train_sync, job_id, req)
                _TRAIN_JOB_STORE.finish(job_id, result)
            except Exception as exc:
                logger.error("Training job %s failed: %s\n%s", job_id, exc, traceback.format_exc())
                _TRAIN_JOB_STORE.fail(job_id, str(exc))

        task = asyncio.create_task(_run())
        _RUNNING_TASKS.add(task)
        task.add_done_callback(_RUNNING_TASKS.discard)

        return {"status": "ok", "job_id": job_id}

    # -------------------------------------------------------------------
    # POST /ml/train/{job_id}/cancel — cooperative cancellation
    # -------------------------------------------------------------------
    @app.post("/ml/train/{job_id}/cancel", dependencies=[Depends(require_auth)])
    async def cancel_train_api(job_id: str):
        try:
            validate_identifier(job_id, "job_id")
            job = _TRAIN_JOB_STORE.request_cancel(job_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {"status": "ok", "job_id": job_id, "job_status": job["status"]}

    # -------------------------------------------------------------------
    # GET /ml/train/{job_id}/stream — SSE
    # -------------------------------------------------------------------
    @app.get("/ml/train/{job_id}/stream", dependencies=[Depends(require_event_stream_auth)])
    async def train_stream_api(job_id: str):
        try:
            validate_identifier(job_id, "job_id")
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if _TRAIN_JOB_STORE.get(job_id) is None:
            raise HTTPException(404, f"Job {job_id} not found")

        async def event_gen():
            sent = 0
            while True:
                job = _TRAIN_JOB_STORE.get(job_id)
                if not job:
                    break
                events = job["events"][sent:]
                status = job["status"]
                result = job.get("result")
                error = job.get("error")

                for ev in events:
                    yield f"event: progress\ndata: {json.dumps(ev, default=str)}\n\n"
                    sent += 1

                if status == "done":
                    yield f"event: result\ndata: {json.dumps(result, default=str)}\n\n"
                    yield "event: done\ndata: {}\n\n"
                    break
                elif status in {"error", "cancelled", "interrupted"}:
                    yield f"event: error\ndata: {json.dumps({'error': error})}\n\n"
                    break

                await asyncio.sleep(0.5)

        return StreamingResponse(event_gen(), media_type="text/event-stream")

    # -------------------------------------------------------------------
    # GET /ml/profiles
    # -------------------------------------------------------------------
    @app.get("/ml/profiles", dependencies=[Depends(require_auth)])
    async def list_profiles_api():
        from src.ml.feature_profile import list_feature_profiles
        profiles = list_feature_profiles()
        return {"status": "ok", "profiles": profiles, "total": len(profiles)}

    # -------------------------------------------------------------------
    # POST /ml/profiles
    # -------------------------------------------------------------------
    @app.post("/ml/profiles", dependencies=[Depends(require_auth)])
    async def create_profile_api(req: SelectFeaturesRequest):
        from src.ml.base_model import FeatureSelectionConfig
        from src.ml.feature_profile import create_feature_profile

        sel_config = FeatureSelectionConfig(methods=req.methods)
        try:
            profile = await asyncio.to_thread(
                create_feature_profile,
                universe=req.universe,
                period=req.period,
                zoo=req.zoo,
                selection_config=sel_config,
                profile_id=req.profile_id,
            )
            return {
                "status": "ok",
                "profile_id": profile.profile_id,
                "n_selected": len(profile.selected_factor_ids),
                "factors": profile.selected_factor_ids[:20],
                "methods": profile.methods,
            }
        except Exception as exc:
            raise HTTPException(500, str(exc))

    # -------------------------------------------------------------------
    # POST /ml/compare
    # -------------------------------------------------------------------
    @app.post("/ml/compare", dependencies=[Depends(require_auth)])
    async def compare_models_api(req: CompareRequest):
        from src.ml.compare import compare_models
        df = compare_models(req.model_ids)
        return {
            "status": "ok",
            "comparison": json.loads(df.reset_index().to_json(orient="records")),
        }

    # -------------------------------------------------------------------
    # POST /ml/ensemble
    # -------------------------------------------------------------------
    @app.post("/ml/ensemble", dependencies=[Depends(require_auth)])
    async def create_ensemble_api(req: EnsembleRequest):
        from src.ml.ensemble import EnsembleConfig, create_ensemble
        config = EnsembleConfig(
            model_ids=req.model_ids,
            method=req.method,
            weights=req.weights,
        )
        try:
            eid = await asyncio.to_thread(create_ensemble, config, req.ensemble_id)
            return {"status": "ok", "ensemble_id": eid}
        except Exception as exc:
            raise HTTPException(500, str(exc))

    # -------------------------------------------------------------------
    # POST /ml/health
    # -------------------------------------------------------------------
    @app.post("/ml/health", dependencies=[Depends(require_auth)])
    async def check_health_api(req: HealthRequest):
        if req.base_id:
            from src.ml.monitoring import compare_version_drift
            result = await asyncio.to_thread(compare_version_drift, req.base_id)
            return {"status": "ok", **result}

        if req.model_id:
            from src.ml.monitoring import evaluate_model_health
            report = await asyncio.to_thread(
                evaluate_model_health, req.model_id, req.recent_period
            )
            return {
                "status": "ok",
                "model_id": report.model_id,
                "train_ic": report.train_ic_mean,
                "recent_ic": report.recent_ic_mean,
                "drift_score": report.drift_score,
                "retrain_recommended": report.retrain_recommended,
            }
        else:
            from src.ml.monitoring import check_all_models_health
            reports = await asyncio.to_thread(check_all_models_health, req.recent_period)
            return {
                "status": "ok",
                "reports": [
                    {
                        "model_id": r.model_id,
                        "drift_score": r.drift_score,
                        "retrain_recommended": r.retrain_recommended,
                    }
                    for r in reports
                ],
            }

    # -------------------------------------------------------------------
    # GET /ml/experiments
    # -------------------------------------------------------------------
    @app.get("/ml/experiments", dependencies=[Depends(require_auth)])
    async def list_experiments_api():
        from src.ml.experiment import list_experiments
        return {"status": "ok", "experiments": list_experiments()}


def _train_sync(job_id: str, req: TrainRequest) -> dict[str, Any]:
    """Synchronous training worker (runs in thread)."""
    from src.ml.base_model import LabelConfig, TrainConfig
    from src.ml.pipeline import run_training_pipeline

    def emit(stage: str, payload: dict[str, Any] | None = None, **kwargs: Any) -> None:
        event = dict(payload or {})
        event.update(kwargs)
        _TRAIN_JOB_STORE.append_event(job_id, stage, **event)

    emit("init", model_type=req.model_type, universe=req.universe)

    label_config = LabelConfig(
        horizon=req.label_horizon,
        label_type=req.label_type,
        benchmark=req.benchmark,
        cost_bps=req.cost_bps,
    )

    config = TrainConfig(
        universe=req.universe,
        period=req.period,
        feature_profile_id=req.feature_profile_id,
        zoo=req.zoo,
        label_config=label_config,
        n_splits=req.n_splits,
        model_type=req.model_type,
        model_params=req.model_params,
        model_id=req.model_id,
        pit_universe=req.pit_universe,
        calibrate_proba=req.calibrate_proba,
        random_seed=req.random_seed,
    )

    emit("training", message="Starting leakage-safe pipeline")
    result = run_training_pipeline(
        config,
        progress_callback=emit,
        should_cancel=lambda: _TRAIN_JOB_STORE.is_cancel_requested(job_id),
    )
    emit("complete", model_id=result.model_id)

    return {
        "model_id": result.model_id,
        "model_path": str(result.model_path),
        "n_features": result.n_features,
        "n_train_samples": result.n_train_samples,
        "cv_summary": result.cv_summary,
        "overfit_warning": result.overfit_warning,
        "research_only": result.research_only,
        "production_eligible": result.production_eligible,
        "top_features": dict(list(result.feature_importance.items())[:10]),
        "wall_seconds": result.wall_seconds,
    }
