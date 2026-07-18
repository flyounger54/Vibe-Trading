"""Deterministic coverage for the maintained legacy CLI command surface."""

from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from rich.console import Console

from cli import _legacy as legacy


pytestmark = pytest.mark.unit


def _console() -> Console:
    return Console(record=True, width=120, color_system=None)


def test_status_prompt_and_output_helpers(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys) -> None:
    monkeypatch.setenv("LANGCHAIN_PROVIDER", "openrouter")
    monkeypatch.setenv("LANGCHAIN_MODEL_NAME", "vendor/model")
    monkeypatch.setattr(legacy.time, "monotonic", lambda: 3725.0)
    stats = legacy._SessionStats(0.0)
    stats.last_elapsed = 1.25
    stats.tool_count = 2
    stats.total_tool_ms = 3500
    parts = legacy._build_status_parts(stats)
    assert parts == ["openrouter/model", "62m05s", "last 1.2s", "2 tools (3.5s)"]
    assert "openrouter/model" in str(legacy._ptk_toolbar(stats))
    monkeypatch.setattr(legacy, "console", _console())
    legacy._print_status_bar(stats)

    session = SimpleNamespace(prompt=lambda prompt: "toolkit")
    monkeypatch.setattr(legacy.sys.stdin, "isatty", lambda: True)
    assert legacy._read_input(session) == "toolkit"
    monkeypatch.setattr(legacy.Prompt, "ask", lambda *args, **kwargs: "rich")
    assert legacy._read_input(None) == "rich"

    assert legacy._strip_rich_tags("[red]failure[/red]") == "failure"
    legacy._print_json_result({"status": "success", "run_id": "r", "run_dir": "/tmp/r"})
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["run_id"] == "r"
    assert legacy._result_exit_code({"status": "success"}) == 0
    assert legacy._result_exit_code({"status": "failed"}) == 1
    assert legacy._coerce_exit_code(None) == 0
    assert legacy._coerce_exit_code(2) == 2

    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text("  analyze AAPL  ", encoding="utf-8")
    assert legacy._read_prompt_source(" direct ", None, no_rich=True) == ("direct", None)
    assert legacy._read_prompt_source(None, prompt_file, no_rich=True) == ("analyze AAPL", None)
    assert legacy._read_prompt_source(None, tmp_path / "missing", no_rich=True)[0] is None
    monkeypatch.setattr(legacy.sys, "stdin", io.StringIO(" piped request "))
    assert legacy._read_prompt_source(None, None, no_rich=True) == ("piped request", None)

    class TtyInput(io.StringIO):
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr(legacy.sys, "stdin", TtyInput())
    assert legacy._read_prompt_source(None, None, no_rich=True, allow_interactive=False)[1] == "A prompt is required."
    monkeypatch.setattr("builtins.input", lambda prompt: "typed")
    assert legacy._read_prompt_source(None, None, no_rich=True) == ("typed", None)
    monkeypatch.setattr("builtins.input", lambda prompt: (_ for _ in ()).throw(EOFError()))
    assert legacy._read_prompt_source(None, None, no_rich=True)[1] == "Prompt input cancelled."


