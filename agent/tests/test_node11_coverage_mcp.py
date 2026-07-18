"""Offline MCP adapter contracts across optional parameters and error envelopes."""

from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import pytest

import mcp_server as server


pytestmark = pytest.mark.unit


class _Registry:
    def __init__(self, present=True):
        self.present = present
        self.calls = []

    def get(self, name):
        return object() if self.present else None

    def execute(self, name, params):
        self.calls.append((name, params))
        return json.dumps({"name": name, "params": params}, ensure_ascii=False)


def test_singletons_json_and_normalization(monkeypatch: pytest.MonkeyPatch) -> None:
    assert json.loads(server._json_ok(value=1)) == {"status": "ok", "value": 1}
    assert json.loads(server._json_error("bad", error_type="validation"))["error_type"] == "validation"
    assert server._clean_list([" a ", "", None, " b"]) == ["a", "b"]
    assert server._blank_to_none(None) is None
    assert server._blank_to_none("  ") is None
    assert server._blank_to_none(" x ") == "x"
    monkeypatch.setenv("VIBE_TRADING_ENABLE_SHELL_TOOLS", " YES ")
    assert server._env_shell_tools_enabled()
    monkeypatch.setenv("VIBE_TRADING_ENABLE_SHELL_TOOLS", "off")
    assert not server._env_shell_tools_enabled()

    loader = SimpleNamespace(
        skills=[SimpleNamespace(name="one", description="desc")],
        get_content=lambda name: "Error: missing" if name == "bad" else "content",
    )
    monkeypatch.setattr(server, "_skills_loader", loader)
    assert json.loads(server.list_skills())[0]["name"] == "one"
    assert json.loads(server.load_skill("bad"))["status"] == "error"
    assert json.loads(server.load_skill("one"))["content"] == "content"


def test_audit_rows_and_risk_tier_contracts() -> None:
    assert server._audit_rows_from_payload(None) == []
    row = server._audit_rows_from_payload(
        [{"criterion_id": " c1 ", "result": " satisfied ", "evidence_ids": [" e1 "], "notes": "n"}]
    )[0]
    assert row.criterion_id == "c1" and row.evidence_ids == ["e1"]
    for payload in ({"criterion_id": "", "result": "ok"}, {"criterion_id": "c", "result": ""}):
        with pytest.raises(ValueError, match="require"):
            server._audit_rows_from_payload([payload])
    assert server._risk_tier_from_text("research_general").value == "research_general"
    with pytest.raises(ValueError, match="not supported"):
        server._risk_tier_from_text("live_trading_or_execution")


def test_goal_read_and_mutation_error_envelopes(monkeypatch: pytest.MonkeyPatch) -> None:
    store = SimpleNamespace(get_current_snapshot=lambda session: None)
    monkeypatch.setattr(server, "_get_goal_store", lambda: store)
    assert json.loads(server.get_research_goal("session"))["error_type"] == "not_found"
    store.get_current_snapshot = lambda session: (_ for _ in ()).throw(ValueError("bad session"))
    assert json.loads(server.get_research_goal("session"))["error_type"] == "validation"

    store.append_evidence = lambda **kwargs: (_ for _ in ()).throw(ValueError("invalid evidence"))
    assert json.loads(
        server.add_goal_evidence("s", "g", "g", "text", symbol_universe=[" AAPL "], assumptions={})
    )["error_type"] == "validation"
    store.update_status = lambda **kwargs: (_ for _ in ()).throw(ValueError("invalid status"))
    assert json.loads(server.update_research_goal_status("s", "g", "g", "bad"))["error_type"] == "validation"


def test_key_gated_registry_and_direct_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = _Registry(present=True)
    monkeypatch.setattr(server, "_get_registry", lambda: registry)
    assert json.loads(server._execute_key_gated("get_macro_series", {"x": 1}))["name"] == "get_macro_series"
    registry.present = False

    class Tool:
        def execute(self, **kwargs):
            return json.dumps({"direct": kwargs})

    monkeypatch.setattr(server, "_key_gated_tool_classes", lambda: {"get_macro_series": Tool})
    assert json.loads(server._execute_key_gated("get_macro_series", {"x": 1}))["direct"] == {"x": 1}
    assert json.loads(server._execute_key_gated("unknown", {"x": 1}))["name"] == "unknown"


