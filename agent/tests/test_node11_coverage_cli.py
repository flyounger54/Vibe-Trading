"""Behavior coverage for the interactive renderer and onboarding workflow."""

from __future__ import annotations

import builtins
from pathlib import Path
from types import SimpleNamespace

import pytest
from rich.console import Console
from rich.text import Text

from cli import onboard, stream


pytestmark = pytest.mark.unit


class _FakeLive:
    instances: list["_FakeLive"] = []

    def __init__(self, renderable, **kwargs) -> None:
        self.renderable = renderable
        self.kwargs = kwargs
        self.started = False
        self.stopped = False
        self.updates: list[object] = []
        self.raise_on_stop = False
        type(self).instances.append(self)

    def start(self, *, refresh: bool) -> None:
        assert refresh is False
        self.started = True

    def stop(self) -> None:
        self.stopped = True
        if self.raise_on_stop:
            raise RuntimeError("render race")

    def update(self, renderable, *, refresh: bool) -> None:
        assert refresh is True
        self.updates.append(renderable)


class _FakeThread:
    def __init__(self, *, target, daemon: bool) -> None:
        self.target = target
        self.daemon = daemon
        self.started = False

    def start(self) -> None:
        self.started = True


def _console() -> Console:
    return Console(record=True, width=120, color_system=None)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("get_market_data", "Market Data"),
        ("run_sql_query", "SQL Query"),
        ("api_url", "API URL"),
        ("", ""),
        ("get_", "get_"),
    ],
)
def test_beautify_tool_name(raw: str, expected: str) -> None:
    assert stream.beautify_tool_name(raw) == expected


def test_argument_summaries_cover_priority_generic_and_truncation() -> None:
    assert stream.summarize_args(None) == ""
    assert stream.summarize_args("abcdef", max_len=4) == "abc…"
    assert stream.summarize_args([1, 2], max_len=20) == "[1, 2]"
    assert stream.summarize_args({"query": "AAPL outlook"}) == '"AAPL outlook"'
    assert stream.summarize_args({"query": "", "symbol": "AAPL"}) == '"AAPL"'
    assert stream.summarize_args({"a": "x", "b": "y"}, max_len=60) == "a=x, b=y"
    assert stream.summarize_args({"long": "x" * 40, "next": "y" * 40}, max_len=10).endswith("…")
    assert stream._truncate("abc", 3) == "abc"
    assert stream._truncate("abc", 1) == "…"


def test_spinner_start_render_pause_resume_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeLive.instances.clear()
    monkeypatch.setattr(stream, "Live", _FakeLive)
    monkeypatch.setattr(stream.threading, "Thread", _FakeThread)
    times = iter((10.0, 11.25, 12.0, 12.25, 12.5, 13.0))
    monkeypatch.setattr(stream.time, "monotonic", lambda: next(times))
    spinner = stream.ThinkingSpinner(_console())
    spinner.start("Researching")
    assert spinner._live is _FakeLive.instances[0]
    assert spinner._tick_thread is not None and spinner._tick_thread.started
    spinner.start("ignored")
    spinner.set_extra("42 tokens")
    assert "Researching" in spinner._render().plain
    assert "42 tokens" in spinner._render().plain

    first = _FakeLive.instances[0]
    with spinner.pause():
        assert spinner._live is None
        assert first.stopped
    assert spinner._live is _FakeLive.instances[1]

    spinner._live.raise_on_stop = True
    spinner.stop()
    assert spinner._live is None


def test_spinner_pause_when_idle_and_tick_update_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    spinner = stream.ThinkingSpinner(_console())
    with spinner.pause():
        pass

    class BrokenLive:
        def update(self, *args, **kwargs):
            spinner._state.stopped = True
            raise RuntimeError("terminal closed")

    spinner._live = BrokenLive()  # type: ignore[assignment]
    spinner._state.stopped = False
    spinner._state.paused = False
    monkeypatch.setattr(stream.time, "sleep", lambda seconds: None)
    spinner._tick()
    assert spinner._state.stopped


def test_stream_renderer_turn_tool_answer_and_footer(monkeypatch: pytest.MonkeyPatch) -> None:
    console = _console()
    renderer = stream.StreamRenderer(console=console)
    now = iter((100.0, 100.25, 100.5))
    monkeypatch.setattr(stream.time, "monotonic", lambda: next(now))

    class FakeSpinner:
        def __init__(self, target_console) -> None:
            self.console = target_console
            self.started: list[str | None] = []
            self.stopped = False

        def start(self, verb=None) -> None:
            self.started.append(verb)

        def stop(self) -> None:
            self.stopped = True

        def pause(self):
            from contextlib import nullcontext

            return nullcontext()

    monkeypatch.setattr(stream, "ThinkingSpinner", FakeSpinner)
    with renderer.turn(verb="Analyzing") as active:
        assert active is renderer
        renderer.on_tool_start("get_market_data", {"symbol": "AAPL"})
        renderer.on_tool_end("get_market_data", summary="250 rows")
        renderer.on_tool_end("run_sql", summary="cache hit")
        renderer.print_answer("First line\nSecond line")
    assert renderer._spinner is None
    assert renderer._active_calls == {}

    renderer.print_footer(run_id="run-1", tool_count=1, duration_ms=1250, token_count=1200, cost=0.003)
    renderer.print_footer(run_id="run-2", tool_count=2, duration_ms=None, token_count=None, cost=None)
    output = console.export_text()
    assert "Market Data" in output
    assert "250 rows" in output
    assert "First line" in output and "Second line" in output
    assert "/show run-1" in output and "1 tool call" in output
    assert "/show run-2" in output and "2 tool calls" in output


