"""Branch-focused contracts for the backtest runner orchestration layer."""

from __future__ import annotations

import ast
from pathlib import Path

import pandas as pd
import pytest
from pydantic import ValidationError

from backtest import runner
from backtest.loaders.base import NoAvailableSourceError


pytestmark = pytest.mark.unit


def _bars(currency: str = "USD") -> pd.DataFrame:
    frame = pd.DataFrame(
        {
            "open": [10.0, 11.0],
            "high": [11.0, 12.0],
            "low": [9.0, 10.0],
            "close": [10.5, 11.5],
            "volume": [100.0, 200.0],
        },
        index=pd.date_range("2025-01-01", periods=2, tz="UTC"),
    )
    frame.attrs["vibe_metadata"] = {"currency": currency, "provider": "test"}
    return frame


def _valid_config(**overrides):
    payload = {
        "codes": ["AAPL"],
        "start_date": "2025-01-01",
        "end_date": "2025-01-02",
        "source": "global",
    }
    payload.update(overrides)
    return payload


@pytest.mark.parametrize(
    "overrides,match",
    [
        ({"codes": []}, "non-empty"),
        ({"codes": [" "]}, "empty strings"),
        ({"start_date": "not-a-date"}, "invalid date"),
        ({"start_date": "2025-02-01", "end_date": "2025-01-01"}, "must be <="),
        ({"interval": "2D"}, "unsupported interval"),
        ({"engine": "quantum"}, "unsupported engine"),
        ({"source": "unknown"}, "unsupported source"),
        ({"fundamental_fields": {" ": ["x"]}}, "table names"),
        ({"fundamental_fields": {"daily": [" "]}}, "field names"),
        ({"event_feeds": ["bad"]}, "valid dictionary"),
        ({"event_feeds": [{"name": "x"}]}, "missing required field"),
    ],
)
def test_config_schema_rejects_each_invalid_contract(overrides, match) -> None:
    with pytest.raises((ValidationError, ValueError), match=match):
        runner.BacktestConfigSchema(**_valid_config(**overrides))


def test_config_schema_accepts_optional_enrichment_contracts() -> None:
    config = runner.BacktestConfigSchema(
        **_valid_config(
            interval="1H",
            engine="options",
            fundamental_fields={"daily_basic": ["pe", "pb"]},
            event_feeds=[
                {"name": "news", "route_template": "/news/{code}", "event_type": "news"}
            ],
        )
    )
    assert config.interval == "1H"
    assert config.fundamental_fields == {"daily_basic": ["pe", "pb"]}


@pytest.mark.parametrize(
    "source,expected",
    [
        ("okx", ["BTC-USDT", "ETH-USDT"]),
        ("ccxt", ["BTC-USDT", "ETH-USDT"]),
        ("global", ["btc/usdt", "eth-usdt"]),
    ],
)
def test_code_normalization(source, expected) -> None:
    assert runner._normalize_codes(["btc/usdt", "eth-usdt"], source) == expected


def test_literal_and_reference_classifiers_cover_recursive_shapes() -> None:
    assert runner._is_literal_node(ast.parse("{'a': (1, [2]), **{}}", mode="eval").body)
    assert not runner._is_literal_node(ast.parse("call()", mode="eval").body)
    assert runner._is_safe_constant_assignment(ast.parse("X: int").body[0])
    assert runner._is_safe_constant_assignment(ast.parse("X = {'a': 1}").body[0])
    assert not runner._is_safe_constant_assignment(ast.parse("X = call()").body[0])
    for expression in ("Name", "pkg.Type", "list[str]", "A | None", "(A, B)"):
        assert runner._is_safe_reference(ast.parse(expression, mode="eval").body)
    assert runner._is_safe_reference(None)
    assert not runner._is_safe_reference(ast.parse("factory()", mode="eval").body)


@pytest.mark.parametrize(
    "source,match",
    [
        ("@decorator\ndef f():\n    pass\n", "Decorators"),
        ("def f(value=make()):\n    pass\n", "Non-literal default"),
        ("def f() -> factory():\n    pass\n", "Unsafe annotation"),
        ("@decorator\nclass C:\n    pass\n", "Decorators"),
        ("class C(factory()):\n    pass\n", "Unsafe base"),
        ("class C(metaclass=M):\n    pass\n", "Class keywords"),
        ("class C:\n    value = make()\n", "Executable class-level"),
    ],
)
def test_function_and_class_definition_validator_rejections(source, match) -> None:
    node = ast.parse(source).body[0]
    with pytest.raises(ValueError, match=match):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            runner._validate_function_def(node)
        else:
            runner._validate_class_body(node)