def test_optional_registry_wrapper_parameter_shapes(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = _Registry()
    monkeypatch.setattr(server, "_get_registry", lambda: registry)
    server.get_dragon_tiger("2025-01-01")
    server.get_dragon_tiger("2025-01-01", "AAPL")
    server.get_lockup_expiry()
    server.get_lockup_expiry("600519.SH")
    server.get_sector_info()
    server.get_sector_info("600519.SH", mode="members", limit=2)
    server.get_stock_news(scope="global")
    server.get_stock_news("AAPL", scope="stock")
    server.get_sec_filings("AAPL")
    server.get_sec_filings("AAPL", form="10-K", metric="Revenue")
    server.get_options_chain("AAPL")
    server.get_options_chain("AAPL", expiration=123)
    server.get_stock_profile("AAPL", sections=[])
    server.get_stock_profile("AAPL", sections=[" key_stats "])
    server.run_shadow_backtest("s")
    server.run_shadow_backtest("s", "2025-01-01", "2025-02-01", ["us"], "journal.csv")
    server.render_shadow_report("s")
    server.render_shadow_report("s", window_start="a", window_end="b", journal_path="j")
    server.scan_shadow_signals("s")
    server.scan_shadow_signals("s", date="2025-01-01")
    params = [payload for _, payload in registry.calls]
    assert any("form" in payload and "metric" in payload for payload in params)
    assert any("sections" in payload for payload in params)
    assert any("markets" in payload and "journal_path" in payload for payload in params)


def _run(status="completed", tasks=None):
    return SimpleNamespace(
        id="run-1",
        status=SimpleNamespace(value=status),
        preset_name="quality",
        created_at="now",
        completed_at="later",
        tasks=tasks or [],
        final_report="report",
        total_input_tokens=1,
        total_output_tokens=2,
        user_vars={"symbol": "AAPL"},
    )


class _Store:
    def __init__(self, loaded=None, *, error=False):
        self.loaded = loaded
        self.error = error

    def load_run(self, run_id):
        if self.error:
            raise ValueError("invalid id")
        return self.loaded

    def reconcile_run(self, run, write=True):
        return run

    def is_run_stale(self, run):
        return False

    def list_runs(self, limit=20):
        return [self.loaded] if self.loaded else []

    def reap_stale_running_runs(self):
        return ["run-1"]


def test_swarm_payload_status_result_list_and_reap(monkeypatch: pytest.MonkeyPatch) -> None:
    import src.swarm.serialization as serialization

    monkeypatch.setattr(serialization, "serialize_task", lambda task: {"status": task.status.value})
    monkeypatch.setattr(serialization, "run_level_error", lambda run: None)
    task = SimpleNamespace(status=SimpleNamespace(value="completed"))
    run = _run(tasks=[task])
    store = _Store(run)
    payload = server._run_to_dict(run, timed_out=True, is_stale=True)
    assert payload["timed_out"] and payload["is_stale"]
    assert server._build_run_payload(_Store(None), "lost", None, timed_out=False)["status"] == "error"
    assert server._build_run_payload(store, "run-1", "override", timed_out=False)["preset"] == "override"

    monkeypatch.setattr(server, "_get_swarm_store", lambda: _Store(None, error=True))
    assert json.loads(server.get_swarm_status("bad"))["status"] == "error"
    assert json.loads(server.get_run_result("bad"))["status"] == "error"
    monkeypatch.setattr(server, "_get_swarm_store", lambda: _Store(None))
    assert "not found" in json.loads(server.get_swarm_status("missing"))["error"]
    assert "not found" in json.loads(server.get_run_result("missing"))["error"]
    monkeypatch.setattr(server, "_get_swarm_store", lambda: store)
    assert json.loads(server.get_swarm_status("run-1"))["status"] == "completed"
    assert json.loads(server.get_run_result("run-1"))["ready"]
    listed = json.loads(server.list_runs(limit=1))
    assert listed[0]["task_counts"] == {"total": 1, "completed": 1}
    assert json.loads(server.reap_stale_runs()) == {"reaped": ["run-1"]}


def test_industry_chain_modes(monkeypatch: pytest.MonkeyPatch) -> None:
    import src.industry_chain.store as store_module

    ticker = SimpleNamespace(code="AAPL", name="Apple", score=1, tier="A")
    segment = SimpleNamespace(
        name="chips", chokepoint_total=10, barrier_type="tech", tickers=[ticker]
    )
    chain = SimpleNamespace(
        chain_id="abcdef123456",
        name="AI Chain",
        overview=SimpleNamespace(lifecycle_stage="growth", prosperity_score=90),
        segments=[segment],
        summary=lambda: {"name": "AI Chain"},
        to_dict=lambda: {"chain_id": "abcdef123456"},
    )
    store = SimpleNamespace(list_chains=lambda: [chain], get_chain=lambda chain_id: chain if chain_id == chain.chain_id else None)
    monkeypatch.setattr(store_module, "IndustryChainStore", lambda: store)
    assert json.loads(server.get_industry_chain("list"))["chains"]
    assert "Provide" in json.loads(server.get_industry_chain("detail"))["error"]
    assert json.loads(server.get_industry_chain("detail", chain_name="AI"))["chain_id"] == chain.chain_id
    assert "not found" in json.loads(server.get_industry_chain("detail", chain_id="missing"))["error"]
    scored = json.loads(server.get_industry_chain("score", chain_id=chain.chain_id))
    assert scored["segments"]["chips"]["tickers"][0]["code"] == "AAPL"


@pytest.mark.parametrize("transport", ["stdio", "sse"])
def test_main_transport_shell_policy(transport, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    monkeypatch.setattr(sys, "argv", ["mcp_server.py", "--transport", transport, "--port", "9999"])
    monkeypatch.setattr(server, "_get_registry", lambda: object())
    monkeypatch.setattr(server, "_env_shell_tools_enabled", lambda: False)
    monkeypatch.setattr(server.mcp, "run", lambda **kwargs: calls.append(kwargs))
    server.main()
    assert calls == ([{"transport": "sse", "port": 9999}] if transport == "sse" else [{}])
    assert server._include_shell_tools is (transport == "stdio")