def test_stream_renderer_swarm_and_validation() -> None:
    console = _console()
    with pytest.raises(ValueError, match="unknown"):
        stream.StreamRenderer(mode="invalid", console=console)
    renderer = stream.StreamRenderer(mode="swarm", console=console)
    assert renderer.mode == "swarm"
    with renderer.turn() as active:
        assert active is renderer
    renderer._emit_static(Text("plain"))
    assert "plain" in console.export_text()


def test_onboarding_render_validation_and_numeric_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    assert onboard._render_env({"B": "2", "EMPTY": "", "A": "1"}) == "B=2\nA=1\n"
    openai = next(provider for provider in onboard.PROVIDERS if provider.key == "openai")
    ollama = next(provider for provider in onboard.PROVIDERS if provider.key == "ollama")
    assert onboard._validate_key(openai, "") == "API key cannot be empty."
    assert "start" in (onboard._validate_key(openai, "wrong-key-value") or "")
    assert "short" in (onboard._validate_key(ollama, "tiny") or "")
    assert onboard._validate_key(openai, "sk-valid-value") is None

    console = _console()
    answers = iter(("", "b", "q", "2", "bad", "1"))
    monkeypatch.setattr(builtins, "input", lambda prompt: next(answers))
    choices = (("one", "One"), ("two", "Two"))
    assert onboard._select_numeric(choices, 1, console) == "two"
    assert onboard._select_numeric(choices, 0, console) is onboard.BACK
    assert onboard._select_numeric(choices, 0, console) is onboard.CANCEL
    assert onboard._select_numeric(choices, 0, console) == "two"
    assert onboard._select_numeric(choices, 0, console) == "one"


def test_onboarding_numeric_selection_cancel_on_input_error(monkeypatch: pytest.MonkeyPatch) -> None:
    console = _console()
    monkeypatch.setattr(builtins, "input", lambda prompt: (_ for _ in ()).throw(EOFError()))
    assert onboard._select_numeric((("one", "One"),), 0, console) is onboard.CANCEL


def test_onboarding_partial_and_finalize_are_private_and_atomic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_dir = tmp_path / "config"
    monkeypatch.setattr(onboard, "_env_dir", lambda: env_dir)
    monkeypatch.setattr(onboard, "_env_path", lambda: env_dir / ".env")
    monkeypatch.setattr(onboard, "_partial_path", lambda: env_dir / ".env.partial")
    onboard._save_partial({"SECRET": "value"})
    assert (env_dir / ".env.partial").read_text() == "SECRET=value\n"
    assert (env_dir / ".env.partial").stat().st_mode & 0o777 == 0o600
    final = onboard._finalize({"SECRET": "final"})
    assert final == env_dir / ".env"
    assert final.read_text() == "SECRET=final\n"
    assert not (env_dir / ".env.partial").exists()


def test_onboarding_save_partial_is_best_effort(monkeypatch: pytest.MonkeyPatch) -> None:
    broken = SimpleNamespace(mkdir=lambda **kwargs: (_ for _ in ()).throw(OSError("readonly")))
    monkeypatch.setattr(onboard, "_env_dir", lambda: broken)
    onboard._save_partial({"A": "1"})


def _run_wizard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    selections: list[object],
    secrets: list[object],
    texts: list[object] | None = None,
) -> Path | None:
    env_dir = tmp_path / "wizard"
    monkeypatch.setattr(onboard, "_env_dir", lambda: env_dir)
    monkeypatch.setattr(onboard, "_env_path", lambda: env_dir / ".env")
    monkeypatch.setattr(onboard, "_partial_path", lambda: env_dir / ".env.partial")
    selection_iter = iter(selections)
    secret_iter = iter(secrets)
    text_iter = iter(texts or [])
    monkeypatch.setattr(onboard, "_select_with_back", lambda *args, **kwargs: next(selection_iter))
    monkeypatch.setattr(onboard, "_prompt_secret", lambda *args, **kwargs: next(secret_iter))
    monkeypatch.setattr(onboard, "_prompt_text", lambda *args, **kwargs: next(text_iter))
    return onboard.run_onboarding(console=_console())


def test_onboarding_openai_custom_model_key_retry_tushare_and_tour(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    result = _run_wizard(
        tmp_path,
        monkeypatch,
        selections=["openai", "__custom__", "600", "__paste__", "__tour__"],
        texts=["gpt-custom"],
        secrets=["bad", "sk-valid-secret", "tushare-secret"],
    )
    assert result is not None
    content = result.read_text()
    assert "LANGCHAIN_MODEL_NAME=gpt-custom" in content
    assert "OPENAI_API_KEY=sk-valid-secret" in content
    assert "TUSHARE_TOKEN=tushare-secret" in content


def test_onboarding_ollama_requires_no_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    result = _run_wizard(
        tmp_path,
        monkeypatch,
        selections=["ollama", "qwen2.5:32b", "2400", "__skip__", "__skip__"],
        secrets=[],
    )
    assert result is not None
    assert "OLLAMA_BASE_URL=http://localhost:11434" in result.read_text()


def test_onboarding_cancel_and_back_navigation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert _run_wizard(tmp_path, monkeypatch, selections=[onboard.CANCEL], secrets=[]) is None
    assert _run_wizard(tmp_path, monkeypatch, selections=[onboard.BACK], secrets=[]) is None

    result = _run_wizard(
        tmp_path,
        monkeypatch,
        selections=["openai", onboard.BACK, "ollama", "qwen2.5:32b", "2400", "__skip__", "__skip__"],
        secrets=[],
    )
    assert result is not None
