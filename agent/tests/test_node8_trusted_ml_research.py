"""Node 8 acceptance contracts for trustworthy ML research."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from src.ml.base_model import FeatureSelectionConfig, LabelConfig, PreprocessConfig, TrainConfig
from src.ml.anti_leakage import run_full_audit
from src.ml.compare import compare_backtest
from src.ml.feature_cache import FeatureCache, LabelCache
from src.ml.features import Preprocessor, preprocess_features
from src.ml.jobs import TrainingJobStore
from src.ml.labels import BenchmarkUnavailableError, build_labels
from src.ml.pipeline import TrainingCancelled, TrainingDataError, run_training_pipeline
from src.ml.scheduler import get_production_version, promote_version
import src.ml.pipeline as pipeline_module


def _feature_frame(values: list[float] | None = None) -> pd.DataFrame:
    dates = pd.date_range("2024-01-01", periods=4, freq="B")
    index = pd.MultiIndex.from_product([dates, ["A", "B"]], names=["date", "code"])
    vals = values or [1, 2, 3, 4, 1000, 2000, 3000, 4000]
    return pd.DataFrame({"alpha": vals, "stable": np.arange(len(index), dtype=float)}, index=index)


def _panel(periods: int = 330) -> dict[str, pd.DataFrame]:
    dates = pd.date_range("2020-01-01", periods=periods, freq="B")
    trend = np.arange(periods, dtype=float)
    close = pd.DataFrame(
        {
            "A": 100 + trend * 0.10 + np.sin(trend / 3),
            "B": 80 + trend * 0.08 + np.cos(trend / 4),
        },
        index=dates,
    )
    return {"close": close}


def _synthetic_feature_matrix(panel: dict[str, pd.DataFrame], **_: object) -> pd.DataFrame:
    close = panel["close"]
    index = pd.MultiIndex.from_product([close.index, close.columns], names=["date", "code"])
    time_code = np.repeat(np.arange(len(close.index), dtype=float), len(close.columns))
    code_code = np.tile(np.arange(len(close.columns), dtype=float), len(close.index))
    return pd.DataFrame({"signal": time_code + code_code, "diverse": (time_code % 7) - code_code}, index=index)


def test_preprocessor_reuses_train_parameters_and_rejects_schema_drift() -> None:
    frame = _feature_frame()
    train, oos = frame.iloc[:4], frame.iloc[4:]
    preprocessor = Preprocessor(PreprocessConfig(winsorize=False, fillna_strategy="median")).fit(train)

    transformed = preprocessor.transform(oos)
    restored = Preprocessor.from_manifest(preprocessor.manifest())

    assert transformed["alpha"].iloc[0] > 100  # train mean, not OOS mean, was used
    pd.testing.assert_frame_equal(transformed, restored.transform(oos))
    assert preprocessor.manifest()["zscore_mean"]["alpha"] == pytest.approx(2.5)
    with pytest.raises(ValueError, match="schema"):
        preprocessor.transform(oos.drop(columns="stable"))


def test_stateless_wrapper_refuses_to_refit_on_an_oos_only_frame() -> None:
    frame = _feature_frame()
    train_dates = frame.index.get_level_values("date").unique()[:2].values
    with pytest.raises(ValueError, match="select no rows"):
        preprocess_features(frame.iloc[4:], PreprocessConfig(), fit_dates=train_dates)


def test_requested_benchmark_failure_is_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    panel = _panel(12)
    monkeypatch.setattr("src.ml.labels._load_benchmark_returns", lambda *_: None)

    with pytest.raises(BenchmarkUnavailableError, match="refusing to silently"):
        build_labels(panel, LabelConfig(horizon=1, benchmark="SPY"))


def test_leakage_sentinel_detects_selection_preprocessing_label_calibration_and_survivorship() -> None:
    dates = pd.date_range("2024-01-01", periods=5, freq="D").values
    panel = {"close": pd.DataFrame({"A": [1, 2, 3, 4, 5]}, index=pd.to_datetime(dates))}
    audit = run_full_audit(
        feature_dates=dates,
        label_dates=dates,
        label_horizon=2,
        train_dates=dates[:3],
        selection_dates=dates,  # directly leaks the final evaluation day
        preprocess_fit_dates=dates,  # directly leaks the final evaluation day
        calibration_dates=dates[2:4],  # overlaps train and evaluation
        test_dates=dates[3:],
        universe="synthetic",
        panel=panel,
        pit_members=None,
    )
    assert not audit["passed"]
    assert {"selection_leakage", "preprocessing_leakage", "label_leakage", "calibration_leakage", "survivorship_bias"} <= set(audit["failed_checks"])


def test_feature_cache_binds_panel_and_pit_fingerprints_and_recovers_corruption(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    panel = _panel(8)
    cache = FeatureCache(tmp_path)
    monkeypatch.setattr(cache, "_compute_content_hash", lambda *_: "factor-v1")
    calls: list[int] = []

    def compute(p: dict[str, pd.DataFrame], *_: object) -> pd.DataFrame:
        calls.append(len(p["close"]))
        return _synthetic_feature_matrix(p)

    pit = {date.strftime("%Y%m%d"): ["A", "B"] for date in panel["close"].index}
    first, hit = cache.get_or_compute(panel, ["signal"], "test", "u", compute, pit)
    second, hit_second = cache.get_or_compute(panel, ["signal"], "test", "u", compute, pit)
    assert not hit and hit_second and first.equals(second)

    cache_file = next((tmp_path / "features").glob("*/*.parquet"))
    cache_file.write_bytes(b"corrupt parquet")
    rebuilt, rebuilt_hit = cache.get_or_compute(panel, ["signal"], "test", "u", compute, pit)
    assert not rebuilt_hit and rebuilt.equals(first) and len(calls) == 2

    changed = {"close": panel["close"].copy()}
    changed["close"].iloc[0, 0] += 1
    _, changed_hit = cache.get_or_compute(changed, ["signal"], "test", "u", compute, pit)
    assert not changed_hit and len(calls) == 3


def test_incremental_feature_compute_includes_rolling_history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    panel = _panel(10)
    cache = FeatureCache(tmp_path, rolling_lookback_bars=3)
    seen_dates: list[pd.Timestamp] = []

    def compute(p: dict[str, pd.DataFrame], *_: object) -> pd.DataFrame:
        seen_dates.extend(p["close"].index.tolist())
        return _synthetic_feature_matrix(p)

    target = [panel["close"].index[-1]]
    result = cache._compute_for_dates(panel, ["signal"], target, compute)
    assert result is not None
    assert seen_dates == panel["close"].index[-4:].tolist()
    assert result.index.get_level_values("date").unique().tolist() == target


def test_feature_cache_reuses_only_a_verified_historical_prefix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache = FeatureCache(tmp_path, rolling_lookback_bars=3)
    monkeypatch.setattr(cache, "_compute_content_hash", lambda *_: "factor-v1")
    initial, extended = _panel(8), _panel(10)
    calls: list[int] = []

    def compute(p: dict[str, pd.DataFrame], *_: object) -> pd.DataFrame:
        calls.append(len(p["close"]))
        return _synthetic_feature_matrix(p)

    cache.get_or_compute(initial, ["signal"], "test", "u", compute)
    _, hit = cache.get_or_compute(extended, ["signal"], "test", "u", compute)
    assert not hit
    assert calls == [8, 5]  # 3 lookback bars + 2 new bars, never an isolated new slice


def test_label_cache_key_includes_every_label_definition_and_data_version(tmp_path: Path) -> None:
    cache = LabelCache(tmp_path)
    base = LabelConfig(horizon=5, label_type="binary", threshold=0.01, quantile_pct=0.2)
    changed = LabelConfig(horizon=5, label_type="binary", threshold=0.02, quantile_pct=0.3)
    assert cache._make_key(base, "u", "data-a", ("a", "b")) != cache._make_key(changed, "u", "data-a", ("a", "b"))
    assert cache._make_key(base, "u", "data-a", ("a", "b")) != cache._make_key(base, "u", "data-b", ("a", "b"))


def test_training_job_store_persists_cancel_and_restart_interruption(tmp_path: Path) -> None:
    path = tmp_path / "jobs.json"
    store = TrainingJobStore(path)
    store.create("job-a", {"period": "2020-2021"})
    store.append_event("job-a", "build_features", rows=42)
    cancelling = store.request_cancel("job-a")
    assert cancelling["status"] == "cancelling"
    assert store.is_cancel_requested("job-a")

    reloaded = TrainingJobStore(path)
    job = reloaded.get("job-a")
    assert job is not None and job["status"] == "interrupted"
    assert job["events"][-1]["stage"] == "interrupted"


def test_research_only_models_cannot_be_promoted_or_selected_as_production(tmp_path: Path) -> None:
    (tmp_path / "research").mkdir()
    (tmp_path / "research" / "metadata.json").write_text(
        json.dumps({"model_id": "research", "production_eligible": False}), encoding="utf-8"
    )
    (tmp_path / "versions.json").write_text(
        json.dumps({"base": [{"model_id": "research", "base_id": "base", "version": "1", "train_window": ["a", "b"]}]}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="research_only"):
        promote_version("research", tmp_path)
    assert get_production_version("base", tmp_path) is None


def test_compare_backtest_reads_canonical_metrics_artifact(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_execute(self, entry_script, run_dir, **kwargs):
        artifacts = run_dir / "artifacts"
        artifacts.mkdir(parents=True)
        (artifacts / "metrics.csv").write_text("sharpe,max_drawdown\n1.25,-0.12\n", encoding="utf-8")
        return SimpleNamespace(success=True, exit_code=0, stderr="")

    monkeypatch.setattr("src.core.runner.Runner.execute", fake_execute)
    result = compare_backtest(model_ids=["model-1"], codes=["A"], start_date="2024-01-01", end_date="2024-01-31")
    assert result.loc["model-1", "sharpe"] == pytest.approx(1.25)
    assert result.loc["model-1", "max_drawdown"] == pytest.approx(-0.12)


def test_training_worker_persists_real_pipeline_stages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import src.api.ml_routes as routes

    store = TrainingJobStore(tmp_path / "jobs.json")
    store.create("job-stage", {"period": "2020-2021"})
    monkeypatch.setattr(routes, "_TRAIN_JOB_STORE", store)

    def fake_pipeline(config, *, progress_callback, should_cancel):
        assert not should_cancel()
        progress_callback("load_data", {"n_rows": 123})
        progress_callback("cv_fold", {"fold": 0, "n_oos": 10})
        return SimpleNamespace(
            model_id="model-stage", model_path=tmp_path / "model-stage", n_features=2,
            n_train_samples=100, cv_summary={"test_ic_mean": 0.1}, overfit_warning=False,
            feature_importance={"signal": 1.0}, wall_seconds=0.5, research_only=False,
            production_eligible=True,
        )

    monkeypatch.setattr("src.ml.pipeline.run_training_pipeline", fake_pipeline)
    response = routes._train_sync("job-stage", routes.TrainRequest(period="2020-2021", model_type="ridge"))
    stages = [event["stage"] for event in store.get("job-stage")["events"]]
    assert response["production_eligible"] is True
    assert {"init", "training", "load_data", "cv_fold", "complete"} <= set(stages)


def test_pipeline_keeps_selection_preprocessing_calibration_and_oos_disjoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    panel = _panel()
    selection_dates: list[set[pd.Timestamp]] = []

    monkeypatch.setattr("src.tools.alpha_bench_tool._load_universe_panel", lambda *_: panel)
    monkeypatch.setattr("src.ml.features.build_feature_matrix", _synthetic_feature_matrix)
    monkeypatch.setattr(
        "src.ml.features.load_pit_universe",
        lambda *_: {date.strftime("%Y%m%d"): ["A", "B"] for date in panel["close"].index},
    )

    def select(features, labels, config, panel=None):
        assert panel is not None
        selected_dates = set(features.index.get_level_values("date"))
        selection_dates.append(selected_dates)
        assert set(labels.index.get_level_values("date")) == selected_dates
        assert set(panel["close"].index) == selected_dates
        return ["signal"], {"fold_local": True}

    monkeypatch.setattr("src.ml.feature_selection.select_features", select)
    config = TrainConfig(
        universe="synthetic",
        period="2020-2021",
        model_type="ridge",
        model_id="safe-model",
        n_splits=1,
        min_train_samples=30,
        use_cache=False,
        calibrate_proba=False,
        selection_config=FeatureSelectionConfig(methods=["corr_dedup"]),
        random_seed=7,
    )

    result = run_training_pipeline(config, models_dir=tmp_path)
    manifest = json.loads((tmp_path / "safe-model" / "model_manifest.json").read_text(encoding="utf-8"))
    partition = manifest["split"]["final_partition"]

    train_dates = set(partition["train_dates"])
    calibration_dates = set(partition["calibration_dates"])
    oos_dates = set(partition["oos_dates"])
    assert selection_dates and selection_dates[-1] == {pd.Timestamp(v) for v in train_dates}
    assert not train_dates & calibration_dates
    assert not train_dates & oos_dates
    assert not calibration_dates & oos_dates
    assert result.production_eligible and not result.research_only
    assert manifest["preprocessing"]["zscore_mean"] is not None


def test_pipeline_marks_missing_pit_as_research_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    panel = _panel()
    monkeypatch.setattr("src.tools.alpha_bench_tool._load_universe_panel", lambda *_: panel)
    monkeypatch.setattr("src.ml.features.build_feature_matrix", _synthetic_feature_matrix)
    monkeypatch.setattr("src.ml.features.load_pit_universe", lambda *_: None)
    config = TrainConfig(
        universe="synthetic",
        period="2020-2021",
        model_type="ridge",
        model_id="research-only-model",
        n_splits=1,
        min_train_samples=30,
        use_cache=False,
        calibrate_proba=False,
    )
    result = run_training_pipeline(config, models_dir=tmp_path)
    assert result.research_only and not result.production_eligible


def test_same_seed_is_reproducible_and_future_oos_changes_do_not_change_training_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    panel = _panel()
    monkeypatch.setattr("src.ml.features.build_feature_matrix", _synthetic_feature_matrix)
    monkeypatch.setattr(
        "src.ml.features.load_pit_universe",
        lambda *_: {date.strftime("%Y%m%d"): ["A", "B"] for date in panel["close"].index},
    )
    active_panel = {"value": panel}
    monkeypatch.setattr("src.tools.alpha_bench_tool._load_universe_panel", lambda *_: active_panel["value"])

    def config(model_id: str) -> TrainConfig:
        return TrainConfig(
            universe="synthetic", period="2020-2021", model_type="ridge", model_id=model_id,
            n_splits=1, min_train_samples=30, use_cache=False, calibrate_proba=False, random_seed=19,
        )

    first = run_training_pipeline(config("repeat-a"), models_dir=tmp_path)
    second = run_training_pipeline(config("repeat-b"), models_dir=tmp_path)
    from src.ml.storage import load_model

    first_model, _ = load_model("repeat-a", tmp_path)
    second_model, _ = load_model("repeat-b", tmp_path)
    probe = np.array([[1.0, 2.0], [3.0, -1.0]])
    np.testing.assert_allclose(first_model.predict(probe), second_model.predict(probe))

    first_manifest = json.loads((tmp_path / first.model_id / "model_manifest.json").read_text(encoding="utf-8"))
    oos_dates = pd.to_datetime(first_manifest["split"]["final_partition"]["oos_dates"])
    future_changed = {"close": panel["close"].copy()}
    future_changed["close"].loc[oos_dates, "A"] *= 1.4
    active_panel["value"] = future_changed
    run_training_pipeline(config("future-shuffled"), models_dir=tmp_path)
    changed_model, _ = load_model("future-shuffled", tmp_path)
    np.testing.assert_allclose(first_model.predict(probe), changed_model.predict(probe))


def test_pipeline_rejects_single_class_labels_and_cancellation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    panel = _panel()
    monkeypatch.setattr("src.tools.alpha_bench_tool._load_universe_panel", lambda *_: panel)
    config = TrainConfig(universe="synthetic", period="2020-2021", model_id="cancelled")
    with pytest.raises(TrainingCancelled, match="cancelled"):
        run_training_pipeline(config, should_cancel=lambda: True, models_dir=tmp_path)

    from src.ml.pipeline import _validate_fold_samples

    binary = TrainConfig(
        universe="synthetic",
        period="2020-2021",
        label_config=LabelConfig(label_type="binary"),
        min_train_samples=2,
    )
    with pytest.raises(TrainingDataError, match="single class"):
        _validate_fold_samples(np.array([1.0, 1.0]), "train", 0, binary)


def test_pipeline_validation_partition_seed_and_metric_edge_contracts() -> None:
    with pytest.raises(ValueError, match="n_splits"):
        pipeline_module._validate_config(TrainConfig(universe="u", period="p", n_splits=0))
    with pytest.raises(ValueError, match="min_train_samples"):
        pipeline_module._validate_config(TrainConfig(universe="u", period="p", min_train_samples=1))
    with pytest.raises(ValueError, match="horizon"):
        pipeline_module._validate_config(TrainConfig(universe="u", period="p", label_config=LabelConfig(horizon=0)))
    assert pipeline_module._seeded_model_params(TrainConfig(universe="u", period="p", model_type="xgboost", random_seed=8))["random_state"] == 8
    assert pipeline_module._seeded_model_params(TrainConfig(universe="u", period="p", model_type="deep_nn", random_seed=9))["seed"] == 9
    dates = pd.date_range("2024-01-01", periods=4).values
    calibration, oos = pipeline_module._split_calibration_and_oos(dates, True)
    assert list(calibration) == [dates[0]] and list(oos) == list(dates[1:])
    no_calibration, all_oos = pipeline_module._split_calibration_and_oos(dates, False)
    assert len(no_calibration) == 0 and list(all_oos) == list(dates)
    with pytest.raises(TrainingDataError, match="at least two"):
        pipeline_module._split_calibration_and_oos(dates[:1], True)
    with pytest.raises(TrainingDataError, match="non-empty close"):
        pipeline_module._validate_panel({})
    with pytest.raises(TrainingDataError, match="NaN or infinity"):
        pipeline_module._reject_nonfinite(np.array([[np.nan]]), "train", 0)
    with pytest.raises(TrainingDataError, match="insufficient"):
        pipeline_module._validate_fold_samples(np.array([1.0]), "OOS", 0, TrainConfig(universe="u", period="p", min_train_samples=2))
    with pytest.raises(TrainingDataError, match="calibration labels"):
        pipeline_module._validate_calibration_labels(np.array([1.0, 1.0]), 0)
    assert pipeline_module._safe_spearman(np.array([1.0]), np.array([1.0])) == 0.0
    classification = pipeline_module._compute_fold_metrics(
        np.array([0.0, 1.0, 0.0, 1.0]), np.array([0.1, 0.9, 0.2, 0.8]),
        np.array([0.0, 1.0, 0.0, 1.0]), np.array([0.2, 0.7, 0.3, 0.8]), "binary",
    )
    assert classification["test_auc"] == pytest.approx(1.0)
    assert pipeline_module._summarize_cv([]) == {}


def test_pipeline_progress_callback_torch_seed_and_classifier_metric_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seed_calls: list[int] = []
    fake_torch = SimpleNamespace(
        manual_seed=lambda seed: seed_calls.append(seed),
        cuda=SimpleNamespace(is_available=lambda: True, manual_seed_all=lambda seed: seed_calls.append(seed)),
    )
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    pipeline_module._seed_everything(23)
    assert seed_calls == [23, 23]

    from sklearn import metrics
    monkeypatch.setattr(metrics, "roc_auc_score", lambda *_: (_ for _ in ()).throw(ValueError("bad score")))
    failed_metric = pipeline_module._compute_fold_metrics(
        np.array([0.0, 1.0, 0.0]), np.array([0.1, 0.8, 0.2]),
        np.array([0.0, 1.0, 0.0]), np.array([0.1, 0.8, 0.2]), "binary",
    )
    assert "test_auc" not in failed_metric

    panel = _panel()
    monkeypatch.setattr("src.tools.alpha_bench_tool._load_universe_panel", lambda *_: panel)
    monkeypatch.setattr("src.ml.features.build_feature_matrix", _synthetic_feature_matrix)
    monkeypatch.setattr(
        "src.ml.features.load_pit_universe",
        lambda *_: {date.strftime("%Y%m%d"): ["A", "B"] for date in panel["close"].index},
    )
    stages: list[str] = []
    run_training_pipeline(
        TrainConfig(universe="synthetic", period="p", model_type="ridge", model_id="progress", n_splits=1,
                    min_train_samples=30, use_cache=False, calibrate_proba=False),
        models_dir=tmp_path,
        progress_callback=lambda stage, _: stages.append(stage),
    )
    assert "save" in stages and "cv_fold" in stages


def test_parameter_search_is_seeded_and_skips_explicit_training_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    base = TrainConfig(universe="u", period="p", model_id="base", min_train_samples=2)
    results = [SimpleNamespace(cv_summary={"test_ic_mean": 0.1}), SimpleNamespace(cv_summary={"test_ic_mean": 0.4})]
    calls: list[dict] = []

    def fake_run(config):
        calls.append(config.model_params)
        if config.model_params["alpha"] == 2:
            raise TrainingDataError("bad data")
        return results.pop(0)

    monkeypatch.setattr(pipeline_module, "run_training_pipeline", fake_run)
    output = pipeline_module.run_param_search(base, {"alpha": [1, 2, 3]})
    assert [item.cv_summary["test_ic_mean"] for item in output] == [0.4, 0.1]
    assert len(calls) == 3
    monkeypatch.setattr(
        pipeline_module,
        "run_training_pipeline",
        lambda config: SimpleNamespace(cv_summary={"test_ic_mean": 0.2}),
    )
    limited = pipeline_module.run_param_search(base, {"alpha": [1, 2, 3]}, max_combinations=1)
    assert len(limited) == 1
