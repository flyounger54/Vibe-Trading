#!/usr/bin/env python3
"""Verify pytest collection errors and, optionally, the exact test count."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


_COLLECTION_ERROR = re.compile(
    r"^.*?ERROR collecting (?P<path>\S+?\.py)(?:\s|$)",
    re.MULTILINE,
)
_COLLECTION_COUNT = re.compile(r"^(?P<count>\d+) tests collected.*$", re.MULTILINE)


def parse_collection_errors(output: str) -> set[str]:
    """Return normalized test paths from pytest collection error headings."""

    return {match.group("path").strip() for match in _COLLECTION_ERROR.finditer(output)}


def parse_collection_count(output: str) -> int | None:
    """Return the final pytest collection count, when present."""

    matches = list(_COLLECTION_COUNT.finditer(output))
    return int(matches[-1].group("count")) if matches else None


def load_expected(path: Path) -> set[str]:
    return {
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


def run_collection() -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        "-m",
        "pytest",
        "--collect-only",
        "-q",
        "--ignore=agent/tests/e2e_backtest",
        "--ignore=agent/tests/test_e2e_harness_v2.py",
    ]
    return subprocess.run(command, capture_output=True, text=True, check=False)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) not in {1, 2}:
        print(
            "usage: check_node0_collection.py EXPECTED_ERRORS [EXPECTED_COUNT]",
            file=sys.stderr,
        )
        return 2

    expected_path = Path(args[0])
    expected = load_expected(expected_path)
    try:
        expected_count = int(args[1]) if len(args) == 2 else None
    except ValueError:
        print("EXPECTED_COUNT must be an integer", file=sys.stderr)
        return 2
    result = run_collection()
    output = result.stdout + result.stderr
    actual = parse_collection_errors(output)
    actual_count = parse_collection_count(output)
    summaries = re.findall(r"^\d+ tests collected.*$", output, flags=re.MULTILINE)
    print(f"pytest collection exit: {result.returncode}")
    for path in sorted(actual):
        print(f"  collection error: {path}")
    if summaries:
        print(summaries[-1])

    if actual != expected:
        added = sorted(actual - expected)
        removed = sorted(expected - actual)
        if added:
            print("Unexpected collection errors:", *added, sep="\n  ", file=sys.stderr)
        if removed:
            print("Expected baseline errors no longer present:", *removed, sep="\n  ", file=sys.stderr)
        return 1

    if expected and result.returncode == 0:
        print("pytest unexpectedly returned 0 while baseline collection errors remain", file=sys.stderr)
        return 1
    if not expected and result.returncode != 0:
        print(f"pytest collection failed with exit {result.returncode}", file=sys.stderr)
        return 1

    if expected_count is not None and actual_count != expected_count:
        print(
            f"pytest collected {actual_count!r} tests; expected exactly {expected_count}",
            file=sys.stderr,
        )
        return 1

    count_suffix = f", {actual_count} test(s)" if actual_count is not None else ""
    print(f"Collection baseline matched: {len(expected)} known error(s){count_suffix}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
