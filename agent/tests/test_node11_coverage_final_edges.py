"""Final release-threshold edge cases with deterministic branch assertions."""

from __future__ import annotations

import importlib
from datetime import datetime, timedelta, timezone

import pytest
from rich.console import Console

from cli import _legacy as legacy


main = importlib.import_module("cli.main")
pytestmark = pytest.mark.unit


def _console() -> Console:
    return Console(record=True, width=100, color_system=None)


def test_expiry_countdown_unparseable_naive_expired_days_hours_and_minutes() -> None:
    assert "unparseable" in legacy._format_expiry_countdown("invalid")
    assert "EXPIRED" in legacy._format_expiry_countdown("2000-01-01T00:00:00")
    assert "d " in legacy._format_expiry_countdown("2099-01-02T00:00:00Z")
    now = datetime.now(timezone.utc)
    hours = (now + timedelta(hours=2, minutes=5)).isoformat()
    minutes = (now + timedelta(minutes=10)).isoformat()
    assert "h " in legacy._format_expiry_countdown(hours)
    assert "m)" in legacy._format_expiry_countdown(minutes)


def test_last_tick_datetime_naive_future_seconds_minutes_and_hours() -> None:
    now = datetime.now(timezone.utc)
    assert legacy._format_last_tick("invalid") == "invalid"
    assert "T" in legacy._format_last_tick((now - timedelta(seconds=10)).replace(tzinfo=None))
    assert "s ago" in legacy._format_last_tick(now - timedelta(seconds=10))
    assert "m ago" in legacy._format_last_tick(now - timedelta(minutes=10))
    assert "h ago" in legacy._format_last_tick(now - timedelta(hours=3))
    future = now + timedelta(hours=1)
    assert legacy._format_last_tick(future) == future.isoformat()


def test_interactive_result_empty_content_run_id_and_recap_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    console = _console()
    main._print_interactive_result(console, {}, 0.1)
    main._print_interactive_result(console, {"content": "answer", "run_id": "r1"}, 1.2)
    ctx = main.InteractiveContext(history=[{"role": "user", "content": "x"}])
    monkeypatch.setattr("cli.ui.transcript.render_recap", lambda history: None)
    main._print_recap_if_needed(console, ctx)
    rendered = console.export_text()
    assert "answer" in rendered and "/show r1" in rendered


def test_main_interactive_cancel_and_success_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main, "_is_interactive_invocation", lambda argv: True)
    monkeypatch.setattr(main, "_maybe_run_onboarding", lambda: False)
    assert main.main([]) == 0
    called: list[int] = []
    monkeypatch.setattr(main, "_maybe_run_onboarding", lambda: True)
    monkeypatch.setattr(main, "_show_banner", lambda: None)
    monkeypatch.setattr(main, "_interactive_loop", lambda max_iter: called.append(max_iter) or 7)
    assert main.main(["chat", "--max-iter=9"]) == 7
    assert called == [9]
