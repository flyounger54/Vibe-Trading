
# ============================================================
# 中文名称: 卡脖子关注度因子
# 简要说明: 概念板块换手率异常 + 研报数量加速的复合注意力因子。
#           捕捉供应链主题从 Discovery 向 Validation 转变的早期信号。
# 典型用途: A 股供应链主题选股，与 ev_chokepoint 策略配合。
# ============================================================
"""Chokepoint attention momentum factor (sentiment_chokepoint_attn).

Composite of two sub-factors:
1. Concept-board turnover anomaly: z-score of board turnover vs 20d MA.
2. Analyst coverage acceleration: 30-day rate of change in volume (proxy
   for coverage momentum when report count data is unavailable).

Both sub-factors are computed purely from OHLCV data (no external API
calls), making the factor backtest-safe with zero lookahead.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

__alpha_meta__ = {
    "id": "sentiment_chokepoint_attn",
    "nickname": "卡脖子关注度 — 换手率异常 + 成交加速",
    "theme": ["sentiment"],
    "formula_latex": (
        r"0.6 \cdot \mathrm{zscore}_{20}\!\left(\frac{V_t}{\bar{V}_{20}}\right)"
        r" + 0.4 \cdot \mathrm{zscore}_{20}\!\left(\frac{\bar{V}_{5} - \bar{V}_{20}}{\bar{V}_{20}}\right)"
    ),
    "columns_required": ["close", "volume"],
    "universe": ["equity_cn"],
    "frequency": ["1d"],
    "decay_horizon": 20,
    "min_warmup_bars": 30,
    "notes": (
        "Chokepoint attention factor. Sub-factor 1: volume vs 20d MA "
        "z-score captures concept-board turnover anomalies. Sub-factor 2: "
        "short-term vs long-term volume acceleration captures analyst "
        "coverage momentum. Weighted 60/40. Cross-sectional z-score per "
        "date for ranking. Higher values = rising attention on the theme."
    ),
}

_SHORT_WINDOW = 5
_LONG_WINDOW = 20
_WEIGHT_TURNOVER = 0.6
_WEIGHT_ACCELERATION = 0.4


def _cross_sectional_zscore(df: pd.DataFrame) -> pd.DataFrame:
    mean = df.mean(axis=1, skipna=True)
    std = df.std(axis=1, ddof=1, skipna=True)
    centered = df.sub(mean, axis=0)
    result = centered.div(std.where(std > 0), axis=0)
    return result.replace([np.inf, -np.inf], np.nan)


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return chokepoint attention z-score.

    Sub-factor 1 (turnover anomaly): volume_t / MA20(volume) z-scored.
    Sub-factor 2 (volume acceleration): (MA5 - MA20) / MA20 z-scored.
    """
    volume = panel["close"] * 0  # shape template
    if "volume" in panel:
        volume = panel["volume"].astype(float)
    else:
        return volume * np.nan

    vol_ma_long = volume.rolling(window=_LONG_WINDOW, min_periods=10).mean()
    vol_ma_long_safe = vol_ma_long.where(vol_ma_long > 0)

    turnover_ratio = volume / vol_ma_long_safe
    sub1 = _cross_sectional_zscore(turnover_ratio)

    vol_ma_short = volume.rolling(window=_SHORT_WINDOW, min_periods=2).mean()
    acceleration = (vol_ma_short - vol_ma_long) / vol_ma_long_safe
    sub2 = _cross_sectional_zscore(acceleration)

    composite = _WEIGHT_TURNOVER * sub1.fillna(0) + _WEIGHT_ACCELERATION * sub2.fillna(0)

    return _cross_sectional_zscore(composite)
