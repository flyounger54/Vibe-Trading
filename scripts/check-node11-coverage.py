#!/usr/bin/env python3
"""Enforce the accepted Node 11 backend and critical-path coverage targets."""

from __future__ import annotations

import json
import sys
from pathlib import Path


RELEASE_TARGET = (80.0, 70.0)
CRITICAL_FILES = (
    "agent/src/security/api_security.py",
    "agent/src/live/order_guard.py",
    "agent/src/live/sdk_order_gate.py",
)
CRITICAL_TARGET = (95.0, 90.0)


def _rates(summary: dict[str, float]) -> tuple[float, float]:
    line = float(summary["covered_lines"]) / max(float(summary["num_statements"]), 1) * 100
    branch = float(summary["covered_branches"]) / max(float(summary["num_branches"]), 1) * 100
    return line, branch


def _combined(files: dict[str, dict], names: tuple[str, ...]) -> dict[str, int]:
    fields = ("covered_lines", "num_statements", "covered_branches", "num_branches")
    return {field: sum(int(files[name]["summary"][field]) for name in names) for field in fields}


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: check-node11-coverage.py COVERAGE_JSON", file=sys.stderr)
        return 2
    payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    global_line, global_branch = _rates(payload["totals"])
    critical_line, critical_branch = _rates(_combined(payload["files"], CRITICAL_FILES))
    print(
        f"backend coverage: global line={global_line:.2f}% branch={global_branch:.2f}%; "
        f"critical line={critical_line:.2f}% branch={critical_branch:.2f}%"
    )
    print(
        f"Node 11 release targets: global {RELEASE_TARGET[0]:.0f}/{RELEASE_TARGET[1]:.0f}; "
        f"critical {CRITICAL_TARGET[0]:.0f}/{CRITICAL_TARGET[1]:.0f} (line/branch)"
    )
    failed = (
        global_line < RELEASE_TARGET[0]
        or global_branch < RELEASE_TARGET[1]
        or critical_line < CRITICAL_TARGET[0]
        or critical_branch < CRITICAL_TARGET[1]
    )
    if failed:
        print("coverage is below the Node 11 release target", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