def test_json_metrics_styles_widths_and_text_helpers(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(legacy, "console", _console())
    missing = tmp_path / "missing.json"
    assert legacy._read_json(missing) == {}
    payload = tmp_path / "data.json"
    payload.write_text('{"ok": true}', encoding="utf-8")
    assert legacy._read_json(payload) == {"ok": True}
    payload.write_text("bad", encoding="utf-8")
    assert legacy._read_json(payload) == {}

    metrics = tmp_path / "metrics.csv"
    assert legacy._read_metrics(metrics) == {}
    metrics.write_text("sharpe,total_return,label,empty\n1.23456,123.4,good,\n", encoding="utf-8")
    assert legacy._read_metrics(metrics) == {"sharpe": "1.2346", "total_return": "123", "label": "good"}
    metrics.write_text("sharpe\n", encoding="utf-8")
    assert legacy._read_metrics(metrics) == {}

    assert legacy._status_style("completed") == "green"
    assert legacy._status_style("unknown") == "dim"
    assert legacy._format_seconds(-1) == "0s"
    assert legacy._format_seconds(61) == "1m 01s"
    assert legacy._format_seconds(3720) == "1h 02m"
    assert "configured" in legacy._configured_label("yes")
    assert "MISSING" in legacy._state_badge(None)
    assert legacy._provider_key_env("QWEN") == "DASHSCOPE_API_KEY"
    assert legacy._provider_key_env("unknown") is None
    assert legacy._provider_base_env("ollama") == "OLLAMA_BASE_URL"
    assert legacy._clip_inline("a  b c", 20) == "a b c"
    assert legacy._clip_inline("abcdefgh", 5) == "ab..."
    assert legacy._fit_cell("abcdef", 4) == "a..."
    assert legacy._welcome_widths(60)["gap"] == 2
    assert legacy._welcome_widths(120)["gap"] == 4
    assert legacy._metric_value_style("sharpe", "1") == "green"
    assert legacy._metric_value_style("sharpe", "-1") == "red"
    assert legacy._metric_value_style("sharpe", "bad") == "white"
    assert legacy._metric_value_style("max_drawdown", "-1") == "yellow"
    assert legacy._metric_value_style("other", "1") == "white"
    stacked = legacy._stack_text([
        legacy._styled_line([("A", 3, "red")]),
        legacy._styled_line([("B", None, "blue")]),
    ])
    assert stacked.plain == "A  \nB"

    monkeypatch.setattr(type(legacy.console), "size", property(lambda self: (_ for _ in ()).throw(RuntimeError())))
    assert legacy._terminal_width() == 80


@pytest.mark.parametrize(
    ("tool", "args", "expected"),
    [
        ("load_skill", {"name": "risk"}, '("risk")'),
        ("write_file", {"path": "/tmp/x"}, " /tmp/x"),
        ("bash", {"command": "echo ok"}, "echo ok"),
        ("check_background", {"task_id": "t1"}, " t1"),
        ("backtest", {}, ""),
        ("other", {"first": "value"}, " value"),
        ("other", {"first": "None"}, ""),
    ],
)
def test_tool_argument_formatting(tool, args, expected) -> None:
    assert expected in legacy._format_tool_call_args(tool, args)


def test_tool_result_preview_variants() -> None:
    assert "failure" in legacy._format_tool_result_preview("x", "error", "failure")
    backtest = legacy._format_tool_result_preview("backtest", "ok", '{"sharpe": 1.2, "total_return": 0.15}')
    assert "sharpe=1.2" in backtest and "return=15.0%" in backtest
    assert legacy._format_tool_result_preview("backtest", "ok", "none") == ""
    assert "https://report" in legacy._format_tool_result_preview("render_shadow_report", "ok", '{"report_url": "https://report"}')
    assert legacy._format_tool_result_preview("render_shadow_report", "ok", "none") == ""
    assert legacy._format_tool_result_preview("extract_shadow_strategy", "ok", '{"shadow_id": "s1"}') == "shadow_id=s1"
    assert legacy._format_tool_result_preview("run_shadow_backtest", "ok", "none") == ""
    assert legacy._format_tool_result_preview("bash", "ok", "OK done") == "OK"
    assert legacy._format_tool_result_preview("bash", "ok", "line one\nline two") == "line one line two"
    assert legacy._format_tool_result_preview("read_file", "ok", "body") == ""
    assert legacy._format_tool_result_preview("unknown", "ok", "body") == ""


def test_mandate_proposal_recovery_guards(monkeypatch: pytest.MonkeyPatch) -> None:
    proposal_id = "mp_" + "a" * 32
    assert legacy._mandate_proposal_from_tool_result({"tool": "other", "status": "ok"}) is None
    assert legacy._mandate_proposal_from_tool_result({"tool": legacy._PROPOSAL_TOOL_NAME, "status": "error"}) is None
    assert legacy._mandate_proposal_from_tool_result({"tool": legacy._PROPOSAL_TOOL_NAME, "status": "ok", "preview": "none"}) is None
    monkeypatch.setattr(legacy, "_load_full_proposal", lambda pid: {"proposal_id": pid})
    recovered = legacy._mandate_proposal_from_tool_result({
        "tool": legacy._PROPOSAL_TOOL_NAME, "status": "ok", "preview": f'{{"proposal_id":"{proposal_id}"}}',
    })
    assert recovered == {"proposal_id": proposal_id}


def test_welcome_help_and_settings_render_responsive_variants(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    console = _console()
    monkeypatch.setattr(legacy, "console", console)
    monkeypatch.setattr(legacy, "_ensure_cli_env", lambda: None)
    monkeypatch.setattr(legacy, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(legacy, "SWARM_DIR", tmp_path / "swarms")
    monkeypatch.setenv("LANGCHAIN_PROVIDER", "openai")
    monkeypatch.setenv("LANGCHAIN_MODEL_NAME", "gpt")
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    assert legacy._build_welcome_panel(60) is not None
    assert legacy._build_welcome_panel(120) is not None
    legacy._print_welcome()
    legacy._print_help()

    monkeypatch.setattr(legacy, "_terminal_width", lambda: 80)
    legacy._show_settings()
    monkeypatch.setattr(legacy, "_terminal_width", lambda: 140)
    monkeypatch.setenv("LANGCHAIN_PROVIDER", "ollama")
    legacy._show_settings()
    monkeypatch.setenv("LANGCHAIN_PROVIDER", "unknown")
    legacy._show_settings()
    assert "Current Config" in console.export_text()


def test_slash_and_swarm_command_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, tuple]] = []
    monkeypatch.setattr(legacy, "console", _console())
    for name in (
        "_print_help", "cmd_skills", "cmd_list", "cmd_show", "cmd_code", "cmd_pine", "cmd_trace",
        "cmd_continue", "cmd_sessions", "_show_settings", "_print_welcome", "cmd_swarm_presets",
        "cmd_swarm_run_live", "cmd_swarm_inspect", "cmd_swarm_list", "cmd_swarm_show", "cmd_swarm_cancel",
    ):
        monkeypatch.setattr(legacy, name, lambda *args, _name=name, **kwargs: calls.append((_name, args)))

    for command in (
        "/help", "/skills", "/list", "/show run", "/show", "/code run", "/code",
        "/pine run", "/pine", "/trace run", "/trace", "/continue run refine", "/continue run",
        "/sessions", "/settings", "/stop", "/clear", "/unknown",
    ):
        legacy._handle_slash_command(command, max_iter=5)
    with pytest.raises(EOFError):
        legacy._handle_slash_command("/quit", max_iter=5)

    for command in ("", "run preset {\"x\":1}", "run", "inspect preset", "inspect", "list", "show run", "show", "cancel run", "cancel", "bad"):
        legacy._handle_swarm_command(command)
    names = [name for name, _ in calls]
    assert "cmd_show" in names and "cmd_swarm_run_live" in names and "cmd_swarm_cancel" in names


def _base_args(**overrides):
    values = {
        "command": None,
        "no_rich": False,
        "run_no_rich": False,
        "list": False,
        "show": None,
        "code": None,
        "pine": None,
        "trace": None,
        "skills": False,
        "swarm_presets": False,
        "swarm_inspect": None,
        "swarm_run": None,
        "swarm_list": False,
        "swarm_show": None,
        "swarm_cancel": None,
        "sessions": False,
        "session_chat": None,
        "upload": None,
        "chat": False,
        "cont": None,
        "prompt": None,
        "prompt_file": None,
        "max_iter": 5,
        "json": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_main_dispatches_subcommands_and_legacy_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, tuple]] = []

    class Parser:
        def __init__(self, args) -> None:
            self.args = args

        def parse_args(self, argv):
            return self.args

    for name in (
        "cmd_init", "serve_main", "cmd_provider_login", "cmd_provider_doctor", "_handle_prompt_command",
        "cmd_list", "cmd_show", "cmd_interactive", "_dispatch_connector", "cmd_memory_list",
        "cmd_memory_show", "cmd_memory_search", "cmd_memory_forget", "cmd_code", "cmd_pine", "cmd_trace",
        "cmd_skills", "cmd_swarm_presets", "cmd_swarm_inspect", "cmd_swarm_run_live", "cmd_swarm_list",
        "cmd_swarm_show", "cmd_swarm_cancel", "cmd_sessions", "cmd_session_chat", "cmd_upload", "cmd_continue",
    ):
        monkeypatch.setattr(legacy, name, lambda *args, _name=name, **kwargs: calls.append((_name, args)) or 0)
    monkeypatch.setattr(legacy, "console", _console())
    monkeypatch.setattr("src.factors.cli_handlers.dispatch", lambda args: 0)
    monkeypatch.setattr("src.hypotheses.cli_handlers.dispatch", lambda args: 0)

    cases = [
        _base_args(command="init"),
        _base_args(command="serve"),
        _base_args(command="provider", provider_command="login", provider="openai-codex"),
        _base_args(command="provider", provider_command="doctor", provider=None),
        _base_args(command="provider", provider_command=None, provider=None),
        _base_args(command="run", run_prompt="x", run_prompt_file=None, run_max_iter=2, run_json=True),
        _base_args(command="list", list_limit=3),
        _base_args(command="show", show="run"),
        _base_args(command="chat", chat_max_iter=4),
        _base_args(command="alpha"),
        _base_args(command="hypothesis"),
        _base_args(command="connector"),
        _base_args(command="memory", memory_command="list", memory_type="all"),
        _base_args(command="memory", memory_command="show", name="item"),
        _base_args(command="memory", memory_command="search", query="alpha", memory_limit=5),
        _base_args(command="memory", memory_command="forget", name="item", yes=True),
        _base_args(command="memory", memory_command=None),
        _base_args(list=True),
        _base_args(show="run"),
        _base_args(code="run"),
        _base_args(pine="run"),
        _base_args(trace="run"),
        _base_args(skills=True),
        _base_args(swarm_presets=True),
        _base_args(swarm_inspect="preset"),
        _base_args(swarm_run=["preset"]),
        _base_args(swarm_run=["preset", "{}"]),
        _base_args(swarm_list=True),
        _base_args(swarm_show="run"),
        _base_args(swarm_cancel="run"),
        _base_args(sessions=True),
        _base_args(session_chat="session"),
        _base_args(upload="file"),
        _base_args(chat=True),
        _base_args(cont=["run", "refine"]),
        _base_args(prompt="analyze"),
    ]
    for args in cases:
        monkeypatch.setattr(legacy, "_build_parser", lambda _args=args: Parser(_args))
        assert legacy.main(["command"]) in {0, legacy.EXIT_USAGE_ERROR}
    names = {name for name, _ in calls}
    assert {"cmd_init", "serve_main", "cmd_continue", "cmd_memory_forget", "cmd_swarm_run_live"} <= names

    class ExitParser:
        def parse_args(self, argv):
            raise SystemExit("bad")

    monkeypatch.setattr(legacy, "_build_parser", lambda: ExitParser())
    assert legacy.main(["bad"]) == legacy.EXIT_USAGE_ERROR


