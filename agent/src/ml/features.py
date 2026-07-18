"""Feature engineering: Alpha Zoo factors → ML feature matrix + preprocessing."""

from __future__ import annotations

import logging
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src.ml.base_model import PreprocessConfig

logger = logging.getLogger(__name__)


class Preprocessor:
    """A fitted, serialisable feature preprocessor.

    Feature transformations are model parameters: quantiles, moments and
    imputation values must be estimated from *training rows only* and then
    reused verbatim for calibration, OOS evaluation and live inference.  The
    old stateless helper made it far too easy to accidentally refit on an OOS
    frame, so this object deliberately separates :meth:`fit` and
    :meth:`transform`.
    """

    def __init__(self, config: PreprocessConfig) -> None:
        self.config = config
        self._columns: list[str] = []
        self._lower: pd.Series | None = None
        self._upper: pd.Series | None = None
        self._mean: pd.Series | None = None
        self._std: pd.Series | None = None
        self._fill_values: pd.Series | None = None
        self._fitted = False

    @property
    def fitted(self) -> bool:
        return self._fitted

    def fit(self, training_features: pd.DataFrame) -> Preprocessor:
        """Fit all learned values on a non-empty training frame only."""
        if training_features.empty:
            raise ValueError("Cannot fit preprocessor on an empty training feature frame")
        if training_features.columns.has_duplicates:
            raise ValueError("Feature columns must be unique before preprocessing")

        self._columns = list(training_features.columns)
        fit_data = training_features[self._columns].copy()

        if self.config.winsorize:
            lo, hi = self.config.winsorize_limits
            if not 0.0 <= lo <= hi <= 1.0:
                raise ValueError("winsorize_limits must satisfy 0 <= lower <= upper <= 1")
            self._lower = fit_data.quantile(lo)
            self._upper = fit_data.quantile(hi)
            fit_data = fit_data.clip(lower=self._lower, upper=self._upper, axis=1)

        if self.config.zscore:
            self._mean = fit_data.mean()
            self._std = fit_data.std().replace(0, 1.0).fillna(1.0)

        if self.config.fillna_strategy == "median":
            # Median is intentionally based on the raw training distribution,
            # matching the values presented to the clip/z-score transform.
            self._fill_values = fit_data.median()
        elif self.config.fillna_strategy not in {"zero", "ffill", "none"}:
            raise ValueError(f"Unknown fillna_strategy: {self.config.fillna_strategy!r}")

        self._fitted = True
        return self

    def transform(self, features: pd.DataFrame) -> pd.DataFrame:
        """Apply fitted parameters without inspecting OOS distribution values."""
        if not self._fitted:
            raise RuntimeError("Preprocessor must be fitted before transform")
        missing = [c for c in self._columns if c not in features.columns]
        extra = [c for c in features.columns if c not in self._columns]
        if missing or extra:
            raise ValueError(
                "Feature schema differs from fitted preprocessor "
                f"(missing={missing}, extra={extra})"
            )

        result = features.loc[:, self._columns].copy()
        if self._lower is not None and self._upper is not None:
            result = result.clip(lower=self._lower, upper=self._upper, axis=1)
        if self._mean is not None and self._std is not None:
            result = (result - self._mean) / self._std

        if self.config.fillna_strategy == "median" and self._fill_values is not None:
            result = result.fillna(self._fill_values)
        elif self.config.fillna_strategy == "zero":
            result = result.fillna(0.0)
        elif self.config.fillna_strategy == "ffill":
            # Group-wise forward fill never reads a future row.  We do not
            # bridge a train/test boundary implicitly; callers can carry an
            # explicit, audited state if they need that behaviour.
            result = result.groupby(level="code").ffill()
        return result

    def manifest(self) -> dict[str, Any]:
        """Return JSON-safe fitted values for the model provenance manifest."""
        if not self._fitted:
            raise RuntimeError("Preprocessor must be fitted before serialisation")
        return {
            "config": asdict(self.config),
            "columns": self._columns,
            "winsorize_lower": self._lower.to_dict() if self._lower is not None else None,
            "winsorize_upper": self._upper.to_dict() if self._upper is not None else None,
            "zscore_mean": self._mean.to_dict() if self._mean is not None else None,
            "zscore_std": self._std.to_dict() if self._std is not None else None,
            "fill_values": self._fill_values.to_dict() if self._fill_values is not None else None,
        }

    @classmethod
    def from_manifest(cls, manifest: dict[str, Any]) -> Preprocessor:
        """Restore exact training parameters for monitoring/live inference."""
        raw_config = dict(manifest.get("config") or {})
        if "winsorize_limits" in raw_config:
            raw_config["winsorize_limits"] = tuple(raw_config["winsorize_limits"])
        instance = cls(PreprocessConfig(**raw_config))
        columns = manifest.get("columns")
        if not isinstance(columns, list) or not columns:
            raise ValueError("Preprocessing manifest is missing its feature schema")
        instance._columns = list(columns)
        instance._lower = _series_or_none(manifest.get("winsorize_lower"), instance._columns)
        instance._upper = _series_or_none(manifest.get("winsorize_upper"), instance._columns)
        instance._mean = _series_or_none(manifest.get("zscore_mean"), instance._columns)
        instance._std = _series_or_none(manifest.get("zscore_std"), instance._columns)
        instance._fill_values = _series_or_none(manifest.get("fill_values"), instance._columns)
        instance._fitted = True
        return instance


