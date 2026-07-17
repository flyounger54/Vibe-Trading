"""Build the distributable Vibe-Trading component catalog.

The catalog is deliberately derived from the same discoverers used at runtime:
the strategy and alpha registries, the bundled skills loader, and the swarm
preset loader.  It has no clock field, so its content hash and rendered files
are reproducible and CI can detect documentation drift.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from src.agent.skills import SkillsLoader
from src.factors.registry import Registry
from src.strategies.registry import StrategyRegistry
from src.swarm.presets import PRESETS_DIR, list_presets, load_preset

CATALOG_SCHEMA_VERSION = "vibe-trading.runtime-catalog.v1"
_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
_GENERATED_DIRECTORY = _REPOSITORY_ROOT / "wiki" / "docs" / "catalog"


def _canonical_json(value: Any) -> str:
    """Return a stable JSON representation suitable for a content fingerprint."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _content_hash(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _bundled_skills_loader() -> SkillsLoader:
    """Load only package-shipped skills, excluding machine-local user overrides.

    User skills are intentionally runtime-local and must not make the checked-in
    package catalog non-reproducible.  The normal ``SkillsLoader`` still loads
    those overrides for interactive sessions.
    """
    skills_dir = Path(__file__).resolve().parents[1] / "skills"
    absent_user_dir = skills_dir / ".catalog-user-overrides-disabled"
    return SkillsLoader(skills_dir=skills_dir, user_skills_dir=absent_user_dir)


def _strategy_component(registry: StrategyRegistry) -> dict[str, Any]:
    items = [
        {
            "id": strategy.id,
            "category": strategy.category,
            "module_path": strategy.module_path,
            "meta": strategy.meta,
        }
        for strategy in sorted(
            (registry.get(strategy_id) for strategy_id in registry.list()),
            key=lambda strategy: strategy.id,
        )
    ]
    return {"health": registry.health(), "items": items}


def _alpha_component(registry: Registry) -> dict[str, Any]:
    items = [
        {
            "id": alpha.id,
            "zoo": alpha.zoo,
            "module_path": alpha.module_path,
            "meta": alpha.meta,
        }
        for alpha in sorted(
            (registry.get(alpha_id) for alpha_id in registry.list()),
            key=lambda alpha: alpha.id,
        )
    ]
    return {"health": registry.health(), "items": items}


def _skills_component(loader: SkillsLoader) -> dict[str, Any]:
    items = [
        {
            "name": skill.name,
            "description": skill.description,
            "category": skill.category,
            "metadata": skill.metadata,
        }
        for skill in sorted(loader.skills, key=lambda skill: skill.name)
    ]
    expected_paths = {
        path.resolve()
        for path in loader.skills_dir.iterdir()
        if path.is_dir() and (path / "SKILL.md").exists()
    } if loader.skills_dir.exists() else set()
    loaded_paths = {skill.dir_path.resolve() for skill in loader.skills if skill.dir_path}
    failed_paths = sorted(expected_paths - loaded_paths)
    return {
        "health": {
            "loaded": len(items),
            "failed": len(failed_paths),
            "errors": [
                {"path": str(path.relative_to(_REPOSITORY_ROOT)), "reason": "skill could not be loaded"}
                for path in failed_paths
            ],
        },
        "items": items,
    }


def _presets_component() -> dict[str, Any]:
    errors: list[dict[str, str]] = []
    if PRESETS_DIR.exists():
        for path in sorted(PRESETS_DIR.glob("*.yaml")):
            try:
                payload = load_preset(path.stem)
                if not isinstance(payload, dict):
                    raise ValueError("preset root must be a mapping")
            except Exception as exc:  # noqa: BLE001 - catalog reports all broken presets
                errors.append({"path": str(path.relative_to(_REPOSITORY_ROOT)), "reason": str(exc)})
    items = sorted(list_presets(), key=lambda preset: str(preset["name"]))
    return {
        "health": {"loaded": len(items), "failed": len(errors), "errors": errors},
        "items": items,
    }


