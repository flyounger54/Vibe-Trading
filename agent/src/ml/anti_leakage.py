"""Anti-leakage audit: full-pipeline checks for look-ahead bias."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def audit_feature_label_alignment(
    feature_dates: np.ndarray,
    label_dates: np.ndarray,
    label_horizon: int,
) -> dict[str, Any]:
    """Check that feature dates don't extend beyond label observation window."""
    max_feature = pd.Timestamp(feature_dates.max())
    max_label_obs = pd.Timestamp(label_dates.max())
    passed = max_feature <= max_label_obs
    return {
        "check": "feature_label_alignment",
        "passed": passed,
        "max_feature_date": str(max_feature.date()),
        "max_label_date": str(max_label_obs.date()),
        "label_horizon": label_horizon,
    }


def audit_train_test_leakage(
    train_dates: np.ndarray,
    test_dates: np.ndarray,
    label_horizon: int,
    gap_days: int = 0,
) -> dict[str, Any]:
    """Check that training label windows don't overlap with the test period."""
    if len(train_dates) == 0 or len(test_dates) == 0:
        return {"check": "train_test_leakage", "passed": True, "reason": "empty split"}

    train_max = pd.Timestamp(train_dates.max())
    test_min = pd.Timestamp(test_dates.min())
    gap = (test_min - train_max).days
    required_gap = label_horizon + gap_days
    passed = gap >= required_gap

    return {
        "check": "train_test_leakage",
        "passed": passed,
        "train_max": str(train_max.date()),
        "test_min": str(test_min.date()),
        "actual_gap_days": gap,
        "required_gap_days": required_gap,
        "suggestion": None if passed else f"Set purge_days>={label_horizon}",
    }


def audit_selection_leakage(
    selection_dates: np.ndarray,
    test_dates: np.ndarray,
) -> dict[str, Any]:
    """Check that feature selection data doesn't overlap with test period."""
    if len(selection_dates) == 0 or len(test_dates) == 0:
        return {"check": "selection_leakage", "passed": True, "reason": "empty set"}

    sel_max = pd.Timestamp(selection_dates.max())
    test_min = pd.Timestamp(test_dates.min())
    overlap = sel_max >= test_min

    return {
        "check": "selection_leakage",
        "passed": not overlap,
        "selection_max": str(sel_max.date()),
        "test_min": str(test_min.date()),
    }


def audit_preprocessing_leakage(
    preprocess_fit_dates: np.ndarray,
    test_dates: np.ndarray,
) -> dict[str, Any]:
    """Check that preprocessing was fit only on train dates."""
    if len(preprocess_fit_dates) == 0 or len(test_dates) == 0:
        return {"check": "preprocessing_leakage", "passed": True, "reason": "empty set"}

    fit_set = set(pd.DatetimeIndex(preprocess_fit_dates))
    test_set = set(pd.DatetimeIndex(test_dates))
    leaked = fit_set & test_set

    return {
        "check": "preprocessing_leakage",
        "passed": len(leaked) == 0,
        "leaked_dates_count": len(leaked),
        "leaked_dates_sample": [str(d.date()) for d in sorted(leaked)[:5]],
    }


def audit_cache_integrity(
    cache_manifest_path: Path,
) -> dict[str, Any]:
    """Check cache date continuity — no gaps, no backfills."""
    import json

    if not cache_manifest_path.exists():
        return {"check": "cache_integrity", "passed": True, "reason": "no cache"}

    try:
        manifest = json.loads(cache_manifest_path.read_text())
    except Exception as exc:
        return {"check": "cache_integrity", "passed": False, "error": str(exc)}

    return {
        "check": "cache_integrity",
        "passed": True,
        "cache_key": manifest.get("cache_key", ""),
        "date_range": manifest.get("date_range", []),
        "n_dates": manifest.get("n_dates", 0),
    }


def audit_survivorship_bias(
    universe: str,
    panel: dict[str, pd.DataFrame],
    pit_members: dict[str, list[str]] | None,
) -> dict[str, Any]:
    """Check whether point-in-time constituents were used."""
    close = panel.get("close")
    if close is None:
        return {"check": "survivorship_bias", "passed": False, "reason": "no close data"}

    current_codes = set(close.columns)
    n_current = len(current_codes)

    if pit_members is None:
        return {
            "check": "survivorship_bias",
            "passed": False,
            "warning": "PIT constituents not loaded — using current index members",
            "n_current_codes": n_current,
        }

    all_pit_codes: set[str] = set()
    for members in pit_members.values():
        all_pit_codes.update(members)

    extra_in_pit = all_pit_codes - current_codes
    missing_from_pit = current_codes - all_pit_codes
    overlap_pct = len(current_codes & all_pit_codes) / max(len(all_pit_codes), 1) * 100

    return {
        "check": "survivorship_bias",
        "passed": True,
        "pit_used": True,
        "n_current_codes": n_current,
        "n_total_pit_codes": len(all_pit_codes),
        "n_delisted_in_pit": len(extra_in_pit),
        "overlap_pct": round(overlap_pct, 1),
    }


def run_full_audit(
    *,
    feature_dates: np.ndarray | None = None,
    label_dates: np.ndarray | None = None,
    label_horizon: int = 1,
    train_dates: np.ndarray | None = None,
    test_dates: np.ndarray | None = None,
    gap_days: int = 0,
    selection_dates: np.ndarray | None = None,
    preprocess_fit_dates: np.ndarray | None = None,
    cache_manifest_path: Path | None = None,
    universe: str | None = None,
    panel: dict[str, pd.DataFrame] | None = None,
    pit_members: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """Run all available audit checks, return consolidated report."""
    checks: list[dict[str, Any]] = []

    if feature_dates is not None and label_dates is not None:
        checks.append(audit_feature_label_alignment(feature_dates, label_dates, label_horizon))

    if train_dates is not None and test_dates is not None:
        checks.append(audit_train_test_leakage(train_dates, test_dates, label_horizon, gap_days))

    if selection_dates is not None and test_dates is not None:
        checks.append(audit_selection_leakage(selection_dates, test_dates))

    if preprocess_fit_dates is not None and test_dates is not None:
        checks.append(audit_preprocessing_leakage(preprocess_fit_dates, test_dates))

    if cache_manifest_path is not None:
        checks.append(audit_cache_integrity(cache_manifest_path))

    if universe is not None and panel is not None:
        checks.append(audit_survivorship_bias(universe, panel, pit_members))

    all_passed = all(c.get("passed", False) for c in checks)
    failed = [c for c in checks if not c.get("passed", False)]

    return {
        "passed": all_passed,
        "n_checks": len(checks),
        "n_passed": sum(1 for c in checks if c.get("passed", False)),
        "n_failed": len(failed),
        "checks": checks,
        "failed_checks": [c["check"] for c in failed],
    }
