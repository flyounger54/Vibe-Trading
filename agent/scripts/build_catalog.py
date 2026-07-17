#!/usr/bin/env python3
"""Generate the checked-in runtime catalog from bundled discoverers."""

from __future__ import annotations

import sys
from pathlib import Path

AGENT_ROOT = Path(__file__).resolve().parents[1]
if str(AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENT_ROOT))

from src.catalog.manifest import write_catalog_assets  # noqa: E402


def main() -> int:
    json_path, markdown_path, manifest = write_catalog_assets()
    counts = manifest["counts"]
    print(
        "Generated runtime catalog: "
        f"{counts['strategies']} strategies, {counts['alphas']} alphas, "
        f"{counts['skills']} skills, {counts['presets']} presets\n"
        f"  {json_path}\n  {markdown_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
