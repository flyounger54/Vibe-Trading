"""Command-handler contracts for Alpha Zoo terminal workflows."""

from __future__ import annotations

import argparse
import builtins
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.factors import cli_handlers as cli


pytestmark = pytest.mark.unit


class _Registry:
    def __init__(self, ids=None, *, fail=False):
        self.ids = ["a1", "a2", "b1"] if ids is None else ids
        self.fail = fail
        self._py_paths = {}

    def list(self, zoo=None, theme=None, universe=None):
        if self.fail:
            raise RuntimeError("registry failed")
        if zoo == "a":
            return [value for value in self.ids if value.startswith("a")]
        if zoo == "b":
            return [value for value in self.ids if value.startswith("b")]
        return list(self.ids)

    def get(self, alpha_id):
        if alpha_id == "missing":
            raise KeyError(alpha_id)
        return SimpleNamespace(
            id=alpha_id,
            zoo="a" if alpha_id.startswith("a") else "b",
            module_path="tests.fake_alpha",
            meta={
                "theme": ["momentum"],
                "universe": ["csi300"],
                "decay_horizon": 5,
                "nickname": "Fast",
                "columns_required": ["close"],
                "formula_latex": "x<y",
                "notes": "note",
            },
        )

    def health(self):
        return {"errors": [{"alpha_id": "bad", "reason": "broken"}]}

    def export_manifest(self):
        return {"health": {"loaded": len(self.ids), "failed": 0}, "alphas": self.ids}


