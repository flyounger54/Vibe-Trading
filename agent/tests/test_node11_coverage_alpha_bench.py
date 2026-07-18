"""Offline orchestration and failure contracts for the alpha benchmark tool."""

from __future__ import annotations

import json
import pickle
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from src.tools import alpha_bench_tool as bench


pytestmark = pytest.mark.unit


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open": [1.0, 2.0, 3.0],
            "high": [2.0, 3.0, 4.0],
            "low": [0.5, 1.5, 2.5],
            "close": [1.5, 2.5, 3.5],
            "volume": [10.0, 20.0, 30.0],
            "amount": [100.0, 200.0, 300.0],
        },
        index=pd.date_range("2025-01-01", periods=3),
    )


def _panel(two_assets: bool = True) -> dict[str, pd.DataFrame]:
    first = _frame()
    fetched = {"A": first}
    if two_assets:
        fetched["B"] = first * 2
    return bench._wide_from_fetched(fetched, include_amount=True)


@pytest.mark.parametrize(
    "period,expected",
    [
        ("2020-2024", ("2020-01-01", "2024-12-31")),
        ("2020-01-02/2024-03-04", ("2020-01-02", "2024-03-04")),
    ],
)
def test_period_parsing(period, expected) -> None:
    assert bench._parse_period(period) == expected
    with pytest.raises(ValueError, match="must be string"):
        bench._parse_period(1)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="must be YYYY"):
        bench._parse_period("bad")


def test_universe_dispatch_cache_empty_and_single_asset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    with pytest.raises(ValueError, match="not recognized"):
        bench._load_universe_panel("unknown", "2020-2021")

    cached = _panel()
    cache = tmp_path / ".vibe-trading" / "cache" / "sp500_2020-01-01_2021-12-31.pkl"
    bench._write_pickle_cache(cache.parent, cache, cached)
    assert bench._load_universe_panel("sp500", "2020-2021")["close"].shape[1] == 2

    monkeypatch.setattr(bench, "_load_sp500_panel", lambda start, end: {})
    with pytest.raises(RuntimeError, match="empty panel"):
        bench._load_universe_panel("sp500", "2020-2021", use_cache=False)
    monkeypatch.setattr(bench, "_load_btc_panel", lambda start, end: _panel(False))
    with pytest.raises(ValueError, match="single-asset"):
        bench._load_universe_panel("btc-usdt", "2020-2021", use_cache=False)
    monkeypatch.setattr(bench, "_load_csi300_panel", lambda start, end: _panel())
    assert bench._load_universe_panel("csi300", "2020-2021", use_cache=False)["close"].shape[1] == 2


def test_pickle_cache_integrity_failure_matrix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache = tmp_path / "cache.pkl"
    assert bench._sha256_path(cache).name == "cache.pkl.sha256"
    assert bench._read_pickle_cache(cache) is None
    cache.write_bytes(b"payload")
    assert bench._read_pickle_cache(cache) is None
    sidecar = bench._sha256_path(cache)
    sidecar.write_text("bad", encoding="utf-8")
    assert bench._read_pickle_cache(cache) is None

    bad_pickle = b"not-pickle"
    cache.write_bytes(bad_pickle)
    sidecar.write_text(__import__("hashlib").sha256(bad_pickle).hexdigest(), encoding="utf-8")
    assert bench._read_pickle_cache(cache) is None

    wrong_shape = pickle.dumps([1, 2])
    cache.write_bytes(wrong_shape)
    sidecar.write_text(__import__("hashlib").sha256(wrong_shape).hexdigest(), encoding="utf-8")
    assert bench._read_pickle_cache(cache) is None

    panel = _panel()
    bench._write_pickle_cache(tmp_path, cache, panel)
    assert bench._read_pickle_cache(cache)["close"].equals(panel["close"])
    assert bench._hashes_equal(" ABC ", "abc")
    assert not bench._hashes_equal("abc", "def")

    original = Path.write_bytes

    def fail_write(path, data):
        if path == cache:
            raise OSError("readonly")
        return original(path, data)

    monkeypatch.setattr(Path, "write_bytes", fail_write)
    bench._write_pickle_cache(tmp_path, cache, panel)