def _series_or_none(value: Any, columns: list[str]) -> pd.Series | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("Invalid preprocessing parameter in manifest")
    missing = [column for column in columns if column not in value]
    if missing:
        raise ValueError(f"Preprocessing manifest is missing parameters for {missing}")
    return pd.Series({column: value[column] for column in columns}, dtype=float)


def load_pit_universe(
    universe: str,
    period: str,
    cache_dir: Path | None = None,
) -> dict[str, list[str]] | None:
    """Load point-in-time index constituents per date.

    Returns {date_str: [ts_code, ...]} or None if PIT data is unavailable.
    Includes delisted stocks on dates they were still active constituents.
    """
    # Lazy imports to avoid circular deps and optional-dep issues at import time
    import re

    _PERIOD_YEAR = re.compile(r"^(\d{4})-(\d{4})$")
    _PERIOD_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2})/(\d{4}-\d{2}-\d{2})$")

    m = _PERIOD_DATE.match(period)
    if m:
        start, end = m.group(1), m.group(2)
    else:
        m = _PERIOD_YEAR.match(period)
        if m:
            start, end = f"{m.group(1)}-01-01", f"{m.group(2)}-12-31"
        else:
            return None

    if universe == "csi300":
        return _load_pit_csi300(start, end, cache_dir)

    logger.info("PIT universe not implemented for %s, falling back to current constituents", universe)
    return None


def _load_pit_csi300(
    start: str, end: str, cache_dir: Path | None
) -> dict[str, list[str]] | None:
    """Load CSI300 historical constituents via Tushare index_weight."""
    import os

    token = os.environ.get("TUSHARE_TOKEN", "")
    if not token:
        logger.warning("TUSHARE_TOKEN not set, cannot load PIT constituents")
        return None

    try:
        import tushare as ts

        pro = ts.pro_api(token)
    except ImportError:
        logger.warning("tushare not installed, PIT unavailable")
        return None

    try:
        df = pro.index_weight(
            index_code="399300.SZ",
            start_date=start.replace("-", ""),
            end_date=end.replace("-", ""),
        )
        if df is None or df.empty:
            return None
    except Exception as exc:
        logger.warning("Failed to fetch index_weight: %s", exc)
        return None

    pit: dict[str, list[str]] = {}
    for trade_date, group in df.groupby("trade_date"):
        date_str = str(trade_date)
        pit[date_str] = group["con_code"].tolist()
    return pit


