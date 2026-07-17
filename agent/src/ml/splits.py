"""Walk-forward time-series splitting with Purge and Embargo."""

from __future__ import annotations

import numpy as np
import pandas as pd


def walk_forward_split(
    dates: pd.DatetimeIndex | np.ndarray,
    n_splits: int = 5,
    min_train_days: int = 252,
    expanding: bool = True,
    purge_days: int = 0,
    gap_days: int = 0,
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Generate walk-forward train/test splits with Purge + Embargo.

    All stocks on the same date land in the same fold (no cross-sectional leakage).

    Args:
        dates: Sorted unique trading dates.
        n_splits: Number of out-of-sample folds.
        min_train_days: Minimum number of training bars.
        expanding: True = expanding window, False = rolling window.
        purge_days: Remove last N training days whose label horizon
            bleeds into the test period. Should equal label_horizon.
        gap_days: Additional embargo buffer after purge.

    Returns:
        List of (train_date_indices, test_date_indices) tuples.
        Indices reference positions in the input dates array.
    """
    unique_dates = np.sort(np.unique(dates))
    n_dates = len(unique_dates)

    total_buffer = purge_days + gap_days
    available_for_test = n_dates - min_train_days - total_buffer
    if available_for_test < n_splits:
        raise ValueError(
            f"Not enough dates ({n_dates}) for {n_splits} splits with "
            f"min_train={min_train_days}, purge={purge_days}, gap={gap_days}. "
            f"Available for test: {available_for_test}"
        )

    test_size = available_for_test // n_splits
    splits: list[tuple[np.ndarray, np.ndarray]] = []

    for fold in range(n_splits):
        test_start_idx = min_train_days + total_buffer + fold * test_size
        test_end_idx = test_start_idx + test_size
        if fold == n_splits - 1:
            test_end_idx = n_dates

        train_end_idx = test_start_idx - total_buffer
        if expanding:
            train_start_idx = 0
        else:
            train_start_idx = max(0, train_end_idx - min_train_days)

        if train_end_idx - purge_days > train_start_idx:
            effective_train_end = train_end_idx - purge_days
        else:
            effective_train_end = train_end_idx

        train_indices = np.arange(train_start_idx, effective_train_end)
        test_indices = np.arange(test_start_idx, test_end_idx)

        splits.append((train_indices, test_indices))

    return splits


def get_dates_for_split(
    unique_dates: np.ndarray,
    split: tuple[np.ndarray, np.ndarray],
) -> tuple[np.ndarray, np.ndarray]:
    """Convert index-based splits to actual date values."""
    train_idx, test_idx = split
    return unique_dates[train_idx], unique_dates[test_idx]


def auto_purge_days(label_config: "LabelConfig") -> int:
    """Derive purge_days from label horizon to prevent label leakage."""
    from src.ml.base_model import LabelConfig

    if isinstance(label_config, LabelConfig):
        return label_config.horizon
    return 0
