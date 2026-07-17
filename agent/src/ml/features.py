"""Feature engineering: Alpha Zoo factors → ML feature matrix + preprocessing."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src.ml.base_model import PreprocessConfig

logger = logging.getLogger(__name__)


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
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Preprocess feature matrix. Fit parameters only on fit_dates to prevent leakage.

    Returns (processed_features, preprocess_params_dict).
    """
    result = features.copy()
    params: dict[str, Any] = {}
    dates = result.index.get_level_values("date")

    if fit_dates is not None:
        fit_mask = dates.isin(fit_dates)
    else:
        fit_mask = pd.Series(True, index=result.index)

    if config.winsorize:
        lo, hi = config.winsorize_limits
        fit_data = result.loc[fit_mask]
        lower = fit_data.quantile(lo)
        upper = fit_data.quantile(hi)
        result = result.clip(lower=lower, upper=upper, axis=1)
        params["winsorize_lower"] = lower.to_dict()
        params["winsorize_upper"] = upper.to_dict()

    if config.zscore:
        fit_data = result.loc[fit_mask]
        mean = fit_data.mean()
        std = fit_data.std().replace(0, 1.0)
        result = (result - mean) / std
        params["zscore_mean"] = mean.to_dict()
        params["zscore_std"] = std.to_dict()

    if config.fillna_strategy == "median":
        fit_data = result.loc[fit_mask]
        medians = fit_data.median()
        result = result.fillna(medians)
        params["fillna_medians"] = medians.to_dict()
    elif config.fillna_strategy == "zero":
        result = result.fillna(0.0)
    elif config.fillna_strategy == "ffill":
        result = result.groupby(level="code").ffill()
    # "none" → no fill

    return result, params
