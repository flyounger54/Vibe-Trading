"""Model registry: auto-discovers Predictor implementations in this package."""

from __future__ import annotations

import importlib
import logging
import pkgutil
from pathlib import Path
from typing import Any

from src.ml.base_model import Predictor

logger = logging.getLogger(__name__)

MODEL_REGISTRY: dict[str, type] = {}


def _discover_models() -> None:
    if MODEL_REGISTRY:
        return
    pkg_dir = str(Path(__file__).parent)
    for _, module_name, _ in pkgutil.iter_modules([pkg_dir]):
        if module_name.startswith("_"):
            continue
        try:
            mod = importlib.import_module(f"src.ml.models.{module_name}")
            for attr_name in dir(mod):
                attr = getattr(mod, attr_name)
                if (
                    isinstance(attr, type)
                    and hasattr(attr, "name")
                    and isinstance(getattr(attr, "name", None), str)
                    and attr is not Predictor
                    and getattr(attr, "name", "")
                ):
                    MODEL_REGISTRY[attr.name] = attr
        except Exception as exc:
            logger.warning("Skipped src.ml.models.%s: %s", module_name, exc)


def get_model(name: str, **params: Any) -> Predictor:
    """Instantiate a model by name with optional parameter overrides."""
    _discover_models()
    if name not in MODEL_REGISTRY:
        available = sorted(MODEL_REGISTRY.keys()) or ["(none — install ml extras)"]
        raise KeyError(f"Unknown model {name!r}. Available: {available}")
    return MODEL_REGISTRY[name](**params)


def list_available_models() -> list[str]:
    _discover_models()
    return sorted(MODEL_REGISTRY.keys())
