"""Dense branch coverage for the interactive CLI front door and REPL routing."""

from __future__ import annotations

import builtins
import importlib
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from rich.console import Console


main = importlib.import_module("cli.main")
pytestmark = pytest.mark.unit
_SESSION_ID = "a1b2c3d4e5f6"


def _console() -> Console:
    return Console(record=True, width=120, color_system=None)


def test_model_tool_skill_session_and_banner_probe_matrix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("LANGCHAIN_MODEL_NAME", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    env = tmp_path / ".env"
    env.write_text("OTHER=x\nLANGCHAIN_MODEL_NAME=from-file\n", encoding="utf-8")
    monkeypatch.setattr(main, "_ENV_PATH", env)
    assert main._probe_model_name() == "from-file"
    env.write_text("OTHER=x\n", encoding="utf-8")
    assert main._probe_model_name().startswith("unset")
    monkeypatch.setattr(main, "_ENV_PATH", tmp_path / "missing")
    assert main._probe_model_name().startswith("unset")
    monkeypatch.setenv("OPENAI_MODEL", "from-env")
    assert main._probe_model_name() == "from-env"

    import src.agent.skills as skills_module
    import src.tools as tools_module

    monkeypatch.setattr(tools_module, "build_registry", lambda: [1, 2])
    assert main._probe_tool_count() == 2
    monkeypatch.setattr(tools_module, "build_registry", lambda: (_ for _ in ()).throw(RuntimeError()))
    assert main._probe_tool_count() == main._FALLBACK_TOOLS
    monkeypatch.setattr(skills_module, "SkillsLoader", lambda: SimpleNamespace(skills=[1]))
    assert main._probe_skill_count() == 1
    monkeypatch.setattr(skills_module, "SkillsLoader", lambda: (_ for _ in ()).throw(RuntimeError()))
    assert main._probe_skill_count() == main._FALLBACK_SKILLS

    import src.state as state_module

    db = tmp_path / "state.db"
    monkeypatch.setattr(state_module, "default_state_db_path", lambda: db)
    assert main._probe_session_count() == 0
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE sessions (id TEXT)")
        conn.executemany("INSERT INTO sessions VALUES (?)", [("a",), ("b",)])
    assert main._probe_session_count() == 2
    monkeypatch.setattr(sqlite3, "connect", lambda *args: (_ for _ in ()).throw(RuntimeError()))
    assert main._probe_session_count() == 0

    monkeypatch.setattr(main, "_probe_model_name", lambda: "m")
    monkeypatch.setattr(main, "_probe_skill_count", lambda: 1)
    monkeypatch.setattr(main, "_probe_tool_count", lambda: 2)
    monkeypatch.setattr(main, "_probe_session_count", lambda: 3)
    main._BANNER_STATS_CACHE.clear()
    assert main._collect_banner_stats() == {"model": "m", "skills": 1, "tools": 2, "sessions": 3}
    monkeypatch.setattr(main, "_probe_model_name", lambda: "new")
    assert main._collect_banner_stats()["model"] == "m"
    assert main._collect_banner_stats(refresh=True)["model"] == "new"


def test_onboarding_session_store_history_new_and_append_edges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    console = _console()
    env = tmp_path / ".env"
    monkeypatch.setattr(main, "_ENV_PATH", env)
    monkeypatch.setattr(main, "get_console", lambda: console)
    monkeypatch.setattr(main, "run_onboarding", lambda **kwargs: None)
    assert not main._maybe_run_onboarding()
    monkeypatch.setattr(main, "run_onboarding", lambda **kwargs: env)
    assert main._maybe_run_onboarding()
    env.touch()
    assert main._maybe_run_onboarding()

    store = SimpleNamespace(
        get_messages=lambda *args, **kwargs: [
            SimpleNamespace(role="user", content="hello"),
            SimpleNamespace(role="tool", content="ignored"),
            SimpleNamespace(role="assistant", content=" "),
            SimpleNamespace(role="assistant", content="answer"),
        ]
    )
    assert main._build_session_history(store, _SESSION_ID) == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "answer"},
    ]
    store.get_messages = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError())
    assert main._build_session_history(store, _SESSION_ID) == []

    class Store:
        def __init__(self) -> None:
            self.sessions: list[object] = []
            self.messages: list[object] = []

        def create_session(self, session: object) -> None:
            self.sessions.append(session)

        def append_message(self, message: object) -> None:
            self.messages.append(message)

    fake = Store()
    monkeypatch.setattr(main, "_session_store", lambda: fake)
    created = main._new_session("research objective")
    assert created and fake.sessions
    main._append_message(created, "user", "content")
    main._append_message("", "user", "skip")
    main._append_message(created, "user", "")
    assert len(fake.messages) == 1
    monkeypatch.setattr(main, "_session_store", lambda: (_ for _ in ()).throw(RuntimeError()))
    assert main._new_session("fail") is None
    main._append_message(created, "user", "best effort")


