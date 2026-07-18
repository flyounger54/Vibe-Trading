"""Runtime event, cancellation, result, and continuation contracts for legacy CLI."""

from __future__ import annotations

import signal
from pathlib import Path
from types import SimpleNamespace

import pytest
from rich.console import Console

from cli import _legacy as legacy


pytestmark = pytest.mark.unit


def _console() -> Console:
    return Console(record=True, width=120, color_system=None)


class _FakeMemory:
    def __init__(self):
        self.run_dir = None


class _FakeAgent:
    events = []
    result = {"status": "success", "content": "done"}

    def __init__(self, **kwargs):
        self.callback = kwargs["event_callback"]
        self.memory = _FakeMemory()
        self.cancelled = False

    def run(self, **kwargs):
        for event_type, data in self.events:
            self.callback(event_type, data)
        return dict(self.result)

    def cancel(self):
        self.cancelled = True


def _install_agent_fakes(monkeypatch: pytest.MonkeyPatch, *, warn: bool = False) -> None:
    import src.agent.loop as loop_module
    import src.config.loader as loader_module
    import src.memory.persistent as memory_module
    import src.providers.chat as chat_module
    import src.tools as tools_module

    monkeypatch.setattr(loop_module, "AgentLoop", _FakeAgent)
    monkeypatch.setattr(memory_module, "PersistentMemory", _FakeMemory)
    monkeypatch.setattr(chat_module, "ChatLLM", lambda: object())
    monkeypatch.setattr(loader_module, "load_agent_config", lambda: {})

    def build_registry(**kwargs):
        if warn:
            kwargs["warn_callback"]("connector unavailable")
        return object()

    monkeypatch.setattr(tools_module, "build_registry", build_registry)
    monkeypatch.setattr(
        legacy,
        "_run_with_graceful_cancel",
        lambda agent, prompt, history, **kwargs: agent.run(),
    )


