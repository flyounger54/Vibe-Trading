"""Model comparison: CV metrics table + backtest comparison + version drift."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_MODELS_DIR = Path.home() / ".vibe-trading" / "models"


def compare_models(
    model_ids: list[str],
    models_dir: Path | None = None,
) -> pd.DataFrame:
    """Compare multiple models by their CV metrics side-by-side.

    Returns DataFrame: index=model_id, columns=metric names.
    """
    base = models_dir or _MODELS_DIR
    rows = []

    for mid in model_ids:
        meta_path = base / mid / "metadata.json"
        if not meta_path.exists():
            rows.append({"model_id": mid, "_error": "not found"})
            continue

        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        cv = meta.get("cv_summary", {})
        label_cfg = meta.get("label_config", {})

        row: dict[str, Any] = {
            "model_id": mid,
            "model_type": meta.get("model_type", "?"),
            "horizon": label_cfg.get("horizon", "?"),
            "label_type": label_cfg.get("label_type", "?"),
            "benchmark": label_cfg.get("benchmark", ""),
            "cost_bps": label_cfg.get("cost_bps", 0),
            "n_features": meta.get("n_features", 0),
            "n_samples": meta.get("n_train_samples", 0),
            "overfit_warning": meta.get("overfit_warning", False),
        }

        for key in ["test_ic_mean", "train_ic_mean", "overfit_ratio_mean",
                     "test_auc_mean", "test_accuracy_mean", "test_f1_mean"]:
            row[key] = cv.get(key)

        rows.append(row)

    df = pd.DataFrame(rows).set_index("model_id")
    return df


def compare_backtest(
    model_ids: list[str] | None = None,
    schedule_names: list[str] | None = None,
    codes: list[str] | None = None,
    start_date: str = "",
    end_date: str = "",
    models_dir: Path | None = None,
) -> pd.DataFrame:
    """Run same-condition backtests for multiple models/schedules and compare.

    Returns DataFrame with Sharpe, max drawdown, annual return, win rate per model.
    """
    from src.core.runner import Runner

    results = []
    agent_root = Path(__file__).resolve().parents[2]
    entry_script = agent_root / "backtest" / "runner.py"

    all_entries: list[tuple[str, dict]] = []

    for mid in (model_ids or []):
        all_entries.append((mid, {"model_id": mid}))

    for sname in (schedule_names or []):
        all_entries.append((f"schedule:{sname}", {"schedule_name": sname}))

    for label, params in all_entries:
        import tempfile

        run_dir = Path(tempfile.mkdtemp(prefix="vt_compare_"))
        _write_backtest_config(run_dir, codes or [], start_date, end_date, params)
        _write_signal_engine(run_dir, params)

        try:
            runner = Runner(timeout=600)
            result = runner.execute(entry_script, run_dir, cwd=agent_root, cli_args=[str(run_dir)])

            if not result.success:
                metrics = {
                    "error": result.stderr[-500:] if result.stderr else "backtest runner failed",
                    "exit_code": result.exit_code,
                }
            else:
                metrics = _read_backtest_metrics(run_dir)

            results.append({"model": label, **metrics})
        except Exception as exc:
            results.append({"model": label, "error": str(exc)})

    if not results:
        return pd.DataFrame()

    return pd.DataFrame(results).set_index("model")


def _read_backtest_metrics(run_dir: Path) -> dict[str, Any]:
    """Read the canonical runner artifact, not an obsolete top-level JSON path."""
    metrics_path = run_dir / "artifacts" / "metrics.csv"
    if not metrics_path.exists():
        return {"error": f"missing required backtest artifact: {metrics_path}"}
    try:
        frame = pd.read_csv(metrics_path)
        if frame.empty:
            return {"error": "backtest metrics artifact is empty"}
        raw = frame.iloc[0].dropna().to_dict()
        metrics: dict[str, Any] = {}
        for key, value in raw.items():
            if isinstance(value, np.generic):
                value = value.item()
            metrics[str(key)] = value
        return metrics
    except (OSError, ValueError, pd.errors.ParserError) as exc:
        return {"error": f"invalid backtest metrics artifact: {exc}"}


def compare_versions(
    base_id: str,
    models_dir: Path | None = None,
) -> pd.DataFrame:
    """Compare different versions of the same model over time.

    Returns DataFrame: index=version, columns=(ic_mean, auc_mean, overfit_ratio, ...).
    Useful for detecting alpha decay across retraining cycles.
    """
    base = models_dir or _MODELS_DIR
    rows = []

    for model_dir in sorted(base.iterdir()):
        if not model_dir.name.startswith(base_id):
            continue
        meta_path = model_dir / "metadata.json"
        if not meta_path.exists():
            continue

        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        cv = meta.get("cv_summary", {})
        train_window = meta.get("period", "")

        rows.append({
            "version": model_dir.name,
            "train_period": train_window,
            "test_ic_mean": cv.get("test_ic_mean"),
            "test_auc_mean": cv.get("test_auc_mean"),
            "overfit_ratio_mean": cv.get("overfit_ratio_mean"),
            "n_features": meta.get("n_features", 0),
            "created_at": meta.get("created_at", ""),
        })

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.set_index("version")
    return df


def _write_backtest_config(
    run_dir: Path,
    codes: list[str],
    start_date: str,
    end_date: str,
    model_params: dict,
) -> None:
    """Write config.json for a comparison backtest run."""
    config = {
        "codes": codes,
        "start_date": start_date,
        "end_date": end_date,
        "source": "auto",
        "interval": "1D",
        "engine": "daily",
    }
    (run_dir / "config.json").write_text(
        json.dumps(config, indent=2), encoding="utf-8"
    )


def _write_signal_engine(run_dir: Path, params: dict) -> None:
    """Write signal_engine.py shim for ml_predictor."""
    code_dir = run_dir / "code"
    code_dir.mkdir(parents=True, exist_ok=True)

    model_id = params.get("model_id", "")
    schedule_name = params.get("schedule_name", "")

    code = f'''"""Auto-generated signal engine for ML predictor comparison."""
from src.strategies.zoo.multi_factor.ml_predictor import SignalEngine as _Base

class SignalEngine(_Base):
    def __init__(self, **params):
        super().__init__(
            model_id={model_id!r},
            schedule_name={schedule_name!r},
            **params,
        )
'''
    (code_dir / "signal_engine.py").write_text(code, encoding="utf-8")
