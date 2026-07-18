"""Model storage: save/load/list trained models with metadata."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.ml.base_model import Predictor

logger = logging.getLogger(__name__)

_MODELS_DIR = Path.home() / ".vibe-trading" / "models"


def save_model(
    model: Predictor,
    model_id: str,
    metadata: dict[str, Any],
    models_dir: Path | None = None,
    model_manifest: Any | None = None,
) -> tuple[Path, Path]:
    """Save model artifacts + metadata.json. Returns (model_path, meta_path)."""
    base = models_dir or _MODELS_DIR
    model_dir = base / model_id
    model_dir.mkdir(parents=True, exist_ok=True)

    model.save(model_dir)
    meta_path = model_dir / "metadata.json"
    metadata["model_id"] = model_id
    metadata["saved_at"] = datetime.now(timezone.utc).isoformat()

    if model_manifest is not None:
        if hasattr(model_manifest, "model_id"):
            manifest_model_id = model_manifest.model_id
        elif isinstance(model_manifest, dict):
            manifest_model_id = model_manifest.get("model_id")
        else:
            raise TypeError("model_manifest must be a ModelManifest or dict")
        if manifest_model_id != model_id:
            raise ValueError("Model manifest model_id does not match saved model_id")
        if hasattr(model_manifest, "save"):
            manifest_path = model_manifest.save(model_dir)
        else:
            manifest_path = model_dir / "model_manifest.json"
            manifest_path.write_text(
                json.dumps(model_manifest, indent=2, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
        metadata["model_manifest_path"] = str(manifest_path)

    meta_path.write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    logger.info("Saved model %s to %s", model_id, model_dir)
    return model_dir, meta_path


def load_model(
    model_id: str,
    models_dir: Path | None = None,
) -> tuple[Predictor, dict[str, Any]]:
    """Load a saved model and its metadata."""
    base = models_dir or _MODELS_DIR
    model_dir = base / model_id

    meta_path = model_dir / "metadata.json"
    if not meta_path.exists():
        raise FileNotFoundError(f"Model not found: {model_dir}")

    metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    model_type = metadata.get("model_type", "ridge")

    from src.ml.models import MODEL_REGISTRY, _discover_models
    _discover_models()

    if model_type not in MODEL_REGISTRY:
        raise KeyError(
            f"Model type {model_type!r} not available. "
            f"Install the required extras or check MODEL_REGISTRY."
        )

    model_cls = MODEL_REGISTRY[model_type]
    model = model_cls.load(model_dir)
    return model, metadata


def list_models(
    models_dir: Path | None = None,
    sort_by: str = "created_at",
) -> list[dict[str, Any]]:
    """List all saved models with summary metadata."""
    base = models_dir or _MODELS_DIR
    if not base.exists():
        return []

    models = []
    for model_dir in sorted(base.iterdir()):
        meta_path = model_dir / "metadata.json"
        if not meta_path.exists():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            cv_summary = meta.get("cv_summary", {})
            models.append({
                "model_id": meta.get("model_id", model_dir.name),
                "model_type": meta.get("model_type", "?"),
                "label_horizon": meta.get("label_config", {}).get("horizon", "?"),
                "label_type": meta.get("label_config", {}).get("label_type", "?"),
                "n_features": meta.get("n_features", 0),
                "ic_mean": cv_summary.get("ic_mean"),
                "auc_mean": cv_summary.get("auc_mean"),
                "overfit_warning": meta.get("overfit_warning", False),
                "research_only": meta.get("research_only", True),
                "production_eligible": meta.get("production_eligible", False),
                "created_at": meta.get("created_at", ""),
                "feature_profile_id": meta.get("feature_profile_id", ""),
            })
        except Exception as exc:
            logger.warning("Failed to read model %s: %s", model_dir.name, exc)

    if sort_by and models:
        models.sort(key=lambda m: m.get(sort_by, ""), reverse=(sort_by != "model_id"))
    return models


def delete_model(model_id: str, models_dir: Path | None = None) -> bool:
    """Delete a saved model directory."""
    import shutil
    base = models_dir or _MODELS_DIR
    model_dir = base / model_id
    if model_dir.exists():
        shutil.rmtree(model_dir)
        logger.info("Deleted model %s", model_id)
        return True
    return False
