
# ============================================================
# 中文名称: 供应链卡脖子信号策略
# 简要说明: 基于6维卡脖子评分 × 主题生命周期乘数 × 证据置信度的
#           复合选股信号。标的宇宙由用户配置（预建181只或自定义）。
#           信号值 ∈ [0, 1]，纯做多，截面 z-score 归一化。
# 典型用途: AI供应链卡脖子标的选股，中长期持有。
# ============================================================
"""Supply-chain chokepoint composite signal (ev_chokepoint).

Combines three layers into a single long-only signal:
1. Chokepoint score (6-dimension, 0-100)
2. Theme lifecycle alpha multiplier (Discovery 0.9 → Exhaustion 0.1)
3. Evidence confidence weight (High 1.0 → Very Low 0.2)

The strategy is universe-agnostic: it scores whatever tickers appear in
``data_map``. The caller (backtest runner or Swarm) is responsible for
providing the relevant universe via ``config.json`` codes list or a prior
``get_supply_chain(mode="universe")`` call.
"""

from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd

__strategy_meta__ = {
    "id": "ev_chokepoint",
    "nickname": "供应链卡脖子信号",
    "category": "event_driven",
    "description": (
        "Composite chokepoint signal: 6-dimension score × lifecycle "
        "multiplier × evidence confidence. Scores tickers in the "
        "AI supply-chain universe. Long-only, cross-sectional z-score "
        "normalised. Tickers without chokepoint_score default to 0."
    ),
    "universe": ["equity_cn", "equity_us", "equity_hk"],
    "frequency": ["1D"],
    "columns_required": ["close", "volume"],
    "default_params": {
        "score_source": "manual",
        "min_liquidity_cny": 5_000_000,
        "min_score": 40,
        "lifecycle_multiplier": 1.0,
        "evidence_confidence": 1.0,
    },
    "risk_profile": "medium",
    "min_bars": 20,
    "reference": "Serenity chokepoint methodology",
    "factors_used": [],
}

_TIER_THRESHOLDS = {
    "core": 80,
    "build": 60,
    "watch": 40,
    "skip": 0,
}

_LIFECYCLE_MULTIPLIERS = {
    "discovery": 0.9,
    "validation": 0.7,
    "mainstream": 0.3,
    "exhaustion": 0.1,
}

_CONFIDENCE_WEIGHTS = {
    "high": 1.0,
    "medium": 0.7,
    "low": 0.4,
    "very_low": 0.2,
}

_LIQUIDITY_LOOKBACK = 20


class SignalEngine:
    """供应链卡脖子复合信号引擎。"""

    def __init__(self, **params: Any) -> None:
        self.score_source: str = str(params.get("score_source", "manual"))
        self.min_liquidity: float = float(
            params.get("min_liquidity_cny", 5_000_000)
        )
        self.min_score: int = int(params.get("min_score", 40))
        self.lifecycle_multiplier: float = float(
            params.get("lifecycle_multiplier", 1.0)
        )
        self.evidence_confidence: float = float(
            params.get("evidence_confidence", 1.0)
        )
        self.chokepoint_scores: dict[str, float] = params.get(
            "chokepoint_scores", {}
        )
        self.lifecycle_stages: dict[str, str] = params.get(
            "lifecycle_stages", {}
        )
        self.confidence_levels: dict[str, str] = params.get(
            "confidence_levels", {}
        )

    def _get_score(self, code: str) -> float:
        return self.chokepoint_scores.get(code, 0.0)

    def _get_lifecycle_mult(self, code: str) -> float:
        stage = self.lifecycle_stages.get(code, "")
        return _LIFECYCLE_MULTIPLIERS.get(stage, self.lifecycle_multiplier)

    def _get_confidence_weight(self, code: str) -> float:
        level = self.confidence_levels.get(code, "")
        return _CONFIDENCE_WEIGHTS.get(level, self.evidence_confidence)

    def _passes_liquidity_filter(self, df: pd.DataFrame) -> pd.Series:
        if "amount" in df.columns:
            avg_amount = (
                pd.to_numeric(df["amount"], errors="coerce")
                .rolling(window=_LIQUIDITY_LOOKBACK, min_periods=1)
                .mean()
            )
            return avg_amount >= self.min_liquidity

        if "volume" in df.columns and "close" in df.columns:
            turnover = (
                pd.to_numeric(df["volume"], errors="coerce")
                * pd.to_numeric(df["close"], errors="coerce")
            )
            avg_turnover = turnover.rolling(
                window=_LIQUIDITY_LOOKBACK, min_periods=1
            ).mean()
            return avg_turnover >= self.min_liquidity

        return pd.Series(True, index=df.index, dtype=bool)

    def _is_st(self, code: str, df: pd.DataFrame) -> bool:
        if "name" in df.columns:
            last_name = str(df["name"].iloc[-1]) if len(df) > 0 else ""
            return "ST" in last_name.upper()
        return False

    def generate(
        self, data_map: Dict[str, pd.DataFrame]
    ) -> Dict[str, pd.Series]:
        """Generate chokepoint composite signals.

        For each ticker:
        1. Look up chokepoint_score (from params or default 0)
        2. Apply lifecycle multiplier and evidence confidence weight
        3. Filter by min_score, liquidity, ST status
        4. Cross-sectional z-score normalise to [0, 1]
        """
        raw_scores: dict[str, pd.Series] = {}
        valid_codes: list[str] = []

        for code, df in data_map.items():
            if not {"close", "volume"}.issubset(df.columns):
                continue

            if self._is_st(code, df):
                continue

            score = self._get_score(code)
            if score < self.min_score:
                raw_scores[code] = pd.Series(0.0, index=df.index, dtype=float)
                continue

            lifecycle_mult = self._get_lifecycle_mult(code)
            confidence_wt = self._get_confidence_weight(code)
            composite = score * lifecycle_mult * confidence_wt

            liquidity_mask = self._passes_liquidity_filter(df)
            signal = pd.Series(composite, index=df.index, dtype=float)
            signal = signal.where(liquidity_mask, other=0.0)

            raw_scores[code] = signal
            if composite > 0:
                valid_codes.append(code)

        if not valid_codes:
            return {code: pd.Series(0.0, index=df.index, dtype=float)
                    for code, df in data_map.items()
                    if len(df) > 0}

        signals: Dict[str, pd.Series] = {}
        for code, df in data_map.items():
            if code not in raw_scores:
                signals[code] = pd.Series(0.0, index=df.index, dtype=float)
                continue

            raw = raw_scores[code]
            all_scores = pd.DataFrame({
                c: raw_scores.get(c, pd.Series(0.0, index=df.index))
                for c in valid_codes
            })

            row_mean = all_scores.iloc[-1].mean() if len(all_scores) > 0 else 0
            row_std = all_scores.iloc[-1].std() if len(all_scores) > 0 else 1
            if row_std == 0 or np.isnan(row_std):
                row_std = 1.0

            z = (raw - row_mean) / row_std
            normalised = z.clip(0.0, 3.0) / 3.0
            signals[code] = normalised.clip(0.0, 1.0)

        return signals
