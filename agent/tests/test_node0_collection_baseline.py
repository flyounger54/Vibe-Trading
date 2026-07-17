"""Tests for the node 0 pytest collection baseline verifier."""

from __future__ import annotations

import importlib.util
from pathlib import Path


_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_node0_collection.py"
_SPEC = importlib.util.spec_from_file_location("check_node0_collection", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


def test_parse_collection_errors_deduplicates_paths() -> None:
    output = """
________ ERROR collecting agent/tests/test_old_loader.py ________
ERROR collecting agent/tests/test_second_loader.py
________ ERROR collecting agent/tests/test_old_loader.py ________
"""

    assert _MODULE.parse_collection_errors(output) == {
        "agent/tests/test_old_loader.py",
        "agent/tests/test_second_loader.py",
    }


def test_load_expected_ignores_comments_and_blank_lines(tmp_path: Path) -> None:
    baseline = tmp_path / "known.txt"
    baseline.write_text("# comment\n\nagent/tests/test_old.py\n", encoding="utf-8")

    assert _MODULE.load_expected(baseline) == {"agent/tests/test_old.py"}
