"""Tests for swarm auto model tiering (Phase 3 of deep fusion)."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

from src.swarm.models import (
    SwarmAgentSpec,
    SwarmRun,
    SwarmTask,
    WorkerResult,
)
from src.swarm.runtime import SwarmRuntime
from src.swarm.task_store import TaskStore


def _spec(agent_id: str = "analyst", model_name: str | None = None) -> SwarmAgentSpec:
    return SwarmAgentSpec(
        id=agent_id,
        role="Test Agent",
        system_prompt="Test",
        model_name=model_name,
        timeout_seconds=60,
        max_retries=0,
    )


def test_terminal_node_gets_deep_model(tmp_path, monkeypatch):
    """Terminal task (no downstream) gets SWARM_DEEP_MODEL when model_name is None."""
    monkeypatch.setenv("SWARM_DEEP_MODEL", "deepseek/deepseek-v4-pro")
    monkeypatch.setenv("SWARM_QUICK_MODEL", "deepseek/deepseek-v4-flash")
    monkeypatch.setenv("SWARM_QUALITY_SCORING", "off")
    monkeypatch.setenv("SWARM_CROSS_VALIDATION", "off")

    store = MagicMock()
    runtime = SwarmRuntime(store=store, max_workers=1)

    analyst = _spec("analyst")
    synthesizer = _spec("synthesizer")

    task_a = SwarmTask(id="t-a", agent_id="analyst", prompt_template="Analyze")
    task_synth = SwarmTask(
        id="t-synth", agent_id="synthesizer", prompt_template="Synthesize",
        depends_on=["t-a"], input_from={"analysis": "t-a"},
    )

    run_dir = tmp_path / "run"
    task_store = TaskStore(run_dir)
    task_store.save_task(task_a)
    task_store.save_task(task_synth)

    run = SwarmRun(
        id="run1",
        preset_name="test",
        created_at="2026-06-24T00:00:00+00:00",
        agents=[analyst, synthesizer],
        tasks=[task_a, task_synth],
    )

    seen_models: list[str | None] = []

    def _fake_run(**kwargs):
        spec = kwargs["agent_spec"]
        seen_models.append(spec.model_name)
        return WorkerResult(status="completed", summary="done")

    monkeypatch.setattr(runtime, "_run_and_quality_check", _fake_run)

    from src.swarm.models import TaskStatus
    from src.swarm.task_store import resolve_dependencies

    # Run layer 0 (analyst - leaf/non-terminal, has downstream)
    results_l0 = runtime._execute_layer(
        run=run, task_store=task_store,
        agent_map={a.id: a for a in [analyst, synthesizer]},
        layer_task_ids=["t-a"], task_summaries={}, task_quality={},
        run_dir=run_dir, cancel_event=threading.Event(),
    )

    # Mark t-a as completed so t-synth is unblocked
    task_store.update_status("t-a", TaskStatus.completed, summary="analysis done")
    resolve_dependencies(run_dir / "tasks", "t-a")

    # Run layer 1 (synthesizer - terminal, no downstream)
    results_l1 = runtime._execute_layer(
        run=run, task_store=task_store,
        agent_map={a.id: a for a in [analyst, synthesizer]},
        layer_task_ids=["t-synth"],
        task_summaries={"t-a": "analysis done"},
        task_quality={},
        run_dir=run_dir, cancel_event=threading.Event(),
    )

    assert seen_models[0] == "deepseek/deepseek-v4-flash"
    assert seen_models[1] == "deepseek/deepseek-v4-pro"


def test_explicit_model_name_not_overridden(tmp_path, monkeypatch):
    """Agent with explicit model_name keeps it even when tier env vars are set."""
    monkeypatch.setenv("SWARM_DEEP_MODEL", "deepseek/deepseek-v4-pro")
    monkeypatch.setenv("SWARM_QUICK_MODEL", "deepseek/deepseek-v4-flash")
    monkeypatch.setenv("SWARM_QUALITY_SCORING", "off")
    monkeypatch.setenv("SWARM_CROSS_VALIDATION", "off")

    store = MagicMock()
    runtime = SwarmRuntime(store=store, max_workers=1)

    agent = _spec("analyst", model_name="openai/gpt-4o")
    task = SwarmTask(id="t1", agent_id="analyst", prompt_template="Analyze")

    run_dir = tmp_path / "run"
    task_store = TaskStore(run_dir)
    task_store.save_task(task)

    run = SwarmRun(
        id="run1", preset_name="test",
        created_at="2026-06-24T00:00:00+00:00",
        agents=[agent], tasks=[task],
    )

    seen_models: list[str | None] = []

    def _fake_run(**kwargs):
        seen_models.append(kwargs["agent_spec"].model_name)
        return WorkerResult(status="completed", summary="done")

    monkeypatch.setattr(runtime, "_run_and_quality_check", _fake_run)

    runtime._execute_layer(
        run=run, task_store=task_store,
        agent_map={"analyst": agent},
        layer_task_ids=["t1"], task_summaries={}, task_quality={},
        run_dir=run_dir, cancel_event=threading.Event(),
    )

    assert seen_models[0] == "openai/gpt-4o"


def test_no_env_vars_no_tiering(tmp_path, monkeypatch):
    """Without SWARM_QUICK/DEEP_MODEL, model_name stays None."""
    monkeypatch.delenv("SWARM_DEEP_MODEL", raising=False)
    monkeypatch.delenv("SWARM_QUICK_MODEL", raising=False)
    monkeypatch.setenv("SWARM_QUALITY_SCORING", "off")
    monkeypatch.setenv("SWARM_CROSS_VALIDATION", "off")

    store = MagicMock()
    runtime = SwarmRuntime(store=store, max_workers=1)

    agent = _spec("analyst")
    task = SwarmTask(id="t1", agent_id="analyst", prompt_template="Analyze")

    run_dir = tmp_path / "run"
    task_store = TaskStore(run_dir)
    task_store.save_task(task)

    run = SwarmRun(
        id="run1", preset_name="test",
        created_at="2026-06-24T00:00:00+00:00",
        agents=[agent], tasks=[task],
    )

    seen_models: list[str | None] = []

    def _fake_run(**kwargs):
        seen_models.append(kwargs["agent_spec"].model_name)
        return WorkerResult(status="completed", summary="done")

    monkeypatch.setattr(runtime, "_run_and_quality_check", _fake_run)

    runtime._execute_layer(
        run=run, task_store=task_store,
        agent_map={"analyst": agent},
        layer_task_ids=["t1"], task_summaries={}, task_quality={},
        run_dir=run_dir, cancel_event=threading.Event(),
    )

    assert seen_models[0] is None
