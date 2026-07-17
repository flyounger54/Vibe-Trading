
# ============================================================
# 中文名称: 北向资金跟踪策略
# 简要说明: 跟踪沪深港通北向资金净流入。净流入均值 > 阈值 → 做多信号，
#           净流出 > 阈值 → 减仓/做空信号。若无 northbound_flow 列，
#           回退到成交量变化率启发式代理信号。
#           信号值 ∈ [-1, 1]，NaN 安全，无前瞻偏差。
# 典型用途: A 股中短期择时，捕捉外资情绪。
# ============================================================
"""Northbound Fund Flow Tracking (ev_northbound).

Track northbound fund flow (沪深港通北向资金). If net inflow MA > threshold,
go long. If net outflow MA > threshold, go short / reduce position.

Fallback: if ``northbound_flow`` column is absent, uses volume change
rate as a proxy for institutional activity.
"""

from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd

__strategy_meta__ = {
    "id": "ev_northbound",
    "nickname": "北向资金跟踪",
    "category": "event_driven",
    "description": (
        "Track northbound fund flow. If northbound_flow column is "
        "available and net inflow MA > threshold, go long. If net "
        "outflow MA > threshold, reduce position. Falls back to "
        "volume-change-rate heuristic when northbound_flow data "
        "is unavailable."
    ),
    "universe": ["equity_cn"],
    "frequency": ["1D"],
    "columns_required": ["close", "volume"],
    "default_params": {
        "flow_ma_period": 5,
        "entry_threshold": 0.0,
        "exit_threshold": 0.0,
    },
    "risk_profile": "medium",
    "min_bars": 10,
    "reference": "沪深港通北向资金流向",
    "factors_used": [],
}

_VOLUME_CHANGE_LOOKBACK = 20


class SignalEngine:
    """北向资金跟踪信号引擎。"""

    def __init__(self, **params: Any) -> None:
        self.flow_ma_period: int = int(params.get("flow_ma_period", 5))
        self.entry_threshold: float = float(params.get("entry_threshold", 0.0))
        self.exit_threshold: float = float(params.get("exit_threshold", 0.0))

        if self.flow_ma_period < 1:
            raise ValueError(
                f"flow_ma_period must be >= 1, got {self.flow_ma_period}"
            )

    def _compute_flow_signal(self, flow: pd.Series) -> pd.Series:
        """Convert raw flow series into a signal in [-1, 1].

        Uses a smoothed (MA) flow and maps it to signal strength via
        normalisation against rolling standard deviation.
        """
        flow_ma = flow.rolling(
            window=self.flow_ma_period,
            min_periods=1,
        ).mean()

        # Normalise by rolling std to get a pseudo-Z-score
        flow_std = flow.rolling(
            window=max(self.flow_ma_period * 4, 20),
            min_periods=max(1, self.flow_ma_period),
        ).std()
        flow_std = flow_std.replace(0.0, np.nan)

        z = flow_ma / flow_std

        # Map Z-score to [-1, 1] using tanh-like clipping
        # Z > entry_threshold → positive; Z < -entry_threshold → negative
        signal = pd.Series(0.0, index=flow.index, dtype=float)

        long_mask = z > self.entry_threshold
        short_mask = z < -self.entry_threshold
        neutral_mask = z.abs() <= self.exit_threshold

        # Scale proportionally: stronger flow → stronger signal
        # Cap at 3 std for full signal
        signal = (z / 3.0).clip(-1.0, 1.0)

        # Apply threshold gating
        signal = signal.where(long_mask | short_mask, other=0.0)
        signal = signal.where(~neutral_mask, other=0.0)

        return signal

    def _volume_change_proxy(self, df: pd.DataFrame) -> pd.Series:
        """Compute volume change rate as a proxy for fund flow.

        Logic: (recent_avg_volume - prior_avg_volume) / prior_avg_volume.
        Positive change → inflow proxy; negative → outflow proxy.
        """
        volume = df["volume"].astype(float)
        short_ma = volume.rolling(
            window=self.flow_ma_period, min_periods=1
        ).mean()
        long_ma = volume.rolling(
            window=_VOLUME_CHANGE_LOOKBACK,
            min_periods=max(1, _VOLUME_CHANGE_LOOKBACK // 2),
        ).mean()
        long_ma_safe = long_ma.replace(0.0, np.nan)
        change_rate = (short_ma - long_ma) / long_ma_safe
        return change_rate.fillna(0.0)

    def generate(
        self, data_map: Dict[str, pd.DataFrame]
    ) -> Dict[str, pd.Series]:
        """为每个标的生成北向资金跟踪信号。

        Logic:
        1. If northbound_flow column exists, compute smoothed flow → signal.
        2. Otherwise, use volume change rate as institutional flow proxy.

        Returns:
            Dict mapping ticker -> pd.Series of signals in [-1.0, 1.0].
        """
        signals: Dict[str, pd.Series] = {}

        for code, df in data_map.items():
            if not {"close", "volume"}.issubset(df.columns):
                continue

            if "northbound_flow" in df.columns:
                flow = pd.to_numeric(
                    df["northbound_flow"], errors="coerce"
                ).fillna(0.0)
                signal = self._compute_flow_signal(flow)
            else:
                # Fallback: volume change rate proxy
                proxy_flow = self._volume_change_proxy(df)
                signal = self._compute_flow_signal(proxy_flow)

            signals[code] = signal.clip(-1.0, 1.0)

        return signals
