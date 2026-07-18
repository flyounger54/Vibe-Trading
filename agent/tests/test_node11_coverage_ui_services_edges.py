"""Offline branch coverage for frontend-facing run analysis services."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

import src.ui_services as ui


pytestmark = pytest.mark.unit


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_date_json_csv_and_code_normalization_edges(tmp_path: Path) -> None:
    assert ui.format_run_date("   ") is None
    assert ui.format_run_date("20260718") == "2026-07-18"
    assert ui.format_run_date("2026-07-18 12:00:00") == "2026-07-18"
    assert ui.format_run_date("unparsed") == "unparsed"

    invalid_json = tmp_path / "invalid.json"
    invalid_json.write_text("{", encoding="utf-8")
    assert ui.load_json_file(invalid_json) is None
    assert ui.load_json_file(tmp_path / "missing.json") is None
    assert ui.load_csv_records(tmp_path / "missing.csv") == []
    assert ui.load_csv_records(tmp_path) == []
    assert ui.normalize_codes(" AAPL, ,MSFT ") == ["AAPL", "MSFT"]
    assert ui.normalize_codes(123) == []


def test_run_context_planner_data_requirement_fallbacks(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "req.json",
        {"prompt": " research ", "context": {"start_date": "", "codes": []}},
    )
    _write_json(
        tmp_path / "planner_output.json",
        {
            "coding_contract": {
                "end_date": "20260718",
                "data_requirements": [
                    {"symbol_scope": 123},
                    {"symbol_scope": " "},
                    {"symbol_scope": " AAPL, MSFT, AAPL "},
                ],
            },
            "requirements": {"context": {"start_date": "2026-01-01"}},
        },
    )
    context = ui.load_run_context(tmp_path)
    assert context["prompt"] == "research"
    assert context["codes"] == ["AAPL", "MSFT"]
    assert context["start_date"] == "2026-01-01"
    assert context["end_date"] == "2026-07-18"

    _write_json(
        tmp_path / "planner_output.json",
        {
            "coding_contract": {"target_scope": "TSLA", "start_date": "20260102"},
            "requirements": {"context": {"codes": "ignored", "end_date": "20260103"}},
        },
    )
    context = ui.load_run_context(tmp_path)
    assert context["codes"] == ["TSLA"] and context["start_date"] == "2026-01-02"


def test_indicator_period_inference_valid_invalid_and_default(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "planner_output.json",
        {
            "coding_contract": {
                "input_logic": {
                    "parameters": {
                        "signal_params": {"fast_ma": "7", "bad_ma": "x", "rsi": 14}
                    }
                }
            }
        },
    )
    _write_json(
        tmp_path / "design_spec.json",
        {
            "defaults_and_tunables": {
                "parameter_assumptions": {"slow_ma": 30, "other_ma": None, "atr": 3}
            }
        },
    )
    assert ui.infer_indicator_periods(tmp_path) == [7, 30]
    empty = tmp_path / "empty"
    empty.mkdir()
    assert ui.infer_indicator_periods(empty) == ui.DEFAULT_ANALYSIS_PERIODS


@pytest.mark.parametrize(
    ("marker", "expected"),
    [
        ("failed_state", "failed"),
        ("metrics", "backtest"),
        ("review", "review"),
        ("code", "coding"),
        ("design", "design"),
        ("planner", "planning"),
        ("request", "queued"),
        ("none", "unknown"),
    ],
)
def test_infer_run_stage_precedence(
    tmp_path: Path, marker: str, expected: str
) -> None:
    run = tmp_path / marker
    run.mkdir()
    if marker == "failed_state":
        _write_json(run / "state.json", {"status": "FAILED"})
    elif marker == "metrics":
        (run / "artifacts").mkdir()
        (run / "artifacts" / "metrics.csv").touch()
    elif marker == "review":
        (run / "review_report.json").touch()
    elif marker == "code":
        (run / "code").mkdir()
        (run / "code" / "signal_engine.py").touch()
    elif marker in {"design", "planner", "request"}:
        filename = {"design": "design_spec.json", "planner": "planner_output.json", "request": "req.json"}[marker]
        (run / filename).touch()
    assert ui.infer_run_stage(run) == expected


def test_log_collection_indicator_warmup_and_safe_float(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "runner_stdout.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
    (logs / "runner_stderr.txt").write_text("error\n", encoding="utf-8")
    assert [row["message"] for row in ui.collect_run_logs(tmp_path, 2)] == [
        "two",
        "three",
        "error",
    ]
    assert len(ui.collect_run_logs(tmp_path, 0)) == 4

    indicators = ui.build_indicator_series(
        [
            {"code": "A", "timestamp": "2026-01-02", "close": 2},
            {"code": "A", "time": "2026-01-01", "close": 1},
        ],
        [2],
    )
    assert indicators["A"]["ma2"] == [
        {"time": "2026-01-01", "value": None},
        {"time": "2026-01-02", "value": 1.5},
    ]
    assert ui._safe_float("") is None
    assert ui._safe_float("bad") is None
    assert ui._safe_float("1.5") == 1.5


def test_ohlcv_artifact_loading_and_price_source_precedence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert ui._load_ohlcv_artifacts(tmp_path) == []
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    assert ui._load_ohlcv_artifacts(tmp_path) == []
    first = artifacts / "ohlcv_AAPL.csv"
    second = artifacts / "ohlcv_MSFT.csv"
    first.touch()
    second.touch()

    def fake_records(path: Path) -> list[dict[str, object]]:
        if path == first:
            return [
                {"trade_date": "2026-01-01", "open": 1, "close": 2},
                {"timestamp": "2026-01-02", "open": 2, "close": 3},
                {"time": "2026-01-03", "open": 3, "close": 4},
                {"": "2026-01-04", "open": 4, "close": 5},
                {"close": 6},
            ]
        return []

    monkeypatch.setattr(ui, "load_csv_records", fake_records)
    rows = ui._load_ohlcv_artifacts(tmp_path)
    assert len(rows) == 4 and {row["code"] for row in rows} == {"AAPL"}

    price_file = artifacts / "price_series.csv"
    price_file.touch()
    monkeypatch.setattr(ui, "load_csv_records", lambda path: [{"time": "2026-01-01", "code": "P", "close": 1}])
    assert ui.load_price_series(tmp_path)[0]["code"] == "P"
    price_file.unlink()
    monkeypatch.setattr(ui, "_load_ohlcv_artifacts", lambda run: [{"code": "O"}])
    assert ui.load_price_series(tmp_path) == [{"code": "O"}]
    monkeypatch.setattr(ui, "_load_ohlcv_artifacts", lambda run: [])
    monkeypatch.setattr(ui, "reconstruct_price_series", lambda run: [{"code": "R"}])
    assert ui.load_price_series(tmp_path) == [{"code": "R"}]


def test_chart_symbol_csv_ohlcv_and_context_fallbacks(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    price = artifacts / "price_series.csv"
    price.write_text("code\nAAPL\n\nMSFT\n", encoding="utf-8")
    assert ui.load_chart_symbols(tmp_path) == ["AAPL", "MSFT"]
    price.unlink()
    (artifacts / "ohlcv_TSLA.csv").touch()
    assert ui.load_chart_symbols(tmp_path) == ["TSLA"]
    (artifacts / "ohlcv_TSLA.csv").unlink()
    assert ui.load_chart_symbols(tmp_path, {"codes": ["NVDA", ""]}) == ["NVDA"]


def test_reconstruct_guard_loader_matrix_and_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ui, "load_run_context", lambda run: {"codes": []})
    assert ui.reconstruct_price_series(tmp_path) == []
    context = {
        "codes": ["AAPL"],
        "start_date": "2026-01-01",
        "end_date": "2026-01-03",
        "source": "astock",
    }
    monkeypatch.setattr(ui, "load_run_context", lambda run: context)
    assert ui.reconstruct_price_series(tmp_path) == []
    signal = tmp_path / "code" / "signal_engine.py"
    signal.parent.mkdir()
    signal.touch()

    import src.providers.llm as llm

    monkeypatch.setattr(llm, "_ensure_dotenv", lambda: (_ for _ in ()).throw(RuntimeError("bad env")))
    frame = pd.DataFrame(
        {"open": [1], "high": [2], "low": [0], "close": [1.5], "volume": [4]},
        index=pd.to_datetime(["2026-01-02"]),
    )

    class Loader:
        result: object = {"AAPL": frame}

        def fetch(self, codes: list[str], start: str, end: str) -> object:
            if isinstance(self.result, Exception):
                raise self.result
            return self.result

    module_names = {
        "okx": "backtest.loaders.okx",
        "global": "backtest.loaders.global_loader",
        "astock": "backtest.loaders.astock_loader",
        "other": "backtest.loaders.tushare",
    }
    for source, module_name in module_names.items():
        context["source"] = source
        monkeypatch.setitem(sys.modules, module_name, SimpleNamespace(DataLoader=Loader))
        assert ui.reconstruct_price_series(tmp_path)[0]["code"] == "AAPL"

    context["source"] = "astock"
    Loader.result = RuntimeError("offline")
    assert ui.reconstruct_price_series(tmp_path) == []
    Loader.result = {}
    assert ui.reconstruct_price_series(tmp_path) == []


def test_build_analysis_symbol_recovery_fetch_lookback_and_flatten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = [{"time": "2026-01-02", "code": "A", "open": 1, "high": 2, "low": 0, "close": 1.5, "volume": 3}]
    monkeypatch.setattr(ui, "load_run_context", lambda run: {"codes": []})
    monkeypatch.setattr(ui, "load_chart_symbols", lambda run, context: [])
    monkeypatch.setattr(ui, "load_price_series", lambda run: rows)
    monkeypatch.setattr(ui, "infer_indicator_periods", lambda run: [1])
    monkeypatch.setattr(ui, "load_csv_records", lambda path: [])
    monkeypatch.setattr(ui, "collect_run_logs", lambda run: [])
    result = ui.build_run_analysis(tmp_path, include_symbol_list=True)
    assert result["chart_symbols"] == ["A"]

    assert ui._compute_fetch_start_date(tmp_path, "2026-01-10") == "2026-01-10"
    _write_json(
        tmp_path / "planner_output.json",
        {"coding_contract": {"data_lookback_days": 10}},
    )
    assert ui._compute_fetch_start_date(tmp_path, "2026-01-20") == "2025-12-26"

    normalized = ui._normalize_price_rows(
        [
            {"time": "", "close": 1},
            {"timestamp": "2026-01-02", "code": "B", "close": "2"},
        ]
    )
    assert normalized[0]["code"] == "B"

    frame = pd.DataFrame(
        {"open": [1, 2], "high": [2, 3], "low": [0, 1], "close": [1.5, 2.5]},
        index=["2025-12-31", "2026-01-02"],
    )
    flattened = ui._flatten_data_map({"A": frame, "EMPTY": frame.iloc[:0]}, "2026-01-01")
    assert len(flattened) == 1 and flattened[0]["code"] == "A"
