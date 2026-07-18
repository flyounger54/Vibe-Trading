"""Deterministic behavior coverage for the ML lifecycle and signal engine."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from src.ml import ensemble, experiment, feature_profile, feature_selection, monitoring
from src.ml.base_model import FeatureSelectionConfig, LabelConfig, PreprocessConfig, TrainConfig
from src.strategies.zoo.multi_factor import ml_predictor


pytestmark = pytest.mark.unit


class _Model:
    def __init__(self, values=(0.2, 0.8), *, proba=True, importance=None) -> None:
        self.values = np.asarray(values, dtype=float)
        self.with_proba = proba
        self.importance = importance

    def predict(self, X):
        return np.resize(self.values, len(X))

    def predict_proba(self, X):
        return np.resize(self.values, len(X)) if self.with_proba else None

    def get_feature_importance(self):
        return self.importance


def _train_config() -> TrainConfig:
    return TrainConfig(
        universe="csi300",
        period="2025-01-01:2025-12-31",
        feature_profile_id="profile-1",
        zoo="qlib158",
        selection_config=FeatureSelectionConfig(methods=["corr_dedup"], min_icir=0.2, max_corr=0.9),
        label_config=LabelConfig(horizon=5, label_type="rank", threshold=0.1, benchmark="000300.SH", cost_bps=5),
        preprocess_config=PreprocessConfig(winsorize=False, zscore=True, fillna_strategy="zero"),
        n_splits=3,
        expanding=False,
        gap_days=2,
        model_type="ridge",
        model_params={"alpha": 2.0},
        pit_universe=False,
        calibrate_proba=False,
        random_seed=7,
        min_train_samples=30,
    )


def test_experiment_round_trip_listing_defaults_and_corruption(tmp_path: Path) -> None:
    config = _train_config()
    path = experiment.save_experiment(config, "quality", schedule={"cron": "0 8 * * 1"}, experiments_dir=tmp_path)
    assert path.exists()
    loaded = experiment.load_experiment("quality", tmp_path)
    assert loaded.universe == "csi300"
    assert loaded.label_config.horizon == 5
    assert loaded.label_config.benchmark == "000300.SH"
    assert loaded.model_params == {"alpha": 2.0}
    assert loaded.selection_config is not None and loaded.selection_config.max_corr == 0.9
    assert loaded.gap_days == 2

    assert experiment.load_experiment("quality.yaml", tmp_path).period == config.period
    rows = experiment.list_experiments(tmp_path)
    assert rows == [{
        "name": "quality",
        "universe": "csi300",
        "model_type": "ridge",
        "horizon": 5,
        "label_type": "rank",
        "has_schedule": True,
        "path": str(path),
    }]
    (tmp_path / "broken.yaml").write_text("not: [valid", encoding="utf-8")
    assert experiment.list_experiments(tmp_path) == rows
    assert experiment.list_experiments(tmp_path / "missing") == []
    with pytest.raises(FileNotFoundError):
        experiment.load_experiment("absent", tmp_path)

    defaults = experiment._parse_experiment({"universe": "sp500", "period": "2026"})
    assert defaults.label_config.horizon == 1
    assert defaults.selection_config is None
    assert defaults.model_type == "lightgbm"


def test_ensemble_weights_predictions_importance_and_persistence(tmp_path: Path) -> None:
    predictor = ensemble.EnsemblePredictor(ensemble.EnsembleConfig(["a", "b"], weights=[1, 3]))
    predictor._sub_models = [
        _Model((1, 2), importance={"f1": 1.0}),
        _Model((3, 4), importance={"f1": 0.5, "f2": 2.0}),
    ]
    predictor._weights = predictor._compute_weights()
    predictor._feature_names = ["f1", "f2"]
    X = np.ones((2, 2))
    assert np.allclose(predictor.predict(X), [2.5, 3.5])
    assert np.allclose(predictor.predict_proba(X), [2.5, 3.5])
    assert predictor.get_feature_importance() == {"f1": 0.625, "f2": 1.5}
    assert predictor.get_params()["weights"] == [0.25, 0.75]
    predictor.calibrate(X, np.array([0, 1]))

    predictor.save(tmp_path)
    restored = ensemble.EnsemblePredictor.load(tmp_path)
    assert restored.get_params()["weights"] == [0.25, 0.75]
    assert restored._feature_names == ["f1", "f2"]


def test_ensemble_loading_weight_modes_stacking_and_empty_probabilities(monkeypatch: pytest.MonkeyPatch) -> None:
    metadata = {
        "a": {"factor_ids": ["f"], "cv_summary": {"test_ic_mean": -0.2}},
        "b": {"factor_ids": ["f"], "cv_summary": {"test_ic_mean": 0.6}},
    }
    monkeypatch.setattr("src.ml.storage.load_model", lambda mid, base: (_Model(), metadata[mid]))
    weighted = ensemble.EnsemblePredictor(ensemble.EnsembleConfig(["a", "b"], method="ic_weighted"))
    weighted._ensure_loaded()
    assert np.allclose(weighted._weights, [0.25, 0.75])
    weighted._ensure_loaded()

    average = ensemble.EnsemblePredictor(ensemble.EnsembleConfig(["a", "b"]))
    average._sub_models = [_Model(proba=False), _Model(proba=False)]
    average._weights = average._compute_weights()
    assert average.predict_proba(np.ones((2, 1))) is None
    assert average.get_feature_importance() is None

    stacking = ensemble.EnsemblePredictor(ensemble.EnsembleConfig(["a", "b"], method="stacking"))
    stacking._sub_models = [_Model((0, 1, 2, 3)), _Model((1, 0, 3, 2))]
    stacking._weights = np.array([0.5, 0.5])
    X = np.ones((4, 1))
    stacking.fit(X, np.array([0.0, 1.0, 2.0, 3.0]))
    assert stacking._meta_learner is not None
    assert len(stacking.predict(X)) == 4
    assert stacking.predict_proba(X) is None

    empty = ensemble.EnsemblePredictor(ensemble.EnsembleConfig([]))
    with pytest.raises(RuntimeError, match="No sub-models"):
        empty._ensure_loaded()


def test_create_ensemble_eligibility_schema_and_save(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    saved: list[tuple] = []

    def populate(self) -> None:
        self._sub_models = [_Model(), _Model()]
        self._feature_names = ["f1"]
        self._weights = np.array([0.5, 0.5])
        self._sub_metadata = [
            {"model_id": "a", "production_eligible": True, "factor_ids": ["f1"], "preprocessing": {"v": 1}, "cv_summary": {"test_ic_mean": 0.1}},
            {"model_id": "b", "production_eligible": True, "factor_ids": ["f1"], "preprocessing": {"v": 1}, "cv_summary": {"test_ic_mean": 0.2}},
        ]

    monkeypatch.setattr(ensemble.EnsemblePredictor, "_ensure_loaded", populate)
    monkeypatch.setattr("src.ml.storage.save_model", lambda *args: saved.append(args))
    assert ensemble.create_ensemble(ensemble.EnsembleConfig(["a", "b"], method="stacking"), "ens", tmp_path) == "ens"
    assert saved and saved[0][2]["sub_model_ics"] == [0.1, 0.2]

    def ineligible(self) -> None:
        populate(self)
        self._sub_metadata[1]["production_eligible"] = False

    monkeypatch.setattr(ensemble.EnsemblePredictor, "_ensure_loaded", ineligible)
    with pytest.raises(ValueError, match="research-only"):
        ensemble.create_ensemble(ensemble.EnsembleConfig(["a", "b"]), "bad", tmp_path)

    def mismatch(self) -> None:
        populate(self)
        self._sub_metadata[1]["factor_ids"] = ["other"]

    monkeypatch.setattr(ensemble.EnsemblePredictor, "_ensure_loaded", mismatch)
    with pytest.raises(ValueError, match="identical"):
        ensemble.create_ensemble(ensemble.EnsembleConfig(["a", "b"]), "bad-schema", tmp_path)


def _selection_frame() -> tuple[pd.DataFrame, pd.Series]:
    index = pd.MultiIndex.from_product(
        [pd.date_range("2026-01-01", periods=6), ["A", "B"]], names=["date", "code"]
    )
    base = np.arange(len(index), dtype=float)
    features = pd.DataFrame({"a": base, "b": base * 1.01, "c": base[::-1]}, index=index)
    labels = pd.Series((base > 5).astype(float), index=index)
    return features, labels


def test_feature_selection_pipeline_and_individual_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    features, labels = _selection_frame()
    kept, report = feature_selection.select_features(
        features,
        labels,
        FeatureSelectionConfig(methods=["ic_filter", "corr_dedup", "unknown"], max_corr=0.9),
        panel=None,
    )
    assert kept == ["a"]
    assert report["n_final"] == 1

    no_close, no_close_report = feature_selection.filter_by_ic(features, labels, {})
    assert no_close == ["a", "b", "c"] and "error" in no_close_report

    ic_values = iter((pd.Series(dtype=float), pd.Series([1.0, 1.0]), pd.Series([0.1, 0.2, 0.4])))
    monkeypatch.setattr("src.factors.factor_analysis_core.compute_ic_series", lambda *args: next(ic_values))
    close = pd.DataFrame({"A": range(6), "B": range(6)}, index=pd.date_range("2026-01-01", periods=6))
    ic_kept, ic_report = feature_selection.filter_by_ic(features, labels, {"close": close}, min_icir=0.1)
    assert ic_kept == ["c"]
    assert set(ic_report["ic_stats"]) == {"b", "c"}

    monkeypatch.setattr("sklearn.feature_selection.mutual_info_classif", lambda X, y, random_state: np.array([0.1, 0.8, 0.4]))
    mi_kept, _ = feature_selection.filter_by_mutual_info(features, labels, top_n=2, min_score=0.2)
    assert mi_kept == ["b", "c"]

    regression_labels = pd.Series(np.linspace(0, 1, len(labels)), index=labels.index)
    monkeypatch.setattr("sklearn.feature_selection.mutual_info_regression", lambda X, y, random_state: np.array([0.3, 0.2, 0.1]))
    mi_reg, _ = feature_selection.filter_by_mutual_info(features, regression_labels, min_score=0.25)
    assert mi_reg == ["a"]


def test_feature_selection_importance_boruta_and_shap(monkeypatch: pytest.MonkeyPatch) -> None:
    features, labels = _selection_frame()

    class FakeForest:
        def __init__(self, **kwargs) -> None:
            self.feature_importances_ = np.array([0.5, 0.3, 0.2, 0.1, 0.1, 0.1])

        def fit(self, X, y) -> None:
            pass

    monkeypatch.setattr("sklearn.ensemble.RandomForestClassifier", FakeForest)
    monkeypatch.setattr("sklearn.ensemble.RandomForestRegressor", FakeForest)
    kept, report = feature_selection.filter_by_boruta(features, labels, max_iter=2, alpha=0.9)
    assert kept == ["a", "b", "c"]
    assert report["threshold"] >= 0

    class FakeBoost:
        def __init__(self, **kwargs) -> None:
            self.feature_importances_ = np.array([0.8, 0.2, 0.0])

        def fit(self, X, y) -> None:
            pass

    monkeypatch.setattr("sklearn.ensemble.GradientBoostingRegressor", FakeBoost)
    imp_kept, _ = feature_selection.filter_by_importance(features, labels, model_type="fallback", min_pct=0.1)
    assert imp_kept == ["a", "b"]

    class FakeExplainer:
        def __init__(self, model) -> None:
            pass

        def shap_values(self, X):
            return np.tile(np.array([0.7, 0.2, 0.1]), (len(X), 1))

    monkeypatch.setitem(sys.modules, "shap", SimpleNamespace(TreeExplainer=FakeExplainer))
    shap_kept, _ = feature_selection.filter_by_shap(features, labels, model_type="fallback", top_n=2, min_pct=0.15)
    assert shap_kept == ["a", "b"]


def _profile(profile_id: str = "profile") -> feature_profile.FeatureProfile:
    return feature_profile.FeatureProfile(
        profile_id=profile_id,
        zoo="qlib158",
        universe="csi300",
        selection_period="2025",
        methods=["corr_dedup"],
        selected_factor_ids=["a", "b"],
        preprocess_config=PreprocessConfig(),
        selection_report={"ok": True},
        created_at="2026-01-01T00:00:00+00:00",
    )


def test_feature_profile_persistence_listing_refresh_and_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    profile = _profile()
    feature_profile._save_profile(profile, tmp_path)
    assert feature_profile.load_feature_profile("profile", tmp_path) == profile
    rows = feature_profile.list_feature_profiles(tmp_path)
    assert rows[0]["n_factors"] == 2
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "profile.json").write_text("bad-json", encoding="utf-8")
    assert feature_profile.list_feature_profiles(tmp_path) == rows
    assert feature_profile.list_feature_profiles(tmp_path / "absent") == []
    with pytest.raises(FileNotFoundError):
        feature_profile.load_feature_profile("absent", tmp_path)

    monkeypatch.setattr(feature_profile, "create_feature_profile", lambda **kwargs: _profile(kwargs["profile_id"]))
    refreshed = feature_profile.refresh_feature_profile("profile", "2026", tmp_path)
    assert refreshed.profile_id == "profile_v2"


def test_create_feature_profile_orchestrates_selection(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    index = pd.MultiIndex.from_product([[pd.Timestamp("2026-01-01")], ["A"]], names=["date", "code"])
    features = pd.DataFrame({"f1": [1.0]}, index=index)
    panel = {"close": pd.DataFrame({"A": [1.0]}, index=[pd.Timestamp("2026-01-01")])}
    monkeypatch.setattr("src.tools.alpha_bench_tool._load_universe_panel", lambda *args: panel)
    monkeypatch.setattr("src.factors.registry.get_default_registry", lambda: SimpleNamespace(list=lambda zoo: ["f1"]))
    monkeypatch.setattr("src.ml.features.build_feature_matrix", lambda *args, **kwargs: features)
    monkeypatch.setattr("src.ml.features.preprocess_features", lambda frame, config: (frame, {}))
    monkeypatch.setattr("src.ml.labels.build_labels", lambda *args: pd.Series([0.1], index=index))
    monkeypatch.setattr("src.ml.feature_selection.select_features", lambda *args, **kwargs: (["f1"], {"selected": 1}))
    created = feature_profile.create_feature_profile("csi300", "2026", profiles_dir=tmp_path)
    assert created.selected_factor_ids == ["f1"]
    assert feature_profile.load_feature_profile(created.profile_id, tmp_path).profile_id == created.profile_id


def test_monitoring_health_success_failure_and_manifest_guard(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    metadata = {
        "cv_summary": {"test_ic_mean": 0.8},
        "universe": "csi300",
        "factor_ids": ["f"],
        "label_config": {"horizon": 1},
        "preprocessing": {"columns": ["f"]},
    }
    index = pd.RangeIndex(12)
    frame = pd.DataFrame({"f": np.arange(12.0)}, index=index)
    monkeypatch.setattr("src.ml.storage.load_model", lambda *args: (_Model(np.arange(12.0)), metadata))
    monkeypatch.setattr("src.ml.features.Preprocessor.from_manifest", lambda manifest: SimpleNamespace(transform=lambda value: value))
    monkeypatch.setattr("src.tools.alpha_bench_tool._load_universe_panel", lambda *args: {"close": frame})
    monkeypatch.setattr("src.ml.features.build_feature_matrix", lambda *args, **kwargs: frame)
    monkeypatch.setattr("src.ml.labels.build_labels", lambda *args: pd.Series(np.arange(12.0), index=index))
    healthy = monitoring.evaluate_model_health("model", "2026", models_dir=tmp_path)
    assert healthy.recent_ic_mean == pytest.approx(1.0)
    assert not healthy.retrain_recommended

    monkeypatch.setattr("src.ml.features.build_feature_matrix", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("data")))
    failed = monitoring.evaluate_model_health("model", "2026", models_dir=tmp_path)
    assert failed.retrain_recommended and failed.details["error"] == "data"

    monkeypatch.setattr("src.ml.storage.load_model", lambda *args: (_Model(), {**metadata, "preprocessing": None}))
    with pytest.raises(ValueError, match="preprocessing manifest"):
        monitoring.evaluate_model_health("unsafe", "2026", models_dir=tmp_path)


def test_monitoring_all_models_and_version_drift(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("src.ml.storage.list_models", lambda root: [
        {"model_id": "ensemble", "model_type": "ensemble"},
        {"model_id": "good", "model_type": "ridge"},
        {"model_id": "bad", "model_type": "ridge"},
    ])
    monkeypatch.setattr(
        monitoring,
        "evaluate_model_health",
        lambda mid, *args: monitoring.HealthReport(mid, 1, 0, 0.2 if mid == "good" else (_ for _ in ()).throw(RuntimeError("bad")), False, "now", {}),
    )
    reports = monitoring.check_all_models_health("2026")
    assert [row.model_id for row in reports] == ["good"]

    monkeypatch.setattr("src.ml.compare.compare_versions", lambda *args: pd.DataFrame())
    assert monitoring.compare_version_drift("m")["status"] == "insufficient_versions"
    monkeypatch.setattr("src.ml.compare.compare_versions", lambda *args: pd.DataFrame({"test_ic_mean": [0.8, 0.6, 0.4]}))
    assert monitoring.compare_version_drift("m")["status"] == "declining"
    monkeypatch.setattr("src.ml.compare.compare_versions", lambda *args: pd.DataFrame({"test_ic_mean": [0.8, 0.2]}))
    assert monitoring.compare_version_drift("m")["status"] == "sudden_drop"
    monkeypatch.setattr("src.ml.compare.compare_versions", lambda *args: pd.DataFrame({"test_ic_mean": [0.2, 0.3]}))
    assert monitoring.compare_version_drift("m")["status"] == "healthy"


def _market_data() -> dict[str, pd.DataFrame]:
    dates = pd.date_range("2026-01-01", periods=4)
    return {
        "A": pd.DataFrame({"open": [1, 2, 3, 4], "close": [2, 3, 4, 5], "volume": [10] * 4}, index=dates),
        "B": pd.DataFrame({"open": [4, 3, 2, 1], "close": [5, 4, 3, 2], "amount": [20] * 4}, index=dates),
    }


def test_ml_signal_helpers_and_ranking(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    data = _market_data()
    unconfigured = ml_predictor.SignalEngine()
    assert all((series == 0).all() for series in unconfigured.generate(data).values())
    panel = ml_predictor._data_map_to_panel(data)
    assert set(panel) == {"open", "close", "volume", "amount"}
    dates = ml_predictor._get_all_dates(data)
    assert len(dates) == 4
    assert len(ml_predictor._slice_panel(panel, dates[:2])["close"]) == 2

    signal_df = pd.DataFrame({"A": [1, 0, -1, 1], "B": [-1, 1, 0, -1]}, index=dates)
    held = ml_predictor._apply_rebalance(signal_df, 2)
    assert held.iloc[1].equals(held.iloc[0]) and held.iloc[3].equals(held.iloc[2])

    engine = ml_predictor.SignalEngine(model_id="m", top_pct=0.5, bottom_pct=0.5, rebalance_days=2)
    predictions = {"A": pd.Series([0.8, np.nan, 0.2, 0.9], index=dates), "B": pd.Series([0.2, 0.7, 0.8, 0.1], index=dates)}
    signals = engine._predictions_to_signals(predictions, data)
    assert signals["A"].iloc[0] == 1.0 and signals["B"].iloc[0] == -1.0

    probability_engine = ml_predictor.SignalEngine(model_id="m", use_proba=True, top_pct=0.5, bottom_pct=0.5)
    probability_signals = probability_engine._predictions_to_signals(predictions, data)
    assert probability_signals["A"].iloc[0] == 0.8
    assert all((series == 0).all() for series in engine._predictions_to_signals({}, data).values())

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert ml_predictor._load_schedule_versions("missing") == []
    versions = tmp_path / ".vibe-trading" / "models"
    versions.mkdir(parents=True)
    (versions / "versions.json").write_text(json.dumps({"weekly": [{"model_id": "m"}]}), encoding="utf-8")
    assert ml_predictor._load_schedule_versions("weekly")[0]["model_id"] == "m"
    (versions / "versions.json").write_text("bad", encoding="utf-8")
    assert ml_predictor._load_schedule_versions("weekly") == []


def test_ml_predict_with_model_fail_closed_and_success(monkeypatch: pytest.MonkeyPatch) -> None:
    index = pd.MultiIndex.from_product([pd.date_range("2026-01-01", periods=2), ["A", "B"]], names=["date", "code"])
    features = pd.DataFrame({"f": [1.0, 2.0, 3.0, 4.0]}, index=index)
    monkeypatch.setattr("src.ml.features.build_feature_matrix", lambda *args, **kwargs: features)
    monkeypatch.setattr("src.ml.features.Preprocessor.from_manifest", lambda manifest: SimpleNamespace(transform=lambda frame: frame))
    result = ml_predictor._predict_with_model(_Model((0.1, 0.2, 0.3, 0.4)), {"preprocessing": {"ok": True}}, {}, ["f"])
    assert result is not None and set(result) == {"A", "B"}

    monkeypatch.setattr("src.ml.features.build_feature_matrix", lambda *args, **kwargs: pd.DataFrame())
    assert ml_predictor._predict_with_model(_Model(), {"preprocessing": {}}, {}, []) is None
    monkeypatch.setattr("src.ml.features.build_feature_matrix", lambda *args, **kwargs: features)
    assert ml_predictor._predict_with_model(_Model(), {}, {}, ["f"]) is None
    monkeypatch.setattr("src.ml.features.Preprocessor.from_manifest", lambda manifest: (_ for _ in ()).throw(ValueError("manifest")))
    assert ml_predictor._predict_with_model(_Model(), {"preprocessing": {}}, {}, ["f"]) is None
    monkeypatch.setattr("src.ml.features.build_feature_matrix", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("factors")))
    assert ml_predictor._predict_with_model(_Model(), {"preprocessing": {}}, {}, ["f"]) is None


def test_ml_fixed_and_rolling_signal_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    data = _market_data()
    dates = next(iter(data.values())).index
    predictions = {"A": pd.Series([0.8] * 4, index=dates), "B": pd.Series([0.2] * 4, index=dates)}
    monkeypatch.setattr("src.ml.storage.load_model", lambda model_id: (_Model(), {"factor_ids": ["f"]}))
    monkeypatch.setattr(ml_predictor, "_predict_with_model", lambda *args: predictions)
    fixed = ml_predictor.SignalEngine(model_id="fixed", top_pct=0.5)
    assert fixed.generate(data)["A"].sum() == 4

    monkeypatch.setattr(ml_predictor, "_load_schedule_versions", lambda name: [
        {"model_id": "m1", "train_window": ["2025", "2026-01-01"]},
        {"model_id": "m2", "train_window": ["2025", "2026-01-03"]},
    ])
    rolling = ml_predictor.SignalEngine(schedule_name="weekly", top_pct=0.5)
    assert rolling.generate(data)["A"].sum() == 4
    monkeypatch.setattr(ml_predictor, "_load_schedule_versions", lambda name: [])
    assert rolling.generate(data)["A"].sum() == 0
