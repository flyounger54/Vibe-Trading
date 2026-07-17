#!/usr/bin/env python3
"""Verify that Vitest failures match the frozen node 0 frontend baseline."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_FAILURE = re.compile(r"^\s*FAIL\s+(?P<test>src/.*)$", re.MULTILINE)


def parse_frontend_failures(output: str) -> set[str]:
    clean = _ANSI.sub("", output)
    return {match.group("test").strip() for match in _FAILURE.finditer(clean)}


def load_expected(path: Path) -> set[str]:
    return {
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: check_node0_frontend.py EXPECTED_FAILURES", file=sys.stderr)
        return 2

    repo_root = Path(__file__).resolve().parents[1]
    expected = load_expected(Path(args[0]))
    result = subprocess.run(
        ["npm", "run", "test:run"],
        cwd=repo_root / "frontend",
        capture_output=True,
        text=True,
        check=False,
    )
    output = _ANSI.sub("", result.stdout + result.stderr)
    actual = parse_frontend_failures(output)
    print(f"Vitest exit: {result.returncode}")
    for test in sorted(actual):
        print(f"  failed: {test}")
    for pattern in (r"^\s*Test Files\s+.*$", r"^\s*Tests\s+.*$", r"^\s*Duration\s+.*$"):
        matches = re.findall(pattern, output, flags=re.MULTILINE)
        if matches:
            print(matches[-1].strip())

    if actual != expected:
        for test in sorted(actual - expected):
            print(f"unexpected frontend failure: {test}", file=sys.stderr)
        for test in sorted(expected - actual):
            print(f"expected frontend failure no longer present: {test}", file=sys.stderr)
        return 1
    if expected and result.returncode == 0:
        print("Vitest unexpectedly returned 0 while baseline failures remain", file=sys.stderr)
        return 1
    if not expected and result.returncode != 0:
        print(f"Vitest failed with exit {result.returncode}", file=sys.stderr)
        return 1

    print(f"Node 0 frontend baseline matched: {len(expected)} known failure(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