def build_feature_matrix(
    panel: dict[str, pd.DataFrame],
    factor_ids: list[str] | None = None,
    zoo: str | None = None,
    pit_members: dict[str, list[str]] | None = None,
) -> pd.DataFrame:
    """Compute alpha-zoo factors and reshape into ML-ready stacked rows.

    Args:
        panel: Wide panel {field: DataFrame(date×codes)} from data loaders.
        factor_ids: Explicit alpha IDs. If None, uses all factors in zoo.
        zoo: Zoo name used when factor_ids is None.
        pit_members: Point-in-time constituents {date: [codes]}.
            When provided, each date only retains that date's actual members.

    Returns:
        DataFrame with MultiIndex(date, code), one column per factor.
    """
    from src.factors.registry import RegistryError, SkipAlpha, get_default_registry

    registry = get_default_registry()

    if factor_ids is None:
        if zoo is None:
            zoo = "qlib158"
        factor_ids = registry.list(zoo=zoo)

    computed: dict[str, pd.Series] = {}
    skipped: list[str] = []

    for fid in factor_ids:
        try:
            factor_df = registry.compute(fid, panel)
            try:
                stacked = factor_df.stack(dropna=False)
            except (ValueError, TypeError):
                stacked = factor_df.stack(future_stack=True)
            stacked.index.names = ["date", "code"]
            computed[fid] = stacked
        except (SkipAlpha, RegistryError, KeyError) as exc:
            skipped.append(fid)
            logger.debug("Factor %s skipped: %s", fid, exc)

    if not computed:
        raise RuntimeError(
            f"No factors computed successfully out of {len(factor_ids)} requested. "
            f"Check panel columns: {list(panel.keys())}"
        )

    if skipped:
        logger.info(
            "Skipped %d/%d factors: %s",
            len(skipped), len(factor_ids),
            skipped[:5] if len(skipped) > 5 else skipped,
        )

    features = pd.DataFrame(computed)

    if pit_members:
        features = _apply_pit_mask(features, pit_members)

    return features


def _apply_pit_mask(
    features: pd.DataFrame, pit_members: dict[str, list[str]]
) -> pd.DataFrame:
    """Mask out rows where the stock was not a constituent on that date."""
    dates = features.index.get_level_values("date")
    codes = features.index.get_level_values("code")

    pit_lookup: dict[str, set[str]] = {
        d: set(members) for d, members in pit_members.items()
    }

    mask = pd.Series(False, index=features.index)
    for date_val in dates.unique():
        date_key = date_val.strftime("%Y%m%d") if hasattr(date_val, "strftime") else str(date_val)
        members = pit_lookup.get(date_key)
        if members is None:
            date_key_alt = str(date_val)[:10].replace("-", "")
            members = pit_lookup.get(date_key_alt)
        if members is not None:
            date_mask = dates == date_val
            code_mask = codes.isin(members)
            mask = mask | (date_mask & code_mask)
        else:
            mask = mask | (dates == date_val)

    return features.loc[mask]


def preprocess_features(
    features: pd.DataFrame,
    config: PreprocessConfig,
    fit_dates: np.ndarray | None = None,
    preprocessor: Preprocessor | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Backward-compatible wrapper around :class:`Preprocessor`.

    New training code should use ``Preprocessor.fit(train).transform(test)``
    directly.  Passing a fitted object is the only supported way to transform
    a separate OOS frame; ``fit_dates`` that select no rows is rejected rather
    than silently fitting on the test set.
    """
    if preprocessor is not None:
        if preprocessor.config != config:
            raise ValueError("Provided preprocessor was fitted with a different config")
        return preprocessor.transform(features), preprocessor.manifest()

    if fit_dates is None:
        fit_data = features
    else:
        dates = features.index.get_level_values("date")
        fit_data = features.loc[dates.isin(fit_dates)]
        if fit_data.empty:
            raise ValueError(
                "fit_dates select no rows in this frame; fit on training data and pass "
                "the resulting Preprocessor when transforming OOS data"
            )

    fitted = Preprocessor(config).fit(fit_data)
    return fitted.transform(features), fitted.manifest()