def _args(**kwargs):
    defaults = {
        "verbose": False,
        "zoo": None,
        "theme": None,
        "universe": "csi300",
        "period": "2020-2021",
        "limit": 50,
        "json": False,
        "include_load_errors": False,
        "show_failed": False,
        "brief": False,
        "top": 2,
        "yes": False,
        "compare_all": False,
        "alpha_ids": [],
        "sort": "ir",
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def test_print_hint_tty_and_exception_help(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    real_stdout_is_tty = cli._stdout_is_tty
    monkeypatch.setattr(cli, "_console", None)
    monkeypatch.setattr(cli, "_stderr_console", None)
    cli._print("out")
    cli._err("err")
    monkeypatch.setattr(cli, "_stdout_is_tty", lambda: False)
    cli._hint("hidden")
    monkeypatch.setattr(cli, "_stdout_is_tty", lambda: True)
    cli._hint("shown")
    assert "out" in capsys.readouterr().out
    code = cli._handle_exception(_args(), "failed", RuntimeError("TUSHARE_TOKEN missing"))
    assert code == 1
    assert "How to fix" in capsys.readouterr().err

    class BadStdout:
        def isatty(self):
            raise RuntimeError("closed")

    monkeypatch.setattr(cli.sys, "stdout", BadStdout())
    assert not real_stdout_is_tty()


def test_list_json_empty_truncated_and_load_errors(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    registry = _Registry()
    monkeypatch.setattr(cli, "Registry", lambda: registry)
    monkeypatch.setattr(cli, "_console", None)
    assert cli.cmd_alpha_list(_args(json=True, limit=2, include_load_errors=True)) == 0
    out = capsys.readouterr().out
    assert len(json.loads(out.split("2 load", 1)[0] if "2 load" in out else out.split("1 load", 1)[0])) == 2

    assert cli.cmd_alpha_list(_args(limit=2)) == 0
    assert "showing 2 of 3" in capsys.readouterr().out
    monkeypatch.setattr(cli, "Registry", lambda: _Registry([]))
    assert cli.cmd_alpha_list(_args(include_load_errors=True)) == 0
    assert "no alphas" in capsys.readouterr().out
    monkeypatch.setattr(cli, "Registry", lambda: _Registry(fail=True))
    assert cli.cmd_alpha_list(_args()) == 1
    assert cli._include_load_errors(_args(show_failed=True))


def test_print_load_error_empty_and_nonempty(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setattr(cli, "_console", None)
    cli._print_load_errors(SimpleNamespace(health=lambda: {"errors": []}))
    assert "no load errors" in capsys.readouterr().out
    cli._print_load_errors(_Registry())
    assert "bad: broken" in capsys.readouterr().out


def test_show_brief_missing_source_and_file_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    registry = _Registry()
    monkeypatch.setattr(cli, "Registry", lambda: registry)
    monkeypatch.setattr(cli, "_console", None)
    assert cli.cmd_alpha_show(_args(alpha_id="missing")) == 1
    assert "not found" in capsys.readouterr().err
    assert cli.cmd_alpha_show(_args(alpha_id="a1", brief=True)) == 0
    assert "formula_latex" in capsys.readouterr().out

    source = tmp_path / "alpha.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    registry._py_paths["a1"] = source
    assert cli.cmd_alpha_show(_args(alpha_id="a1")) == 0
    assert "VALUE = 1" in capsys.readouterr().out
    registry._py_paths["a1"] = tmp_path / "missing.py"
    assert cli.cmd_alpha_show(_args(alpha_id="a1")) == 0
    assert "could not load source" in capsys.readouterr().err


def test_confirmation_and_zoo_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(builtins, "input", lambda prompt: " YES ")
    assert cli._confirm_bench_all(3)
    monkeypatch.setattr(builtins, "input", lambda prompt: "no")
    assert not cli._confirm_bench_all(3)
    monkeypatch.setattr(
        builtins,
        "input",
        lambda prompt: (_ for _ in ()).throw(EOFError()),
    )
    assert not cli._confirm_bench_all(3)
    assert cli._zoo_for_id(_Registry(), "a1") == "a"
    assert cli._zoo_for_id(_Registry(), None) == ""
    assert cli._zoo_for_id(_Registry(), "missing") == ""


def test_progress_helpers_plain_aggregate_success_and_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_console", None)
    monkeypatch.setattr(cli, "Progress", None)
    registry = _Registry()
    calls = []

    def run_bench(**kwargs):
        calls.append(kwargs)
        if kwargs["zoo"] == "b":
            return {"status": "error", "error": "failed"}
        return {"status": "ok", "rows": [{"id": "a1", "ir": 1}], "skipped": [{"id": "a2"}]}

    single = cli._run_single_zoo_with_progress(
        zoo="a", universe="csi300", period="2020-2021", top=2,
        n_target=2, reg=registry, run_bench=run_bench,
    )
    assert single["status"] == "ok"
    result = cli._run_all_zoos_with_progress(
        target_ids=registry.ids, universe="csi300", period="2020-2021", top=2,
        start_ts=0, reg=registry, run_bench=run_bench,
    )
    assert result["status"] == "ok" and result["n_skipped"] == 2
    empty = cli._run_all_zoos_with_progress(
        target_ids=["b1"], universe="csi300", period="2020-2021", top=2,
        start_ts=0, reg=registry, run_bench=run_bench,
    )
    assert empty["status"] == "error" and "no alphas" in empty["error"]


def test_bench_command_abort_empty_error_and_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    from src.factors import bench_runner
    from src.tools import alpha_bench_tool

    monkeypatch.setattr(cli, "_console", None)
    monkeypatch.setattr(cli, "_stderr_console", None)
    monkeypatch.setattr(cli, "Registry", lambda: _Registry())
    monkeypatch.setattr(cli, "_confirm_bench_all", lambda total: False)
    assert cli.cmd_alpha_bench(_args(zoo=None)) == 1

    monkeypatch.setattr(cli, "Registry", lambda: _Registry([]))
    assert cli.cmd_alpha_bench(_args(zoo="empty")) == 1
    monkeypatch.setattr(cli, "Registry", lambda: _Registry())
    monkeypatch.setattr(
        bench_runner,
        "run_bench",
        lambda **kwargs: {"status": "error", "error": "TUSHARE_TOKEN missing"},
    )
    assert cli.cmd_alpha_bench(_args(zoo="a")) == 1
    assert "How to fix" in capsys.readouterr().err

    monkeypatch.setattr(
        bench_runner,
        "run_bench",
        lambda **kwargs: {
            "status": "ok",
            "rows": [
                {"id": "a1", "ir": 1.0, "ic_mean": 0.1},
                {"id": "a2", "ir": 2.0, "_category": "momentum"},
            ],
            "skipped": [{"id": "bad", "reason": "skip"}],
            "wall_seconds": 1,
        },
    )
    monkeypatch.setattr(alpha_bench_tool, "_default_output_dir", lambda: tmp_path)
    monkeypatch.setattr(alpha_bench_tool, "_render_html", lambda context: "<html></html>")
    assert cli.cmd_alpha_bench(_args(zoo="a", top=1)) == 0
    assert '"status": "ok"' in capsys.readouterr().out
    assert list(tmp_path.glob("alpha_bench_*.html"))


def test_compare_validation_error_and_success(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setattr(cli, "Registry", lambda: _Registry())
    monkeypatch.setattr(cli, "_stderr_console", None)
    monkeypatch.setattr(cli, "_render_compare_table", lambda ranking, sort: None)
    assert cli.cmd_alpha_compare(_args(alpha_ids=[])) == 1
    assert cli.cmd_alpha_compare(_args(alpha_ids=["a1"])) == 1
    monkeypatch.setattr(
        cli,
        "compare_alphas",
        lambda *args, **kwargs: {"status": "error", "error": "bad"},
    )
    assert cli.cmd_alpha_compare(_args(alpha_ids=["a1", "a2", "a1"])) == 1
    monkeypatch.setattr(
        cli,
        "compare_alphas",
        lambda *args, **kwargs: {
            "status": "ok",
            "ranking": [{"id": "a2", "ir": 2.0}],
            "n_compared": 2,
            "winner": "a2",
            "n_skipped": 1,
        },
    )
    assert cli.cmd_alpha_compare(_args(alpha_ids=["a1", "a2"])) == 0
    assert "winner: a2" in capsys.readouterr().err


def test_export_manifest_boundary_success_and_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.setattr(cli, "_console", None)
    monkeypatch.setattr(cli, "_REPO_ROOT", tmp_path / "repo")
    monkeypatch.setattr(cli, "Registry", lambda: _Registry())
    outside = tmp_path / "outside.json"
    assert cli.cmd_alpha_export_manifest(_args(out=str(outside), force=False)) == 1
    assert cli.cmd_alpha_export_manifest(_args(out=str(outside), force=True)) == 0
    assert json.loads(outside.read_text(encoding="utf-8"))["health"]["loaded"] == 3


def test_parser_and_dispatch(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    parser = argparse.ArgumentParser()
    alpha = cli.add_subparser(parser.add_subparsers(dest="command"))
    assert alpha.prog.endswith("alpha")
    assert cli.dispatch(_args(alpha_command=None)) == 1
    assert "usage:" in capsys.readouterr().out
    assert cli.dispatch(_args(alpha_command="unknown")) == 1
    monkeypatch.setitem(cli._DISPATCH, "fake", lambda args: 7)
    assert cli.dispatch(_args(alpha_command="fake")) == 7
