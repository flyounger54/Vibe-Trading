"""FeatureProfile: first-class artifact for reusable feature selection results."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.ml.base_model import FeatureSelectionConfig, PreprocessConfig

logger = logging.getLogger(__name__)

_PROFILES_DIR = Path.home() / ".vibe-trading" / "feature_profiles"


@dataclass(frozen=True)
class FeatureProfile:
    profile_id: str
    zoo: str
    universe: str
    selection_period: str
    methods: list[str]
    selected_factor_ids: list[str]
    preprocess_config: PreprocessConfig
    selection_report: dict[str, Any]
    created_at: str
    version: int = 1


def create_feature_profile(
    universe: str,
    period: str,
    zoo: str = "qlib158",
    selection_config: FeatureSelectionConfig | None = None,
    preprocess_config: PreprocessConfig | None = None,
    profile_id: str | None = None,
    profiles_dir: Path | None = None,
) -> FeatureProfile:
    """Run independent feature selection and save the result as a reusable profile."""
    from src.ml.feature_selection import select_features
    from src.ml.features import build_feature_matrix, preprocess_features
    from src.ml.labels import build_labels
    from src.ml.base_model import LabelConfig
    from src.tools.alpha_bench_tool import _load_universe_panel

    if selection_config is None:
        selection_config = FeatureSelectionConfig()
    if preprocess_config is None:
        preprocess_config = PreprocessConfig()

    panel = _load_universe_panel(universe, period)

    from src.factors.registry import get_default_registry
    registry = get_default_registry()
    all_factor_ids = registry.list(zoo=zoo)

    features = build_feature_matrix(panel, factor_ids=all_factor_ids, zoo=zoo)
    features, _ = preprocess_features(features, preprocess_config)

    label_config = LabelConfig(horizon=1, label_type="return")
    labels = build_labels(panel, label_config)

    selected_ids, report = select_features(
        features, labels, selection_config, panel=panel
    )

    now = datetime.now(timezone.utc).isoformat()
    if profile_id is None:
        methods_tag = "_".join(selection_config.methods[:2])
        profile_id = f"{zoo}_{universe}_{methods_tag}_{datetime.now(timezone.utc):%Y%m%d}"

    profile = FeatureProfile(
        profile_id=profile_id,
        zoo=zoo,
        universe=universe,
        selection_period=period,
        methods=selection_config.methods,
        selected_factor_ids=selected_ids,
        preprocess_config=preprocess_config,
        selection_report=report,
        created_at=now,
    )

    _save_profile(profile, profiles_dir)
    logger.info(
        "Created FeatureProfile %s: %d/%d factors selected",
        profile_id, len(selected_ids), len(all_factor_ids),
    )
    return profile


def refresh_feature_profile(
    profile_id: str,
    new_period: str,
    profiles_dir: Path | None = None,
) -> FeatureProfile:
    """Re-run selection with new period, create a new version."""
    old = load_feature_profile(profile_id, profiles_dir)
    selection_config = FeatureSelectionConfig(methods=old.methods)

    new_profile = create_feature_profile(
        universe=old.universe,
        period=new_period,
        zoo=old.zoo,
        selection_config=selection_config,
        preprocess_config=old.preprocess_config,
        profile_id=f"{old.profile_id}_v{old.version + 1}",
        profiles_dir=profiles_dir,
    )
    return new_profile


def load_feature_profile(
    profile_id: str, profiles_dir: Path | None = None,
) -> FeatureProfile:
    """Load a saved FeatureProfile."""
    base = profiles_dir or _PROFILES_DIR
    path = base / profile_id / "profile.json"
    if not path.exists():
        raise FileNotFoundError(f"FeatureProfile not found: {path}")

    data = json.loads(path.read_text(encoding="utf-8"))
    data["preprocess_config"] = PreprocessConfig(**data["preprocess_config"])
    return FeatureProfile(**data)


def list_feature_profiles(
    profiles_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """List all saved profiles with summary info."""
    base = profiles_dir or _PROFILES_DIR
    if not base.exists():
        return []

    profiles = []
    for profile_dir in sorted(base.iterdir()):
        path = profile_dir / "profile.json"
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                profiles.append({
                    "profile_id": data["profile_id"],
                    "zoo": data["zoo"],
                    "universe": data["universe"],
                    "n_factors": len(data["selected_factor_ids"]),
                    "methods": data["methods"],
                    "created_at": data["created_at"],
                })
            except Exception as exc:
                logger.warning("Failed to read profile %s: %s", profile_dir.name, exc)
    return profiles


def _save_profile(profile: FeatureProfile, profiles_dir: Path | None = None) -> None:
    base = profiles_dir or _PROFILES_DIR
    path = base / profile.profile_id
    path.mkdir(parents=True, exist_ok=True)

    data = asdict(profile)
    data["preprocess_config"] = asdict(profile.preprocess_config)
    (path / "profile.json").write_text(
        json.dumps(data, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
