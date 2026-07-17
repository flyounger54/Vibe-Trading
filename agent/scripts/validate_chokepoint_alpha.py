#!/usr/bin/env python3
"""Validate chokepoint score alpha effectiveness.

Two tests:
1. **Bucket monotonicity**: Split tickers into CS=3/4/5 score buckets,
   verify excess returns increase monotonically with score. Report p-values
   via Welch's t-test between adjacent buckets.

2. **Walk-forward degradation ratio**: Run IS/OOS split, compute
   OOS/IS expectancy ratio. Passes if ratio > 0.5.

Usage:
    python scripts/validate_chokepoint_alpha.py --returns returns.csv --scores scores.csv

Input files:
    returns.csv: date, code, return_1d (or return_5d, return_10d)
    scores.csv:  code, chokepoint_score (0-100)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats


def load_data(
    returns_path: str, scores_path: str, return_col: str = "return_10d"
) -> pd.DataFrame:
    """Merge returns with chokepoint scores."""
    returns = pd.read_csv(returns_path, parse_dates=["date"])
    scores = pd.read_csv(scores_path)

    if return_col not in returns.columns:
        candidates = [c for c in returns.columns if "return" in c.lower()]
        if candidates:
            return_col = candidates[0]
            print(f"Using return column: {return_col}")
        else:
            sys.exit(f"No return column found. Available: {list(returns.columns)}")

    merged = returns.merge(scores[["code", "chokepoint_score"]], on="code", how="inner")
    merged = merged.dropna(subset=[return_col, "chokepoint_score"])
    merged["return"] = merged[return_col].astype(float)
    return merged


def bucket_monotonicity_test(
    df: pd.DataFrame,
    buckets: list[tuple[str, int, int]] | None = None,
) -> dict:
    """Test that excess returns increase with chokepoint score buckets."""
    if buckets is None:
        buckets = [
            ("CS=3 (40-59)", 40, 59),
            ("CS=4 (60-79)", 60, 79),
            ("CS=5 (80-100)", 80, 100),
        ]

    results = []
    for label, lo, hi in buckets:
        mask = (df["chokepoint_score"] >= lo) & (df["chokepoint_score"] <= hi)
        subset = df.loc[mask, "return"]
        results.append({
            "bucket": label,
            "count": len(subset),
            "mean_return": subset.mean() if len(subset) > 0 else np.nan,
            "std_return": subset.std() if len(subset) > 0 else np.nan,
            "median_return": subset.median() if len(subset) > 0 else np.nan,
        })

    p_values = []
    for i in range(len(results) - 1):
        mask_lo = (
            (df["chokepoint_score"] >= buckets[i][1])
            & (df["chokepoint_score"] <= buckets[i][2])
        )
        mask_hi = (
            (df["chokepoint_score"] >= buckets[i + 1][1])
            & (df["chokepoint_score"] <= buckets[i + 1][2])
        )
        r_lo = df.loc[mask_lo, "return"].dropna()
        r_hi = df.loc[mask_hi, "return"].dropna()

        if len(r_lo) >= 5 and len(r_hi) >= 5:
            t_stat, p_val = stats.ttest_ind(r_hi, r_lo, equal_var=False)
            p_values.append({
                "comparison": f"{buckets[i][0]} vs {buckets[i + 1][0]}",
                "t_stat": round(t_stat, 3),
                "p_value": round(p_val, 4),
                "significant": p_val < 0.05,
            })
        else:
            p_values.append({
                "comparison": f"{buckets[i][0]} vs {buckets[i + 1][0]}",
                "t_stat": np.nan,
                "p_value": np.nan,
                "significant": False,
            })

    monotonic = all(
        results[i]["mean_return"] <= results[i + 1]["mean_return"]
        for i in range(len(results) - 1)
        if not np.isnan(results[i]["mean_return"])
        and not np.isnan(results[i + 1]["mean_return"])
    )

    return {
        "test": "bucket_monotonicity",
        "buckets": results,
        "p_values": p_values,
        "monotonic": monotonic,
        "pass": monotonic and all(p.get("significant", False) for p in p_values),
    }


def walk_forward_degradation(
    df: pd.DataFrame, is_ratio: float = 0.6
) -> dict:
    """Walk-forward IS/OOS split to check for overfitting."""
    dates = sorted(df["date"].unique())
    split_idx = int(len(dates) * is_ratio)
    is_dates = set(dates[:split_idx])
    oos_dates = set(dates[split_idx:])

    df_is = df[df["date"].isin(is_dates)]
    df_oos = df[df["date"].isin(oos_dates)]

    high_score = df["chokepoint_score"] >= 60

    is_return = df_is.loc[high_score & df_is.index.isin(df_is.index), "return"].mean()
    oos_return = df_oos.loc[high_score & df_oos.index.isin(df_oos.index), "return"].mean()

    if np.isnan(is_return) or is_return == 0:
        ratio = np.nan
    else:
        ratio = oos_return / is_return

    return {
        "test": "walk_forward_degradation",
        "is_period": f"{dates[0]} to {dates[split_idx - 1]}",
        "oos_period": f"{dates[split_idx]} to {dates[-1]}",
        "is_mean_return": round(is_return, 4) if not np.isnan(is_return) else None,
        "oos_mean_return": round(oos_return, 4) if not np.isnan(oos_return) else None,
        "degradation_ratio": round(ratio, 3) if not np.isnan(ratio) else None,
        "pass": ratio > 0.5 if not np.isnan(ratio) else False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate chokepoint alpha")
    parser.add_argument("--returns", required=True, help="Path to returns CSV")
    parser.add_argument("--scores", required=True, help="Path to scores CSV")
    parser.add_argument(
        "--return-col", default="return_10d", help="Return column name"
    )
    args = parser.parse_args()

    print("Loading data...")
    df = load_data(args.returns, args.scores, args.return_col)
    print(f"  {len(df)} rows, {df['code'].nunique()} tickers")

    print("\n=== Test 1: Bucket Monotonicity ===")
    mono = bucket_monotonicity_test(df)
    for b in mono["buckets"]:
        print(f"  {b['bucket']}: n={b['count']}, mean={b['mean_return']:.4f}")
    for p in mono["p_values"]:
        sig = "***" if p["significant"] else ""
        print(f"  {p['comparison']}: t={p['t_stat']}, p={p['p_value']} {sig}")
    print(f"  Monotonic: {mono['monotonic']}")
    print(f"  PASS: {mono['pass']}")

    print("\n=== Test 2: Walk-Forward Degradation ===")
    wf = walk_forward_degradation(df)
    print(f"  IS: {wf['is_period']}, mean={wf['is_mean_return']}")
    print(f"  OOS: {wf['oos_period']}, mean={wf['oos_mean_return']}")
    print(f"  Degradation ratio: {wf['degradation_ratio']} (threshold > 0.5)")
    print(f"  PASS: {wf['pass']}")

    print("\n=== Overall ===")
    overall = mono["pass"] and wf["pass"]
    print(f"  {'PASS' if overall else 'FAIL'}")
    sys.exit(0 if overall else 1)


if __name__ == "__main__":
    main()
