#!/usr/bin/env python3
"""Verify Vitest failures and, optionally, exact file/test counts."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_FAILURE = re.compile(r"^\s*FAIL\s+(?P<test>src/.*)$", re.MULTILINE)
_FILE_COUNT = re.compile(r"^\s*Test Files\s+.*?\((?P<count>\d+)\)\s*$", re.MULTILINE)
_TEST_COUNT = re.compile(r"^\s*Tests\s+.*?\((?P<count>\d+)\)\s*$", re.MULTILINE)


def parse_frontend_failures(output: str) -> set[str]:
    clean = _ANSI.sub("", output)
    return {match.group("test").strip() for match in _FAILURE.finditer(clean)}


def parse_frontend_counts(output: str) -> tuple[int | None, int | None]:
    """Return the final Vitest test-file and test counts."""

    clean = _ANSI.sub("", output)
    file_matches = list(_FILE_COUNT.finditer(clean))
    test_matches = list(_TEST_COUNT.finditer(clean))
    file_count = int(file_matches[-1].group("count")) if file_matches else None
    test_count = int(test_matches[-1].group("count")) if test_matches else None
    return file_count, test_count


def load_expected(path: Path) -> set[str]:
    return {
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) not in {1, 3}:
        print(
            "usage: check_node0_frontend.py EXPECTED_FAILURES "
            "[EXPECTED_FILE_COUNT EXPECTED_TEST_COUNT]",
            file=sys.stderr,
        )
        return 2

    repo_root = Path(__file__).resolve().parents[1]
    expected = load_expected(Path(args[0]))
    try:
        expected_counts = (int(args[1]), int(args[2])) if len(args) == 3 else None
    except ValueError:
        print("EXPECTED_FILE_COUNT and EXPECTED_TEST_COUNT must be integers", file=sys.stderr)
        return 2
    result = subprocess.run(
        ["npm", "run", "test:run"],
        cwd=repo_root / "frontend",
        capture_output=True,
        text=True,
        check=False,
    )
    output = _ANSI.sub("", result.stdout + result.stderr)
    actual = parse_frontend_failures(output)
    actual_counts = parse_frontend_counts(output)
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

    if expected_counts is not None and actual_counts != expected_counts:
        print(
            "Vitest count mismatch: "
            f"files/tests={actual_counts}, expected={expected_counts}",
            file=sys.stderr,
        )
        return 1

    print(
        "Frontend baseline matched: "
        f"{len(expected)} known failure(s), files/tests={actual_counts}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