def test_wide_panel_shapes_and_missing_fields() -> None:
    assert bench._wide_from_fetched({}, include_amount=True) == {}
    empty_index = _frame().iloc[0:0]
    assert bench._wide_from_fetched({"A": empty_index}, include_amount=False) == {}
    frame = _frame().drop(columns=["amount", "volume"])
    panel = bench._wide_from_fetched({"B": frame, "A": frame.iloc[1:]}, include_amount=True)
    assert sorted(panel) == ["close", "high", "low", "open"]
    assert list(panel["close"].columns) == ["A", "B"]
    assert len(panel["close"]) == 3


def test_retry_success_recovery_and_exhaustion(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 2:
            raise RuntimeError("retry")
        return "ok"

    assert bench._retry(flaky, tries=3, base_delay=0) == "ok"
    assert bench._retry(lambda: (_ for _ in ()).throw(RuntimeError("down")), tries=2, base_delay=0) is None


def test_forward_returns_and_alpha_statistics(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValueError, match="missing 'close'"):
        bench._compute_forward_returns({})
    returns = bench._compute_forward_returns(_panel())
    assert returns.iloc[-1].isna().all()

    registry = SimpleNamespace(
        compute=lambda alpha_id, panel: panel["close"],
        get=lambda alpha_id: SimpleNamespace(
            zoo="alpha101", meta={"theme": ["momentum"], "formula_latex": "x<y"}
        ),
    )
    monkeypatch.setattr(
        "src.factors.factor_analysis_core.compute_ic_series",
        lambda factor, ret: pd.Series([0.1, 0.2, -0.1]),
    )
    result = bench._bench_one_alpha(registry, "alpha101_001", _panel(), returns)
    assert result["ic_count"] == 3 and result["theme"] == ["momentum"]
    monkeypatch.setattr(
        "src.factors.factor_analysis_core.compute_ic_series",
        lambda factor, ret: pd.Series([0.5, 0.5]),
    )
    assert bench._bench_one_alpha(registry, "x", _panel(), returns)["ir"] == 0.0
    monkeypatch.setattr(
        "src.factors.factor_analysis_core.compute_ic_series",
        lambda factor, ret: pd.Series(dtype=float),
    )
    with pytest.raises(RuntimeError, match="IC series empty"):
        bench._bench_one_alpha(registry, "x", _panel(), returns)


def test_alpha_selection_contracts() -> None:
    registry = SimpleNamespace(
        get=lambda alpha_id: alpha_id,
        list=lambda **kwargs: [kwargs.get("zoo") or "all"],
    )
    with pytest.raises(ValueError, match="mutually exclusive"):
        bench._select_alpha_ids(registry, alpha_id="a", zoo="z")
    assert bench._select_alpha_ids(registry, alpha_id="a", zoo=None) == ["a"]
    assert bench._select_alpha_ids(registry, alpha_id=None, zoo="alpha101") == ["alpha101"]
    assert bench._select_alpha_ids(registry, alpha_id=None, zoo=None) == ["all"]


def test_sp500_btc_loader_and_constituent_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    frame = _frame().drop(columns=["amount"])
    loader = SimpleNamespace(fetch=lambda codes, start, end: {codes[0]: frame})
    monkeypatch.setattr("backtest.loaders.registry.resolve_loader", lambda market: loader)
    monkeypatch.setattr(bench, "_fetch_sp500_constituents", lambda: ["BRK-B"])
    sp = bench._load_sp500_panel("2025-01-01", "2025-01-03")
    assert "vwap" in sp and sp["_meta"]["constituent_source"] == "wikipedia"
    monkeypatch.setattr(bench, "_fetch_sp500_constituents", lambda: [])
    fallback = bench._load_sp500_panel("2025-01-01", "2025-01-03")
    assert fallback["_meta"]["constituent_source"] == "hand-picked fallback"
    btc = bench._load_btc_panel("2025-01-01", "2025-01-03")
    assert "vwap" in btc


def test_wikipedia_constituent_success_and_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    response = SimpleNamespace(text="html", raise_for_status=lambda: None)
    monkeypatch.setitem(sys.modules, "requests", SimpleNamespace(get=lambda *args, **kwargs: response))
    monkeypatch.setattr(
        pd,
        "read_html",
        lambda value: [pd.DataFrame({"Other": [1]}), pd.DataFrame({"Symbol": ["BRK.B", "nan", "AAPL"]})],
    )
    assert bench._fetch_sp500_constituents() == ["BRK-B", "AAPL"]
    monkeypatch.setattr(pd, "read_html", lambda value: (_ for _ in ()).throw(ValueError("bad html")))
    assert bench._fetch_sp500_constituents() == []


def _html_context() -> dict:
    return {
        "csp": bench._CSP,
        "css": "body{}",
        "generated_at": "now",
        "universe": "<script>",
        "period": "2020-2021",
        "n_alphas_tested": 1,
        "n_skipped": 1,
        "top": [
            {
                "id": "a<1",
                "zoo": "alpha101",
                "theme": ["momentum"],
                "formula_latex": "x<y",
                "ic_mean": 0.1,
                "ic_std": 0.2,
                "ir": 0.5,
                "ic_positive_ratio": 0.6,
                "ic_count": 3,
            }
        ],
        "failures": [{"alpha_id": "bad", "reason": "x<y"}],
    }


def test_html_renderers_escape_and_include_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    manual = bench._render_html_manual(_html_context())
    assert "&lt;script&gt;" in manual and "Skipped / Failed" in manual
    rendered = bench._render_html(_html_context())
    assert "&lt;script&gt;" in rendered and bench._CSP in rendered
    context = _html_context()
    context["failures"] = []
    assert "Skipped / Failed" not in bench._render_html_manual(context)
    assert bench._esc('"<') == "&quot;&lt;"


class _Registry:
    def __init__(self, ids=None):
        self.ids = ["a", "bad", "unexpected"] if ids is None else ids

    def list(self, **kwargs):
        return self.ids

    def get(self, alpha_id):
        if alpha_id == "unknown":
            raise KeyError(alpha_id)
        return SimpleNamespace(zoo="test", meta={})

    def compute(self, alpha_id, panel):
        return panel["close"]


def test_run_alpha_bench_validation_and_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert bench.run_alpha_bench(period="2020-2021")["status"] == "error"
    assert bench.run_alpha_bench(universe="sp500")["status"] == "error"
    assert bench.run_alpha_bench(universe="sp500", period="bad")["status"] == "error"
    assert bench.run_alpha_bench(universe="sp500", period="2020-2021", top=0)["status"] == "error"
    assert bench.run_alpha_bench(universe="sp500", period="2020-2021", top="bad")["status"] == "error"

    from src.factors import registry as registry_module

    monkeypatch.setattr(registry_module, "get_default_registry", lambda: _Registry([]))
    empty = bench.run_alpha_bench(universe="sp500", period="2020-2021")
    assert "no alphas" in empty["error"]
    monkeypatch.setattr(registry_module, "get_default_registry", lambda: _Registry())
    monkeypatch.setattr(bench, "_load_universe_panel", lambda *args, **kwargs: _panel())
    monkeypatch.setattr(bench, "_compute_forward_returns", lambda panel: panel["close"])

    def one(registry, alpha_id, panel, returns):
        if alpha_id == "bad":
            raise RuntimeError("skip")
        if alpha_id == "unexpected":
            raise TypeError("bug")
        return {
            "id": alpha_id,
            "zoo": "test",
            "theme": [],
            "formula_latex": "",
            "ic_mean": 0.1,
            "ic_std": 0.1,
            "ir": 1.0,
            "ic_positive_ratio": 1.0,
            "ic_count": 2,
        }

    monkeypatch.setattr(bench, "_bench_one_alpha", one)
    result = bench.run_alpha_bench(
        universe="sp500", period="2020-2021", output_dir=tmp_path, top=1
    )
    assert result["status"] == "ok"
    assert result["n_alphas_tested"] == 1 and result["n_skipped"] == 2
    assert Path(result["report_path"]).is_file()
    assert json.loads(bench.AlphaBenchTool().execute(universe="sp500", period="bad"))["status"] == "error"


def test_run_alpha_bench_error_envelopes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.factors import registry as registry_module

    monkeypatch.setattr(
        registry_module,
        "get_default_registry",
        lambda: (_ for _ in ()).throw(RuntimeError("registry down")),
    )
    result = bench.run_alpha_bench(universe="sp500", period="2020-2021")
    assert "registry init failed" in result["error"]

    monkeypatch.setattr(registry_module, "get_default_registry", lambda: _Registry())
    monkeypatch.setattr(
        bench,
        "_load_universe_panel",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("network")),
    )
    result = bench.run_alpha_bench(universe="sp500", period="2020-2021")
    assert result["n_alphas_tested"] == 0
    monkeypatch.setattr(bench, "_load_universe_panel", lambda *args, **kwargs: {})
    result = bench.run_alpha_bench(universe="sp500", period="2020-2021")
    assert "forward returns failed" in result["error"]
