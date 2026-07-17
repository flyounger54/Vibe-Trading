"""Node 7 contracts: one deterministic catalog and honest strategy runnability."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.catalog.manifest import (
    CATALOG_SCHEMA_VERSION,
    build_catalog_manifest,
    render_catalog_markdown,
    write_catalog_assets,
)
from src.strategies.registry import (
    StrategyConfigurationError,
    StrategyRegistry,
)


def test_catalog_is_deterministic_and_matches_runtime_registries() -> None:
    """A catalog is a complete, stable projection of discovered components."""
    first = build_catalog_manifest()
    second = build_catalog_manifest()

    assert first == second
    assert first["schema_version"] == CATALOG_SCHEMA_VERSION
    assert first["components"]["strategies"]["health"]["failed"] == 0
    assert first["components"]["alphas"]["health"]["failed"] == 0
    assert first["components"]["skills"]["health"]["failed"] == 0
    assert first["components"]["presets"]["health"]["failed"] == 0

    counts = first["counts"]
    assert counts["strategies"] == len(first["components"]["strategies"]["items"])
    assert counts["alphas"] == len(first["components"]["alphas"]["items"])
    assert counts["skills"] == len(first["components"]["skills"]["items"])
    assert counts["presets"] == len(first["components"]["presets"]["items"])


def test_every_strategy_has_an_operational_contract_in_catalog() -> None:
    manifest = build_catalog_manifest()
    strategies = manifest["components"]["strategies"]["items"]

    assert strategies
    for strategy in strategies:
        meta = strategy["meta"]
        for field in (
            "required_params",
            "required_any_of",
            "configuration_requirements",
            "directly_runnable",
            "universe",
            "columns_required",
            "risk_profile",
        ):
            assert field in meta, f"{strategy['id']}: missing {field}"


def test_external_model_strategy_is_not_in_default_runnable_set() -> None:
    registry = StrategyRegistry()

    assert "mf_ml_predictor" not in registry.list(directly_runnable=True)
    assert "mf_ml_predictor" in registry.list(directly_runnable=False)
    with pytest.raises(StrategyConfigurationError, match="model_id.*schedule_name"):
        registry.load("mf_ml_predictor")

    engine = registry.load("mf_ml_predictor", model_id="demo-model")
    assert hasattr(engine, "generate")


def test_catalog_writer_produces_json_and_markdown_from_same_snapshot(tmp_path: Path) -> None:
    json_path, markdown_path, manifest = write_catalog_assets(tmp_path)

    assert json.loads(json_path.read_text(encoding="utf-8")) == manifest
    assert markdown_path.read_text(encoding="utf-8") == render_catalog_markdown(manifest)
    assert str(manifest["counts"]["strategies"]) in markdown_path.read_text(encoding="utf-8")


def test_tracked_catalog_docs_are_fresh() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    manifest = build_catalog_manifest()
    json_path = repo_root / "wiki" / "docs" / "catalog" / "runtime-catalog.json"
    markdown_path = repo_root / "wiki" / "docs" / "catalog" / "runtime-catalog.md"

    assert json_path.exists(), "run: python agent/scripts/build_catalog.py"
    assert markdown_path.exists(), "run: python agent/scripts/build_catalog.py"
    assert json.loads(json_path.read_text(encoding="utf-8")) == manifest
    assert markdown_path.read_text(encoding="utf-8") == render_catalog_markdown(manifest)