@pytest.mark.parametrize(
    "source,match",
    [
        ("def broken(:\n", "Invalid signal_engine.py syntax"),
        ("from signal_engine import SignalEngine\n", "Circular import"),
        ("raise RuntimeError('boom')\n", "Executable top-level"),
    ],
)
def test_signal_source_validator_rejections(tmp_path: Path, source, match) -> None:
    path = tmp_path / "signal_engine.py"
    path.write_text(source, encoding="utf-8")
    with pytest.raises(ValueError, match=match):
        runner._validate_signal_engine_source(path)


def test_signal_source_validator_accepts_safe_async_and_class_shapes(tmp_path: Path) -> None:
    path = tmp_path / "signal_engine.py"
    path.write_text(
        '"""safe"""\n'
        "import math\n"
        "LIMIT: int = 3\n"
        "async def helper(value: int = 1) -> int:\n    return value\n"
        "class SignalEngine:\n"
        "    label = 'safe'\n"
        "    pass\n"
        "    def generate(self, data: dict | None = None):\n        return {}\n",
        encoding="utf-8",
    )
    runner._validate_signal_engine_source(path)
    module = runner._load_module_from_file(path, "node11_safe_signal")
    runner._validate_signal_engine_class(module.SignalEngine)


def test_signal_class_interface_validation() -> None:
    class RequiresArgument:
        def __init__(self, required):
            self.required = required

        def generate(self):
            return {}

    class MissingGenerate:
        pass

    with pytest.raises(ValueError, match="required arguments"):
        runner._validate_signal_engine_class(RequiresArgument)
    with pytest.raises(ValueError, match="callable 'generate'"):
        runner._validate_signal_engine_class(MissingGenerate)


def test_loader_selection_fallback_and_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel = object()
    monkeypatch.setattr(runner, "get_loader_cls_with_fallback", lambda source: sentinel)
    assert runner._get_loader("global") is sentinel

    def unavailable(source):
        raise NoAvailableSourceError(source, [])

    monkeypatch.setattr(runner, "get_loader_cls_with_fallback", unavailable)
    monkeypatch.setattr(runner, "LOADER_REGISTRY", {"tushare": sentinel})
    assert runner._get_loader("missing") is sentinel
    monkeypatch.setattr(runner, "LOADER_REGISTRY", {})
    with pytest.raises(NoAvailableSourceError):
        runner._get_loader("missing")


def test_grouping_and_primary_source_contracts(monkeypatch: pytest.MonkeyPatch) -> None:
    markets = {"A": "us_equity", "B": "crypto", "C": "us_equity"}
    monkeypatch.setattr(runner, "_detect_market", markets.__getitem__)
    monkeypatch.setattr(
        runner,
        "_detect_source",
        lambda code: "global" if code in {"A", "C"} else "okx",
    )
    assert runner._group_codes_by_market(["A", "B", "C"]) == {
        "us_equity": ["A", "C"],
        "crypto": ["B"],
    }
    assert runner._group_codes_by_source(["A", "B", "C"])["global"] == ["A", "C"]
    assert runner._detect_primary_source(["A"], "global") == "global"
    assert runner._detect_primary_source(["A"], "auto") == "global"
    assert runner._detect_primary_source(["A", "B", "C"], "auto") == "global"


class _FakeLoader:
    name = "primary"

    def __init__(self, result=None, available=True):
        self.result = {} if result is None else result
        self.available = available
        self.calls = []

    def is_available(self):
        return self.available

    def fetch(self, codes, start, end, **kwargs):
        self.calls.append((codes, start, end, kwargs))
        return self.result


def test_fetch_auto_primary_and_runtime_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    fallback = _FakeLoader({"BTC-USDT": _bars()})
    monkeypatch.setattr(runner, "_group_codes_by_market", lambda codes: {"crypto": codes})
    monkeypatch.setattr(runner, "resolve_loader", lambda market: _FakeLoader())
    monkeypatch.setattr(runner, "FALLBACK_CHAINS", {"crypto": ["primary", "down", "okx"]})
    monkeypatch.setattr(
        runner,
        "LOADER_REGISTRY",
        {
            "down": lambda: _FakeLoader(available=False),
            "okx": lambda: fallback,
        },
    )
    result = runner._fetch_auto(
        ["btc/usdt"],
        {"start_date": "2025-01-01", "end_date": "2025-01-02", "extra_fields": ["x"]},
        "1H",
    )
    assert list(result) == ["BTC-USDT"]
    assert fallback.calls[0][0] == ["BTC-USDT"]