def test_run_agent_no_rich_event_matrix(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install_agent_fakes(monkeypatch, warn=True)
    proposal = {"proposal_id": "mp_" + "a" * 32}
    monkeypatch.setattr(legacy, "_mandate_proposal_from_tool_result", lambda data: proposal)
    monkeypatch.setattr(legacy.time, "monotonic", lambda: 10.0)
    received = []
    _FakeAgent.events = [
        ("mandate.proposal", proposal),
        ("tool_call", {"tool": "bash", "arguments": {"command": "echo ok"}}),
        ("tool_heartbeat", {"tool": "bash"}),
        ("tool_progress", {"tool": "bash", "stage": "fetch", "current": 1, "total": 2, "message": "half"}),
        ("tool_progress", {"tool": "bash", "stage": "hidden"}),
        ("tool_result", {"tool": "bash", "status": "ok", "elapsed_ms": 1200, "preview": "OK"}),
        ("thinking_done", {}),
        ("compact", {"tokens_before": 100}),
        ("text_delta", {"delta": "answer"}),
    ]
    result = legacy._run_agent(
        "prompt",
        no_rich=True,
        run_dir_override="/tmp/run",
        session_id="session",
        proposal_sink=received.append,
    )
    assert result["status"] == "success"
    assert received == [proposal, proposal]
    out = capsys.readouterr().out
    assert "WARNING" in out and "1/2" in out and "context compressed" in out and "answer" in out


def test_run_agent_dashboard_stream_off_and_proposal_sink_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_agent_fakes(monkeypatch)
    proposal = {"proposal_id": "mp_" + "b" * 32}
    monkeypatch.setattr(legacy, "_mandate_proposal_from_tool_result", lambda data: proposal)
    events = []
    dashboard = SimpleNamespace(handle_event=lambda event, data: events.append((event, data)))
    _FakeAgent.events = [("tool_result", {"tool": "proposal"}), ("text_delta", {"delta": "x"})]
    legacy._run_agent(
        "prompt",
        dashboard=dashboard,
        proposal_sink=lambda payload: (_ for _ in ()).throw(RuntimeError("consumer")),
    )
    assert [event for event, _ in events] == ["tool_result", "text_delta"]
    events.clear()
    legacy._run_agent("prompt", dashboard=dashboard, stream_output=False)
    assert events == []


def test_run_agent_rich_event_matrix(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_agent_fakes(monkeypatch, warn=True)
    console = _console()
    monkeypatch.setattr(legacy, "console", console)
    _FakeAgent.events = [
        ("text_delta", {"delta": "answer"}),
        ("thinking_done", {}),
        ("tool_call", {"tool": "read_file", "arguments": {"path": "a.py"}}),
        ("tool_result", {"tool": "read_file", "status": "error", "elapsed_ms": 10, "preview": "bad"}),
        ("compact", {"tokens_before": 20}),
    ]
    legacy._run_agent("prompt")
    text = console.export_text()
    assert "WARNING" in text and "answer" in text and "context compressed" in text


class _CancelAgent:
    def __init__(self, action=None):
        self.action = action
        self.cancelled = 0

    def run(self, **kwargs):
        if self.action:
            self.action()
        return {"status": "success"}

    def cancel(self):
        self.cancelled += 1


def test_graceful_cancel_install_first_and_second_interrupt(
    monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    installed = []
    restored = []
    original = object()
    monkeypatch.setattr(signal, "getsignal", lambda sig: original)

    def set_signal(sig, handler):
        if callable(handler):
            installed.append(handler)
        else:
            restored.append(handler)

    monkeypatch.setattr(signal, "signal", set_signal)
    agent = _CancelAgent(lambda: installed[-1](signal.SIGINT, None))
    assert legacy._run_with_graceful_cancel(agent, "p", [], no_rich=True)["status"] == "success"
    assert agent.cancelled == 1 and original in restored
    assert "Cancelling" in capsys.readouterr().out

    times = iter([1.0, 1.5])
    monkeypatch.setattr(legacy.time, "time", lambda: next(times))

    def twice():
        installed[-1](signal.SIGINT, None)
        installed[-1](signal.SIGINT, None)

    with pytest.raises(KeyboardInterrupt):
        legacy._run_with_graceful_cancel(_CancelAgent(twice), "p", None, no_rich=False)


def test_graceful_cancel_signal_api_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    agent = _CancelAgent()
    monkeypatch.setattr(signal, "getsignal", lambda sig: (_ for _ in ()).throw(ValueError("thread")))
    assert legacy._run_with_graceful_cancel(agent, "p", None, no_rich=True)["status"] == "success"
    monkeypatch.setattr(signal, "getsignal", lambda sig: object())
    monkeypatch.setattr(signal, "signal", lambda *args: (_ for _ in ()).throw(OSError("thread")))
    assert legacy._run_with_graceful_cancel(agent, "p", None, no_rich=True)["status"] == "success"


@pytest.mark.parametrize(
    "metrics,expected_rows",
    [
        ({}, None),
        ({"benchmark_ticker": "SPY"}, 1),
        ({"benchmark_ticker": "SPY", "benchmark_return": "bad"}, 1),
        ({"benchmark_ticker": "SPY", "benchmark_return": "0.1", "total_return": "0.2"}, 3),
        ({"benchmark_ticker": "SPY", "_benchmark_return_raw": 0.2, "total_return": "0.1", "information_ratio": "1.2", "excess_return": "0.1"}, 5),
        ({"benchmark_ticker": "SPY", "benchmark_return": "0", "excess_return": "0.0000"}, 2),
    ],
)
def test_benchmark_table_variants(metrics, expected_rows) -> None:
    table = legacy._build_benchmark_table(metrics)
    if expected_rows is None:
        assert table is None
    else:
        assert table is not None and len(table.rows) == expected_rows


def test_print_result_plain_and_rich(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    metrics = {
        "total_return": "0.2",
        "sharpe": "1.5",
        "max_drawdown": "-0.1",
        "trade_count": "3",
        "benchmark_ticker": "SPY",
        "benchmark_return": "0.1",
    }
    monkeypatch.setattr(legacy, "_read_metrics", lambda path: metrics)
    result = {
        "status": "success",
        "run_id": "r1",
        "run_dir": "/tmp/r1",
        "reason": "done",
        "review": {"overall_score": 90, "passed": True},
        "content": "answer",
    }
    legacy._print_result(result, 61, no_rich=True)
    out = capsys.readouterr().out
    assert "Review: PASS" in out and "Metrics:" in out and "answer" in out

    console = _console()
    monkeypatch.setattr(legacy, "console", console)
    monkeypatch.setattr(legacy, "_terminal_width", lambda: 80)
    legacy._print_result(result, 1)
    monkeypatch.setattr(legacy, "_terminal_width", lambda: 120)
    failed = {**result, "review": {"overall_score": 20, "passed": False}}
    legacy._print_result(failed, 1)
    text = console.export_text()
    assert "Benchmark Comparison" in text and "FAIL" in text


def test_cmd_run_preflight_json_plain_and_interrupt(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setattr(
        "src.preflight.run_preflight",
        lambda console: [SimpleNamespace(critical=True, status="failed")],
    )
    assert legacy.cmd_run("prompt", 2) == legacy.EXIT_RUN_FAILED
    monkeypatch.setattr(
        "src.preflight.run_preflight",
        lambda console: [SimpleNamespace(critical=False, status="failed")],
    )
    monkeypatch.setattr(legacy, "_run_agent", lambda *args, **kwargs: {"status": "success", "run_id": "r1"})
    monkeypatch.setattr(legacy, "_print_result", lambda *args, **kwargs: None)
    assert legacy.cmd_run("p" * 130, 2, no_rich=True) == 0
    assert "..." in capsys.readouterr().out
    assert legacy.cmd_run("prompt", 2, json_mode=True) == 0
    assert '"status": "success"' in capsys.readouterr().out
    monkeypatch.setattr(legacy, "_run_agent", lambda *args, **kwargs: (_ for _ in ()).throw(KeyboardInterrupt()))
    assert legacy.cmd_run("prompt", 2, json_mode=True) == legacy.EXIT_RUN_FAILED
    assert legacy.cmd_run("prompt", 2, no_rich=True) == legacy.EXIT_RUN_FAILED


def test_history_and_continue_plain_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    runs = tmp_path / "runs"
    sessions = tmp_path / "sessions"
    monkeypatch.setattr(legacy, "RUNS_DIR", runs)
    monkeypatch.setattr(legacy, "SESSIONS_DIR", sessions)
    assert legacy.cmd_continue("missing", "p", 1, no_rich=True) == legacy.EXIT_USAGE_ERROR
    assert "not found" in capsys.readouterr().out

    session = sessions / "s1"
    session.mkdir(parents=True)
    monkeypatch.setattr(legacy, "_build_history_from_trace", lambda path: [{"role": "user", "content": "old"}])
    monkeypatch.setattr(legacy, "_run_agent", lambda *args, **kwargs: {"status": "success"})
    monkeypatch.setattr(legacy, "_print_result", lambda *args, **kwargs: None)
    assert legacy.cmd_continue("s1", "next", 2, no_rich=True) == 0
    assert (runs / "s1").is_dir()
    assert legacy.cmd_continue("s1", "next", 2, json_mode=True) == 0

    monkeypatch.setattr(legacy, "_run_agent", lambda *args, **kwargs: (_ for _ in ()).throw(KeyboardInterrupt()))
    assert legacy.cmd_continue("s1", "next", 2, no_rich=True) == legacy.EXIT_RUN_FAILED


def test_trace_history_empty_and_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.agent.trace import TraceWriter

    monkeypatch.setattr(TraceWriter, "find_trace_dir", lambda *args, **kwargs: None)
    assert legacy._build_history_from_trace(tmp_path) == []
    monkeypatch.setattr(TraceWriter, "find_trace_dir", lambda *args, **kwargs: tmp_path)
    monkeypatch.setattr(
        TraceWriter,
        "read",
        lambda *args, **kwargs: [
            {"type": "start", "prompt": "question"},
            {"type": "answer", "content": "answer"},
            {"type": "tool", "content": "ignored"},
            {"type": "start", "prompt": ""},
        ],
    )
    assert legacy._build_history_from_trace(tmp_path) == [
        {"role": "user", "content": "question"},
        {"role": "assistant", "content": "answer"},
    ]
