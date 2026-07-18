"""Complete branch contracts for the shared CLI activity rail."""

from __future__ import annotations

import pytest
from rich.text import Text

from cli.ui import rail


pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "name,expected",
    [
        ("get_market_data", "Market Data"),
        ("read-file", "Read File"),
        ("", "Tool"),
    ],
)
def test_name_and_integer_normalization(name, expected) -> None:
    assert rail._format_tool_name(name) == expected
    assert rail._coerce_int("3") == 3
    assert rail._coerce_int(-2) == 0
    assert rail._coerce_int("bad") == 0


@pytest.mark.parametrize(
    "tool,expected",
    [
        ("bash", "shell command"),
        ("background_run", "shell command"),
        ("load_skill", "skill"),
        ("read_file", "file"),
        ("edit_file", "file"),
        ("web_fetch", "url"),
        ("web_search", "web"),
        ("get_stock_price", "market data"),
        ("final_answer", "answer"),
        ("custom_tool", "Custom Tool"),
    ],
)
def test_tool_titles(tool, expected) -> None:
    assert rail._tool_title(tool, {}) == expected


def test_argument_and_initial_detail_variants() -> None:
    assert rail._arg_value("bad", "x") == ""
    assert rail._arg_value({"a": "", "b": " value\n"}, "a", "b") == "value"
    assert rail._initial_detail("bash", {"command": "pwd"}) is None
    assert rail._initial_detail("read_file", {"file_path": "a/b.py"}) == "a/b.py"
    assert rail._initial_detail("write_file", {}) is None
    assert rail._initial_detail("read_url", {"url": "https://example.com/a"}) == "fetching example.com/a"
    assert rail._initial_detail("search", {"q": "momentum"}) == "momentum"
    assert rail._initial_detail("market_data", {"ticker": "AAPL"}) == "AAPL"
    assert rail._initial_detail("tool", None) is None


@pytest.mark.parametrize(
    "tool,status,preview,expected",
    [
        ("load_skill", "ok", '<skill name="alpha">', ["loaded alpha"]),
        ("bash", "ok", '{"stdout":"one\\ntwo"}', ["one", "two"]),
        ("bash", "ok", '{"stderr":"warning"}', ["warning"]),
        ("bash", "ok", "{}", ["completed"]),
        ("read_file", "ok", '{"content":"abc"}', ["read 3 chars"]),
        ("read_file", "ok", "{}", ["read file"]),
        (
            "load_skill",
            "ok",
            '{"content":"<skill\\u0020name=\\u0022beta\\u0022>"}',
            ["loaded beta"],
        ),
        ("load_skill", "ok", "{}", ["loaded skill"]),
        ("read_url", "ok", '{"title":" Example title "}', ["Example title"]),
        ("read_url", "ok", '{"markdown":"hello"}', ["read 5 chars"]),
        ("search", "ok", '{"results":[1,2]}', ["Did 1 search · 2 results"]),
        ("tool", "ok", '{"summary":"done"}', ["done"]),
        ("tool", "error", '{"error":"failed"}', ["Error: failed"]),
        ("tool", "error", '{"stderr":"denied"}', ["Error: denied"]),
        ("tool", "error", "plain failure", ["Error: plain failure"]),
        ("tool", "ok", "", []),
        ("tool", "ok", "not {json", ["not {json"]),
    ],
)
def test_result_summary_contracts(tool, status, preview, expected) -> None:
    assert rail._result_summary(tool, status, preview) == expected


def test_compaction_url_clipping_wrapping_and_command_helpers() -> None:
    lines = rail._compact_lines("one\ntwo\nthree\nfour\nfive", max_lines=3)
    assert lines == ["one", "... +3 lines (ctrl + t to view transcript)", "five"]
    assert rail._compact_lines(" \n", max_lines=2) == []
    assert rail._short_url("") == ""
    assert rail._short_url("relative/path") == "relative/path"
    assert rail._short_url("http://[broken") == "http://[broken"
    assert rail._clip("short", 10) == "short"
    assert rail._clip("long value", 5) == "long…"
    assert rail._clip_preserve("abcdef", 4) == "abc…"
    assert rail._wrap("abcdefghijk", 5) == ["abcd…"]
    assert rail._wrap("two words", 20) == ["two words"]
    assert rail._split_command("") == ("", "")
    assert rail._split_command('"C:\\Program Files\\Python.exe" script.py') == (
        "Python",
        "script.py",
    )
    assert rail._split_command("/usr/bin/python task.py") == ("python", "task.py")
    assert rail._display_executable("") == ""
    line = Text()
    rail._append_arg_text(line, "--file ./path/data.csv --flag", limit=80)
    assert "./path/data.csv" in line.plain
    empty = Text()
    rail._append_arg_text(empty, "", limit=10)
    assert empty.plain == ""


def _dashboard(monkeypatch: pytest.MonkeyPatch) -> rail.RailRunDashboard:
    dashboard = rail.RailRunDashboard("test", 10)
    monkeypatch.setattr(dashboard, "refresh", lambda: None)
    return dashboard