def test_resume_prompt_exception_empty_choice_and_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    console = _console()
    monkeypatch.setattr(main, "_session_store", lambda: (_ for _ in ()).throw(RuntimeError()))
    assert main._maybe_resume_last_session(console) is None
    store = SimpleNamespace(list_sessions=lambda limit: [])
    monkeypatch.setattr(main, "_session_store", lambda: store)
    assert main._maybe_resume_last_session(console) is None
    last = SimpleNamespace(session_id=_SESSION_ID, title="")
    store.list_sessions = lambda limit: [last]
    monkeypatch.setattr(builtins, "input", lambda prompt: (_ for _ in ()).throw(EOFError()))
    assert main._maybe_resume_last_session(console) is None
    monkeypatch.setattr(builtins, "input", lambda prompt: "new")
    assert main._maybe_resume_last_session(console) is None
    monkeypatch.setattr(builtins, "input", lambda prompt: "resume")
    monkeypatch.setattr(main, "_build_session_history", lambda *args: [{"role": "user", "content": "old"}])
    resumed = main._maybe_resume_last_session(console)
    assert resumed and resumed["title"] == "(untitled)"


def test_slash_dispatch_empty_unknown_import_exit_and_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import cli.commands.slash_router as router

    console = _console()
    ctx = main.InteractiveContext()
    monkeypatch.setattr(main, "get_console", lambda: console)
    assert main._dispatch_slash("/", ctx) == 0
    monkeypatch.setattr(router, "find_exact", lambda name: None)
    monkeypatch.setattr(main, "_suggest_commands", lambda name: [])
    assert main._dispatch_slash("/missing", ctx) == 0
    monkeypatch.setattr(main, "_suggest_commands", lambda name: ["history"])
    main._dispatch_slash("/historu", ctx)

    cmd = SimpleNamespace(name="help", handler_module="cli.commands.help")
    monkeypatch.setattr(router, "find_exact", lambda name: cmd)
    monkeypatch.setattr(main.importlib, "import_module", lambda name: (_ for _ in ()).throw(ImportError("missing")))
    assert main._dispatch_slash("/help", ctx) == 0

    module = SimpleNamespace(run=lambda *args: 7)
    monkeypatch.setattr(main.importlib, "import_module", lambda name: module)
    assert main._dispatch_slash("/help x", ctx) == 7
    cmd.handler_module = "cli.commands.chat"
    assert main._dispatch_slash("/help x", ctx) == 7
    for code, expected in ((None, 2), (0, 2), (2, 2), (3, 0)):
        module.run = lambda *args, _code=code: (_ for _ in ()).throw(SystemExit(_code))
        assert main._dispatch_slash("/help", ctx) == expected
    module.run = lambda *args: (_ for _ in ()).throw(ValueError("bad"))
    assert main._dispatch_slash("/help", ctx) == 0


def test_tool_summary_debug_recap_and_proposal_render_matrix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    console = _console()
    assert main._summarise_tool_result("x", "error", "line1\nline2") == "line1 line2"
    monkeypatch.setattr(
        "cli._legacy._format_tool_result_preview",
        lambda *args: (_ for _ in ()).throw(RuntimeError()),
    )
    assert main._summarise_tool_result("x", "ok", "preview") == ""
    ctx = main.InteractiveContext(
        history=[{"role": "user", "content": "12345678"}], debug=True
    )
    main._print_debug_summary(
        console,
        {"iterations": 2, "react_trace": [{"type": "tool_result"}, "bad"]},
        1.2,
        ctx,
    )
    main._print_debug_summary(console, {"react_trace": "bad"}, 0.1, ctx)
    main._print_recap_if_needed(console, ctx)
    main._print_recap_if_needed(console, ctx)

    proposal = {
        "intent_normalized": "trade",
        "account": {"type": "paper"},
        "reauth_for": "old",
        "funding_note": "funding",
        "halt_note": "halt",
        "profiles": [
            {
                "ordinal": 1,
                "label": "Conservative",
                "universe": ["AAPL", "MSFT"],
                "max_order_usd": 100,
                "daily_trade_cap": 2,
                "leverage": None,
                "notes": "safe",
            },
            {"ordinal": 2, "label": "Active", "universe": "US", "leverage": 2},
            {"ordinal": 3, "label": "Empty", "leverage": "none"},
        ],
    }
    main._render_mandate_proposal(console, proposal)
    main._render_mandate_proposal(console, {"profiles": []})
    rendered = console.export_text()
    assert "widening" in rendered and "leverage 2" in rendered and "live trading" in rendered


