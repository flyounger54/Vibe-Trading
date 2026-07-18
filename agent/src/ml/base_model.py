"""Predictor protocol and shared types for ML models."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import numpy as np


@runtime_checkable
class Predictor(Protocol):
    """All ML models must satisfy this structural protocol."""

    name: str

    def fit(self, X: np.ndarray, y: np.ndarray, **kwargs: Any) -> None: ...

    def predict(self, X: np.ndarray) -> np.ndarray: ...

    def predict_proba(self, X: np.ndarray) -> np.ndarray | None:
        """Return positive-class probabilities for classifiers, None for regressors."""
        ...

    def calibrate(self, X_val: np.ndarray, y_val: np.ndarray) -> None:
        """Calibrate probability output on a held-out validation set.

        Uses Platt scaling or isotonic regression (sklearn CalibratedClassifierCV).
        Regression models treat this as a no-op.
        The calibrator is persisted alongside the model via save/load.
        """
        ...

    def get_feature_importance(self) -> dict[str, float] | None: ...

    def get_params(self) -> dict[str, Any]: ...

    def save(self, path: Path) -> None: ...

    @classmethod
    def load(cls, path: Path) -> Predictor: ...


@dataclass(frozen=True)
class PreprocessConfig:
    """Feature preprocessing configuration."""

    winsorize: bool = True
    winsorize_limits: tuple[float, float] = (0.01, 0.99)
    zscore: bool = True
    fillna_strategy: str = "median"  # "median" | "zero" | "ffill" | "none"


@dataclass(frozen=True)
class LabelConfig:
    """Label construction configuration."""

    horizon: int = 1
    label_type: str = "return"  # "return" | "rank" | "binary" | "top_bottom"
    threshold: float = 0.0
    quantile_pct: float = 0.2
    benchmark: str | None = None
    cost_bps: float = 0.0

    @property
    def key(self) -> str:
        parts = [f"{self.label_type}_{self.horizon}d"]
        if self.benchmark:
            parts.append(f"vs_{self.benchmark.replace('.', '_')}")
        if self.cost_bps > 0:
            parts.append(f"cost{int(self.cost_bps)}")
        return "_".join(parts)


@dataclass(frozen=True)
class FeatureSelectionConfig:
    """Feature selection pipeline configuration."""

    methods: list[str] = field(default_factory=lambda: ["ic_filter", "corr_dedup"])
    min_icir: float = 0.3
    max_corr: float = 0.85
    shap_top_n: int | None = None
    shap_min_pct: float = 0.01
    boruta_max_iter: int = 100
    boruta_alpha: float = 0.05
    mi_top_n: int | None = None
    mi_min_score: float = 0.0
    importance_min_pct: float = 0.01


@dataclass(frozen=True)
class TrainConfig:
    """End-to-end training pipeline configuration."""

    universe: str
    period: str
    feature_profile_id: str | None = None
    zoo: str = "qlib158"
    selection_config: FeatureSelectionConfig | None = None
    label_config: LabelConfig = field(default_factory=LabelConfig)
    preprocess_config: PreprocessConfig = field(default_factory=PreprocessConfig)
    n_splits: int = 5
    expanding: bool = True
    gap_days: int = 0
    model_type: str = "lightgbm"
    model_params: dict[str, Any] | None = None
    model_id: str | None = None
    use_cache: bool = True
    pit_universe: bool = True
    calibrate_proba: bool = True
    random_seed: int = 42
    min_train_samples: int = 20


@dataclass(frozen=True)
class TrainResult:
    """Immutable output of a training pipeline run."""

    model_id: str
    model_path: Path
    meta_path: Path
    n_features: int
    n_features_selected: int
    n_train_samples: int
    cv_scores: list[dict[str, float]]
    cv_summary: dict[str, float]
    feature_importance: dict[str, float]
    selection_report: dict[str, Any] | None
    anti_leakage_audit: dict[str, Any]
    overfit_warning: bool
    wall_seconds: float
    research_only: bool
    production_eligible: bool