def test_dashboard_event_state_machine(monkeypatch: pytest.MonkeyPatch) -> None:
    dashboard = _dashboard(monkeypatch)
    try:
        dashboard.handle_event("text_delta", {"delta": ""})
        dashboard.handle_event("text_delta", {"delta": "answer"})
        dashboard.handle_event("thinking_done", {})
        dashboard.handle_event("llm_usage", {"input_tokens": "2", "output_tokens": 3})
        dashboard.handle_event("llm_usage", {"total_tokens": 4})
        assert (dashboard.input_tokens, dashboard.output_tokens) == (2, 7)

        dashboard.handle_event("tool_call", {"tool": "read_file", "arguments": {"path": "a.py"}})
        dashboard.handle_event(
            "tool_progress",
            {"tool": "read_file", "stage": "read", "current": 1, "total": 2, "message": "half"},
        )
        dashboard.handle_event("tool_heartbeat", {"tool": "read_file"})
        dashboard.steps[-1].lines.append("still running… 1s")
        dashboard.handle_event(
            "tool_result",
            {"tool": "read_file", "status": "ok", "elapsed_ms": 1250, "preview": '{"content":"abc"}'},
        )
        assert dashboard.steps[-1].status == "done"
        assert dashboard.steps[-1].duration_s == 1.25
        assert dashboard.steps[-1].lines[-1] == "read 3 chars"

        dashboard.handle_event("tool_result", {"tool": "missing", "status": "error", "preview": "boom"})
        assert dashboard.steps[-1].status == "error"
        dashboard.handle_event("compact", {"tokens_before": 100})
        assert dashboard.steps[-1].status == "warning"
        dashboard.finish({"status": "failed", "reason": "stopped"}, elapsed=2.0)
        assert dashboard.completion_summary == "Failed. stopped"
        assert not dashboard.thinking_active
    finally:
        dashboard.close()


@pytest.mark.parametrize(
    "result,expected",
    [
        ({"status": "success", "content": "answer"}, "Done."),
        ({"status": "done"}, "Done."),
    ],
)
def test_dashboard_finish_summaries(result, expected, monkeypatch: pytest.MonkeyPatch) -> None:
    dashboard = _dashboard(monkeypatch)
    dashboard.steps.append(rail.RailStep("active", duration_s=0))
    dashboard.finish(result, elapsed=-1)
    assert dashboard.completion_summary == expected
    assert dashboard.steps[0].duration_s == 0


def test_dashboard_render_and_step_styles(monkeypatch: pytest.MonkeyPatch) -> None:
    dashboard = _dashboard(monkeypatch)
    try:
        steps = [
            rail.RailStep("shell", tool="bash", args={"command": "python ./job.py"}),
            rail.RailStep("shell", tool="bash", args={}, status="done", duration_s=1),
            rail.RailStep("file", tool="read_file", args={"path": "src/a.py"}, status="done"),
            rail.RailStep("search", tool="web_search", args={"q": "alpha"}, status="error"),
            rail.RailStep("context", tool="compact", status="warning", lines=["compressed"]),
        ]
        dashboard.steps = steps
        rendered = dashboard.render()
        assert rendered.renderables
        for step in steps:
            assert dashboard._step_line(step, width=80).plain.startswith("• ")
        dashboard.completion_summary = "Finished."
        dashboard.status = "success"
        assert dashboard._finish_line("Finished.").plain == "•  Finished."
        dashboard.status = "failed"
        assert dashboard._finish_line("Failed.").plain == "•  Failed."
    finally:
        dashboard.close()


@pytest.mark.parametrize(
    "tool,active,expected",
    [
        ("load_skill", True, "Loading skill…"),
        ("load_skill", False, "Loaded skill"),
        ("read_file", True, "Reading file…"),
        ("write_file", False, "Wrote file"),
        ("read_url", True, "Fetching…"),
        ("web_search", False, "Searched web"),
        ("market_data", True, "Reading market data…"),
        ("compact", False, "Compacted context"),
        ("custom", False, "Custom"),
    ],
)
def test_action_and_detail_labels(tool, active, expected, monkeypatch: pytest.MonkeyPatch) -> None:
    dashboard = _dashboard(monkeypatch)
    try:
        step = rail.RailStep(
            "Custom",
            tool=tool,
            args={"name": "skill", "path": "a.py", "url": "https://x.test/a", "q": "query", "symbol": "AAPL"},
            status="active" if active else "done",
        )
        assert dashboard._action_label(step) == expected
        detail = dashboard._title_detail(step)
        if tool != "compact":
            assert detail
    finally:
        dashboard.close()


def test_active_step_append_and_detail_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    dashboard = _dashboard(monkeypatch)
    try:
        created = dashboard._active_step("tool")
        assert dashboard._active_step("") is created
        created.status = "done"
        second = dashboard._active_step("tool")
        dashboard._append_to_active("", throttle=False)
        dashboard._append_to_active("detail", throttle=False)
        assert second.lines == ["detail"]
        dashboard._append_to_active("skipped", throttle=True)
        assert second.lines == ["detail"]
        rows = dashboard._detail_lines("one two three", width=5, active=True)
        assert rows[0].plain.startswith("  └")
        assert dashboard._render_lines(rail.RailStep("x", lines=[str(i) for i in range(10)])) == [
            str(i) for i in range(3, 10)
        ]
        assert rail._looks_like_path("src/a.py")
        assert not rail._looks_like_path("plain")
        line = Text("x")
        dashboard._append_duration(line, "")
        dashboard._append_duration(line, "1s")
        assert line.plain == "x  1s"
    finally:
        dashboard.close()
