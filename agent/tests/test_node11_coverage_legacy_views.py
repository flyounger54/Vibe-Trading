"""Branch coverage for legacy CLI dashboards, trace replay, and history views."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from rich.console import Console

from cli import _legacy as legacy
from src.swarm.models import RunStatus, TaskStatus


pytestmark = pytest.mark.unit


def _console() -> Console:
    return Console(record=True, width=120, color_system=None)


def _event(kind: str, agent: str | None = None, **data: object) -> SimpleNamespace:
    return SimpleNamespace(type=kind, agent_id=agent, data=data)


def test_swarm_dashboard_event_and_render_state_matrix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    times = iter(range(100, 300))
    monkeypatch.setattr(legacy.time, "monotonic", lambda: float(next(times)))
    dashboard = legacy._SwarmDashboard("quality", "run-1")
    dashboard.handle_event(_event("layer_started", layer=2))
    dashboard.handle_event(_event("ignored"))
    dashboard.handle_event(_event("task_started", "running"))
    dashboard.handle_event(_event("tool_call", "running", tool="search"))
    dashboard.handle_event(_event("worker_text", "running", content="first\nlast line"))
    dashboard.handle_event(_event("worker_text", "running", content="  "))
    dashboard.handle_event(_event("tool_result", "running", status="error"))
    dashboard.handle_event(
        _event("task_completed", "done", iterations=3, summary="completed summary")
    )
    dashboard.handle_event(_event("task_failed", "failed", error="broken" * 30))
    dashboard.handle_event(_event("task_blocked", "blocked", blocked_by=["upstream"]))
    dashboard.handle_event(_event("task_retry", "retry", attempt=2))
    dashboard._ensure_agent("waiting")
    assert dashboard._ensure_agent("waiting") == "waiting"
    assert len(dashboard.build_table().rows) == 7

    dashboard.handle_event(_event("run_completed", status="completed"))
    assert dashboard.finished and len(dashboard.build_table().rows) == 7
    dashboard.final_status = "failed"
    assert "FAILED" in str(dashboard.build_table().title)
    assert len(dashboard.completed_summaries) == 3


def test_trace_replay_all_event_variants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.agent.trace import TraceWriter

    console = _console()
    monkeypatch.setattr(legacy, "console", console)
    monkeypatch.setattr(TraceWriter, "find_trace_dir", lambda *args, **kwargs: None)
    legacy.cmd_trace("missing")
    monkeypatch.setattr(TraceWriter, "find_trace_dir", lambda *args, **kwargs: "/trace")
    monkeypatch.setattr(TraceWriter, "read", lambda *args, **kwargs: [])
    legacy.cmd_trace("empty")

    entries = [
        {"type": "start", "ts": 1, "iter": 1, "prompt": "p" * 130},
        {"type": "thinking", "content": "thinking"},
        {"type": "tool_call", "tool": "search", "args": {"q": "x" * 60}},
        {"type": "tool_call", "tool": "read", "args": {}},
        {"type": "tool_result", "tool": "search", "status": "ok", "preview": "done"},
        {
            "type": "tool_result",
            "tool": "bash",
            "status": "error",
            "result": "failed",
            "result_path": "/tmp/offload",
            "result_size": 4096,
        },
        {"type": "tool_skipped", "tool": "write"},
        {"type": "message", "role": "user", "content_preview": "question"},
        {"type": "message", "role": "assistant", "content": "reply"},
        {"type": "answer", "content": "final"},
        {"type": "end", "status": "success", "iterations": 2},
        {"type": "end", "status": "failed", "iterations": 1},
        {"type": "unknown"},
    ]
    monkeypatch.setattr(TraceWriter, "read", lambda *args, **kwargs: entries)
    legacy.cmd_trace("run-1")
    rendered = console.export_text()
    assert "Trace replay" in rendered and "offloaded" in rendered and "ANSWER" in rendered


def test_run_list_show_and_code_view_branches(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    console = _console()
    monkeypatch.setattr(legacy, "console", console)
    runs = tmp_path / "runs"
    sessions = tmp_path / "sessions"
    monkeypatch.setattr(legacy, "RUNS_DIR", runs)
    monkeypatch.setattr(legacy, "SESSIONS_DIR", sessions)
    legacy.cmd_list()
    runs.mkdir()
    legacy.cmd_list()

    run = runs / "r1"
    (run / "artifacts").mkdir(parents=True)
    (run / "code").mkdir()
    (run / "state.json").write_text('{"status":"success","reason":"done"}', encoding="utf-8")
    (run / "req.json").write_text(
        '{"prompt":"' + "p" * 600 + '"}', encoding="utf-8"
    )
    (run / "artifacts" / "metrics.csv").write_text(
        "total_return,sharpe\n0.1,1.2\n", encoding="utf-8"
    )
    (run / "code" / "signal_engine.py").write_text("print('ok')\n", encoding="utf-8")
    from src.agent.trace import TraceWriter

    monkeypatch.setattr(TraceWriter, "find_trace_dir", lambda *args, **kwargs: run)
    monkeypatch.setattr(
        TraceWriter,
        "read",
        lambda *args, **kwargs: [{"type": "answer", "content": "a" * 250}],
    )
    legacy.cmd_list()
    legacy.cmd_show("missing")
    legacy.cmd_show("r1")
    legacy.cmd_code("missing")
    legacy.cmd_code("r1")
    rendered = console.export_text()
    assert "Recent Runs" in rendered and "Reason" in rendered and "print" in rendered


def test_swarm_preset_list_and_show_view_matrices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.swarm.presets as presets_module
    import src.swarm.store as store_module

    console = _console()
    monkeypatch.setattr(legacy, "console", console)
    monkeypatch.setattr(presets_module, "list_presets", lambda: [])
    legacy.cmd_swarm_presets()
    monkeypatch.setattr(
        presets_module,
        "list_presets",
        lambda: [
            {
                "name": "p",
                "title": "Preset",
                "variables": [{"name": "symbol"}, "market"],
                "description": "d" * 60,
            }
        ],
    )
    legacy.cmd_swarm_presets()

    class Store:
        runs: list[object] = []
        loaded: object | None = None

        def __init__(self, **kwargs: object) -> None:
            pass

        def list_runs(self) -> list[object]:
            return self.runs

        def load_run(self, run_id: str) -> object | None:
            return self.loaded

    monkeypatch.setattr(store_module, "SwarmStore", Store)
    legacy.cmd_swarm_list()
    Store.runs = [
        SimpleNamespace(
            id=f"r-{status.value}",
            preset_name="p",
            status=status,
            tasks=[],
            created_at="2026-01-01T00:00:00",
        )
        for status in [
            RunStatus.completed,
            RunStatus.failed,
            RunStatus.cancelled,
            RunStatus.running,
            RunStatus.pending,
        ]
    ]
    legacy.cmd_swarm_list()
    legacy.cmd_swarm_show("missing")

    tasks = [
        SimpleNamespace(
            id="done",
            agent_id="a",
            depends_on=["root"],
            status=TaskStatus.completed,
            summary="summary",
            error=None,
        ),
        SimpleNamespace(
            id="failed",
            agent_id="b",
            depends_on=[],
            status=TaskStatus.failed,
            summary=None,
            error="failure",
        ),
        SimpleNamespace(
            id="pending",
            agent_id="c",
            depends_on=[],
            status=TaskStatus.pending,
            summary=None,
            error=None,
        ),
    ]
    Store.loaded = SimpleNamespace(
        id="r1",
        status=RunStatus.completed,
        preset_name="p",
        created_at="created",
        completed_at="completed",
        user_vars={"symbol": "AAPL"},
        total_input_tokens=10,
        total_output_tokens=20,
        tasks=tasks,
        final_report="report",
    )
    legacy.cmd_swarm_show("r1")
    rendered = console.export_text()
    assert "No presets" in rendered and "Variables" in rendered and "Final Report" in rendered


def test_session_list_empty_active_archived_and_message_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.session.store as store_module
    import src.state as state_module

    console = _console()
    monkeypatch.setattr(legacy, "console", console)

    class Store:
        sessions: list[object] = []

        def __init__(self, **kwargs: object) -> None:
            pass

        def list_sessions(self) -> list[object]:
            return self.sessions

        def get_messages(self, session_id: str) -> list[object]:
            return [object(), object()]

    monkeypatch.setattr(store_module, "SessionStore", Store)
    monkeypatch.setattr(state_module, "default_state_db_path", lambda: "/tmp/state.db")
    legacy.cmd_sessions()
    Store.sessions = [
        SimpleNamespace(
            session_id="a",
            title="",
            status=SimpleNamespace(value="active"),
            updated_at="2026-01-01T00:00:00",
        ),
        SimpleNamespace(
            session_id="b",
            title="Archived",
            status=SimpleNamespace(value="archived"),
            updated_at="2026-01-02T00:00:00",
        ),
    ]
    legacy.cmd_sessions()
    rendered = console.export_text()
    assert "No sessions" in rendered and "Archived" in rendered