def build_catalog_manifest(
    *,
    strategy_registry: StrategyRegistry | None = None,
    alpha_registry: Registry | None = None,
    skills_loader: SkillsLoader | None = None,
) -> dict[str, Any]:
    """Return a deterministic catalog snapshot of all bundled components."""
    strategies = _strategy_component(strategy_registry or StrategyRegistry())
    alphas = _alpha_component(alpha_registry or Registry())
    skills = _skills_component(skills_loader or _bundled_skills_loader())
    presets = _presets_component()

    components = {
        "strategies": strategies,
        "alphas": alphas,
        "skills": skills,
        "presets": presets,
    }
    counts = {
        "strategies": len(strategies["items"]),
        "strategy_categories": len({item["category"] for item in strategies["items"]}),
        "alphas": len(alphas["items"]),
        "alpha_zoos": len({item["zoo"] for item in alphas["items"]}),
        "skills": len(skills["items"]),
        "skill_categories": len({item["category"] for item in skills["items"]}),
        "presets": len(presets["items"]),
    }
    payload = {
        "schema_version": CATALOG_SCHEMA_VERSION,
        "counts": counts,
        "components": components,
    }
    return {**payload, "content_sha256": _content_hash(payload)}


def _group_counts(items: list[dict[str, Any]], key: str) -> list[tuple[str, int]]:
    return sorted(Counter(str(item[key]) for item in items).items())


def render_catalog_markdown(manifest: dict[str, Any]) -> str:
    """Render the human-readable catalog summary from one manifest snapshot."""
    counts = manifest["counts"]
    components = manifest["components"]
    lines = [
        "<!-- GENERATED by `python agent/scripts/build_catalog.py`; do not edit manually. -->",
        "# Vibe-Trading Runtime Catalog",
        "",
        "This inventory is generated from the same bundled discoverers used by the runtime.",
        "",
        f"Content fingerprint: `{manifest['content_sha256']}`",
        "",
        "## Inventory",
        "",
        "| Component | Discovered | Groups |",
        "| --- | ---: | ---: |",
        f"| Strategies | {counts['strategies']} | {counts['strategy_categories']} categories |",
        f"| Alpha Zoo | {counts['alphas']} | {counts['alpha_zoos']} zoos |",
        f"| Finance skills | {counts['skills']} | {counts['skill_categories']} categories |",
        f"| Swarm presets | {counts['presets']} | — |",
        "",
        "## Registry health",
        "",
        f"- Strategies: {components['strategies']['health']['loaded']} loaded, "
        f"{components['strategies']['health']['failed']} failed",
        f"- Alpha Zoo: {components['alphas']['health']['loaded']} loaded, "
        f"{components['alphas']['health']['failed']} failed",
        f"- Finance skills: {components['skills']['health']['loaded']} loaded, "
        f"{components['skills']['health']['failed']} failed",
        f"- Swarm presets: {components['presets']['health']['loaded']} loaded, "
        f"{components['presets']['health']['failed']} failed",
        "",
        "## Strategies by category",
        "",
    ]
    lines.extend(
        f"- `{name}`: {count}" for name, count in _group_counts(components["strategies"]["items"], "category")
    )
    lines.extend(["", "## Alpha Zoo by zoo", ""])
    lines.extend(
        f"- `{name}`: {count}" for name, count in _group_counts(components["alphas"]["items"], "zoo")
    )
    lines.extend(["", "## Skills by category", ""])
    lines.extend(
        f"- `{name}`: {count}" for name, count in _group_counts(components["skills"]["items"], "category")
    )
    lines.extend(["", "## Swarm presets", ""])
    lines.extend(f"- `{preset['name']}` — {preset.get('title', '')}" for preset in components["presets"]["items"])
    return "\n".join(lines) + "\n"


def write_catalog_assets(output_dir: Path | None = None) -> tuple[Path, Path, dict[str, Any]]:
    """Build and write the canonical JSON manifest and Markdown catalog."""
    destination = output_dir or _GENERATED_DIRECTORY
    destination.mkdir(parents=True, exist_ok=True)
    manifest = build_catalog_manifest()
    json_path = destination / "runtime-catalog.json"
    markdown_path = destination / "runtime-catalog.md"
    json_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text(render_catalog_markdown(manifest), encoding="utf-8")
    return json_path, markdown_path, manifest
