"""Runtime-discovered catalog for strategies, Alpha Zoo, skills, and presets."""

from src.catalog.manifest import (
    CATALOG_SCHEMA_VERSION,
    build_catalog_manifest,
    render_catalog_markdown,
    write_catalog_assets,
)

__all__ = [
    "CATALOG_SCHEMA_VERSION",
    "build_catalog_manifest",
    "render_catalog_markdown",
    "write_catalog_assets",
]
