#!/usr/bin/env python3
"""Enforce Node 5 correctness-core line and branch coverage thresholds."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 4:
        print("usage: check-node5-coverage.py COVERAGE_JSON MIN_LINE MIN_BRANCH")
        return 2
    payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    totals = payload["totals"]
    line = totals["covered_lines"] / max(totals["num_statements"], 1) * 100
    branch = totals["covered_branches"] / max(totals["num_branches"], 1) * 100
    min_line = float(sys.argv[2])
    min_branch = float(sys.argv[3])
    print(f"Node 5 correctness-core coverage: line={line:.2f}% branch={branch:.2f}%")
    if line < min_line or branch < min_branch:
        print(
            f"coverage gate failed: required line>={min_line:.2f}% "
            f"branch>={min_branch:.2f}%",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