def test_proposal_reply_invalid_error_and_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    console = _console()
    monkeypatch.setattr(main, "get_console", lambda: console)
    proposal = {"profiles": [{"ordinal": 1}, {"ordinal": None}]}
    ctx = main.InteractiveContext(pending_proposal=proposal)
    assert not main._handle_proposal_reply("adjust it", ctx)
    assert main._handle_proposal_reply("2", ctx)
    monkeypatch.setattr(main, "_commit_mandate", lambda *args: {"status": "error", "error": "offline"})
    assert main._handle_proposal_reply("1", ctx) and ctx.pending_proposal is proposal
    monkeypatch.setattr(main, "_commit_mandate", lambda *args: {"mandate_id": "m1"})
    assert main._handle_proposal_reply("1", ctx) and ctx.pending_proposal is None


def test_interactive_loop_import_fallback_resume_and_input_matrix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import cli.input as input_module

    console = _console()
    monkeypatch.setattr(main, "get_console", lambda: console)
    monkeypatch.setattr(main, "_maybe_resume_last_session", lambda console: None)
    monkeypatch.setattr(input_module, "make_session", lambda: object())
    sequence: list[object] = [
        KeyboardInterrupt(),
        EOFError(),
        " ",
        "hello",
        "1",
        "stop",
        "/halt",
        "/resume",
        "/connector status",
        "/journal x",
        "/quit",
    ]

    def get_input(**kwargs: object) -> str:
        value = sequence.pop(0)
        if isinstance(value, BaseException):
            raise value
        return str(value)

    monkeypatch.setattr(input_module, "get_user_input", get_input)
    ctrl = iter([False])
    monkeypatch.setattr(input_module, "ctrl_c_within_window", lambda *args, **kwargs: next(ctrl))
    turns: list[str] = []

    def run_turn(text: str, ctx: main.InteractiveContext) -> None:
        turns.append(text)
        if text == "hello":
            ctx.pending_proposal = {"profiles": [{"ordinal": 1}]}

    monkeypatch.setattr(main, "_run_one_turn", run_turn)
    monkeypatch.setattr(main, "_handle_proposal_reply", lambda text, ctx: setattr(ctx, "pending_proposal", None) or True)
    monkeypatch.setattr(main, "_is_halt_turn", lambda text: text == "stop")
    monkeypatch.setattr(main, "_trip_halt_from_repl", lambda *args, **kwargs: None)
    monkeypatch.setattr(main, "_clear_halt_from_repl", lambda *args, **kwargs: None)
    monkeypatch.setattr(main, "_run_connector_command_from_repl", lambda *args, **kwargs: None)

    def dispatch(text: str, ctx: main.InteractiveContext) -> int:
        if text == "/journal x":
            ctx.pending_prompt = "queued"
        return 2 if text == "/quit" else 0

    monkeypatch.setattr(main, "_dispatch_slash", dispatch)
    assert main._interactive_loop(5) == 0
    assert turns == ["hello", "queued"]

    store = SimpleNamespace(
        get_session=lambda sid: SimpleNamespace(title="", session_id=sid)
    )
    monkeypatch.setattr(main, "_session_store", lambda: store)
    monkeypatch.setattr(main, "_build_session_history", lambda *args: [])
    monkeypatch.setattr(input_module, "get_user_input", lambda **kwargs: (_ for _ in ()).throw(EOFError()))
    monkeypatch.setattr(input_module, "ctrl_c_within_window", lambda *args, **kwargs: True)
    assert main._interactive_loop(5, resume_session_id=_SESSION_ID) == 0
    store.get_session = lambda sid: None
    assert main._interactive_loop(5, resume_session_id=_SESSION_ID) == 1
