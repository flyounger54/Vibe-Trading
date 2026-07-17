"""Tests for the node 0 frontend failure baseline verifier."""

from __future__ import annotations

import importlib.util
from pathlib import Path


_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_node0_frontend.py"
_SPEC = importlib.util.spec_from_file_location("check_node0_frontend", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


def test_parse_frontend_failures_strips_ansi_and_deduplicates() -> None:
    output = (
        "\x1b[31m FAIL  src/a.test.ts > suite > case\x1b[0m\n"
        " FAIL  src/b.test.tsx > suite > other\n"
        " FAIL  src/a.test.ts > suite > case\n"
    )

    assert _MODULE.parse_frontend_failures(output) == {
        "src/a.test.ts > suite > case",
        "src/b.test.tsx > suite > other",
    }


def test_load_expected_ignores_comments(tmp_path: Path) -> None:
    baseline = tmp_path / "known.txt"
    baseline.write_text("# comment\nsrc/a.test.ts > suite > case\n", encoding="utf-8")

    assert _MODULE.load_expected(baseline) == {"src/a.test.ts > suite > case"}


def test_parse_frontend_counts_strips_ansi() -> None:
    output = (
        "\x1b[32m Test Files  27 passed (27)\x1b[0m\n"
        "\x1b[32m      Tests  227 passed (227)\x1b[0m\n"
    )

    assert _MODULE.parse_frontend_counts(output) == (27, 227)