def test_fetch_auto_resolve_failure_uses_legacy_loader(monkeypatch: pytest.MonkeyPatch) -> None:
    loader = _FakeLoader({"AAPL": _bars()})
    monkeypatch.setattr(runner, "_group_codes_by_market", lambda codes: {"us_equity": codes})
    monkeypatch.setattr(
        runner,
        "resolve_loader",
        lambda market: (_ for _ in ()).throw(NoAvailableSourceError(market, [])),
    )
    monkeypatch.setattr(runner, "_get_loader", lambda source: lambda: loader)
    assert "AAPL" in runner._fetch_auto(["AAPL"], _valid_config())


def test_bundle_validation_base_currency_and_auto_loader(monkeypatch: pytest.MonkeyPatch) -> None:
    from backtest.engines import base

    monkeypatch.setattr(base, "_maybe_enrich_fundamentals", lambda rows, config: rows)
    monkeypatch.setattr(base, "_maybe_enrich_events", lambda rows, config: rows)
    empty = _bars().iloc[0:0]
    with pytest.raises(ValueError, match="loader is required"):
        runner.build_data_bundle(_valid_config(), None)
    with pytest.raises(ValueError, match="No data fetched"):
        runner.build_data_bundle(_valid_config(), None, data_map={"AAPL": empty})
    with pytest.raises(ValueError, match="requires base_currency"):
        runner.build_data_bundle(
            _valid_config(),
            None,
            data_map={"AAPL": _bars("USD"), "600519.SH": _bars("CNY")},
        )
    bundle = runner.build_data_bundle(
        {**_valid_config(), "base_currency": "usd"},
        None,
        data_map={"AAPL": _bars()},
    )
    assert bundle.base_currency == "USD"
    assert runner._AutoLoader({"AAPL": _bars()}).fetch(
        ["AAPL"], "", "", fields=[], interval="1D"
    ).keys() == {"AAPL"}


@pytest.mark.parametrize(
    "source,codes,expected",
    [
        ("auto", ["AAPL", "BTC-USDT"], "CompositeEngine"),
        ("tushare", ["IF2506.CFFEX"], "ChinaFuturesEngine"),
        ("global", ["ESZ4"], "GlobalFuturesEngine"),
        ("local", ["EUR/USD"], "ForexEngine"),
        ("okx", ["BTC-USDT"], "CryptoEngine"),
        ("astock", ["600519.SH"], "ChinaAEngine"),
        ("tushare", ["AAPL.US"], "GlobalEquityEngine"),
        ("global", ["AAPL"], "GlobalEquityEngine"),
        ("other", [], "CryptoEngine"),
    ],
)
def test_market_engine_routes(source, codes, expected) -> None:
    engine = runner._create_market_engine(source, {}, codes)
    assert type(engine).__name__ == expected


@pytest.mark.parametrize(
    "stage,config,signal_source,match",
    [
        ("missing_config", None, None, "config.json not found"),
        ("invalid_config", {"codes": []}, None, "Invalid config"),
        ("missing_signal", _valid_config(), None, "signal_engine.py not found"),
        ("invalid_signal", _valid_config(), "raise RuntimeError()\n", "source error"),
        ("missing_class", _valid_config(), "VALUE = 1\n", "class not found"),
        (
            "invalid_class",
            _valid_config(),
            "class SignalEngine:\n    def __init__(self, value): self.value=value\n    def generate(self): return {}\n",
            "interface error",
        ),
    ],
)
def test_main_reports_preflight_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    stage,
    config,
    signal_source,
    match,
) -> None:
    run_dir = tmp_path / stage
    run_dir.mkdir()
    monkeypatch.setattr("src.tools.path_utils.safe_run_dir", lambda value: Path(value))
    if config is not None:
        (run_dir / "config.json").write_text(__import__("json").dumps(config), encoding="utf-8")
    if signal_source is not None:
        (run_dir / "code").mkdir()
        (run_dir / "code" / "signal_engine.py").write_text(signal_source, encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        runner.main(run_dir)
    assert exc.value.code == 1
    assert match in capsys.readouterr().out


def test_main_rejects_disallowed_run_root(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setattr(
        "src.tools.path_utils.safe_run_dir",
        lambda value: (_ for _ in ()).throw(ValueError("outside allowed roots")),
    )
    with pytest.raises(SystemExit):
        runner.main(Path("/outside"))
    assert "outside allowed roots" in capsys.readouterr().out