def test_upload_provider_login_and_swarm_inspection(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    console = _console()
    monkeypatch.setattr(legacy, "console", console)
    upload_dir = tmp_path / "uploads"
    monkeypatch.setattr(legacy, "UPLOADS_DIR", upload_dir)
    legacy.cmd_upload(str(tmp_path / "missing"))
    directory = tmp_path / "directory"
    directory.mkdir()
    legacy.cmd_upload(str(directory))
    source = tmp_path / "data.csv"
    source.write_text("a,b\n1,2\n", encoding="utf-8")
    legacy.cmd_upload(str(source))
    assert len(list(upload_dir.glob("*.csv"))) == 1

    assert legacy.cmd_provider_login("unknown") == legacy.EXIT_USAGE_ERROR
    monkeypatch.setattr("src.providers.openai_codex.login_openai_codex", lambda **kwargs: SimpleNamespace(account_id="acct"))
    assert legacy.cmd_provider_login("openai_codex") == legacy.EXIT_SUCCESS
    monkeypatch.setattr("src.providers.openai_codex.login_openai_codex", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("oauth")))
    assert legacy.cmd_provider_login("openai-codex") == legacy.EXIT_RUN_FAILED

    valid = {
        "valid": True, "name": "quality", "title": "Quality", "description": "desc",
        "agents": [{"id": "a", "role": "analyst", "tools": ["search"]}],
        "tasks": [{"id": "t", "agent_id": "a", "depends_on": []}],
        "variables": ["symbol"], "layers": [[{"task_id": "t", "agent_id": "a"}]],
        "errors": [], "warnings": [],
    }
    monkeypatch.setattr("src.swarm.presets.inspect_preset", lambda name: valid)
    assert legacy.cmd_swarm_inspect("quality") == legacy.EXIT_SUCCESS
    invalid = {**valid, "valid": False, "errors": ["cycle"], "warnings": ["unused"]}
    monkeypatch.setattr("src.swarm.presets.inspect_preset", lambda name: invalid)
    assert legacy.cmd_swarm_inspect("invalid") == legacy.EXIT_RUN_FAILED
    monkeypatch.setattr("src.swarm.presets.inspect_preset", lambda name: (_ for _ in ()).throw(FileNotFoundError("missing")))
    assert legacy.cmd_swarm_inspect("missing") == legacy.EXIT_USAGE_ERROR
    monkeypatch.setattr("src.swarm.presets.inspect_preset", lambda name: (_ for _ in ()).throw(RuntimeError("broken")))
    assert legacy.cmd_swarm_inspect("broken") == legacy.EXIT_RUN_FAILED
