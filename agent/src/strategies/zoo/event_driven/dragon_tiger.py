
# ============================================================
# 中文名称: 龙虎榜跟踪策略
# 简要说明: A 股龙虎榜特色策略。当标的出现在龙虎榜(dragon_tiger > 0)时，
#           次日产生买入信号，持有 holding_days 后退出。
#           若无 dragon_tiger 列，回退到成交量异常放大启发式代理信号。
#           信号值 ∈ [-1, 1]，NaN 安全，无前瞻偏差。
# 典型用途: A 股短线事件驱动交易。
# ============================================================
"""Dragon Tiger Board Tracking (ev_dragon_tiger).

If a stock appears on the dragon-tiger board (龙虎榜), the next day
generates a buy signal held for ``holding_days``, then exits.

Fallback: if ``dragon_tiger`` column is absent, uses a volume-spike
heuristic (volume > 3x rolling average) as a proxy event signal.
"""

from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd

__strategy_meta__ = {
    "id": "ev_dragon_tiger",
    "nickname": "龙虎榜跟踪",
    "category": "event_driven",
    "description": (
        "If a stock appears on the dragon-tiger board, the next day "
        "generates a buy signal. If dragon_tiger column is present and "
        "> 0, signal = signal_strength for holding_days, then exit. "
        "Falls back to volume-spike detection when dragon_tiger data "
        "is unavailable."
    ),
    "universe": ["equity_cn"],
    "frequency": ["1D"],
    "columns_required": ["close", "volume"],
    "default_params": {"holding_days": 5, "signal_strength": 0.5},
    "risk_profile": "high",
    "min_bars": 10,
    "reference": "A股龙虎榜特色数据",
    "factors_used": [],
}

_VOLUME_SPIKE_MULTIPLIER = 3.0
_VOLUME_SPIKE_LOOKBACK = 20


class SignalEngine:
    """龙虎榜跟踪信号引擎。"""

    def __init__(self, **params: Any) -> None:
        self.holding_days: int = int(params.get("holding_days", 5))
        self.signal_strength: float = float(params.get("signal_strength", 0.5))

        if not 0.0 < self.signal_strength <= 1.0:
            raise ValueError(
                f"signal_strength must be in (0, 1], got {self.signal_strength}"
            )
        if self.holding_days < 1:
            raise ValueError(
                f"holding_days must be >= 1, got {self.holding_days}"
            )

    def _detect_events(self, df: pd.DataFrame) -> pd.Series:
        """Detect event triggers. Returns a boolean Series (True = event fired).

        Uses dragon_tiger column if available; otherwise falls back to
        volume-spike heuristic.
        """
        n = len(df)
        if "dragon_tiger" in df.columns:
            dt_col = pd.to_numeric(df["dragon_tiger"], errors="coerce")
            return dt_col.fillna(0.0) > 0
        else:
            # Fallback: volume spike heuristic
            volume = df["volume"].astype(float)
            avg_vol = volume.rolling(
                window=_VOLUME_SPIKE_LOOKBACK,
                min_periods=max(1, _VOLUME_SPIKE_LOOKBACK // 2),
            ).mean()
            spike = volume > (_VOLUME_SPIKE_MULTIPLIER * avg_vol)
            # Guard against NaN in avg_vol (early bars)
            return spike.fillna(False)

    def generate(
        self, data_map: Dict[str, pd.DataFrame]
    ) -> Dict[str, pd.Series]:
        """为每个标的生成龙虎榜事件信号。

        Logic:
        1. Detect event on day T (dragon_tiger > 0, or volume spike).
        2. Signal activates on day T+1 (no lookahead) with signal_strength.
        3. Signal stays active for holding_days, then returns to 0.

        Returns:
            Dict mapping ticker -> pd.Series of signals in [-1.0, 1.0].
        """
        signals: Dict[str, pd.Series] = {}

        for code, df in data_map.items():
            if not {"close", "volume"}.issubset(df.columns):
                continue

            events = self._detect_events(df)

            # Shift events by 1 to avoid lookahead: event on day T →
            # signal starts day T+1
            trigger = events.astype(bool).shift(1).fillna(False)

            # Build signal: for each trigger, hold signal_strength for
            # holding_days
            raw_signal = pd.Series(0.0, index=df.index, dtype=float)

            trigger_indices = trigger[trigger].index
            for t_idx in trigger_indices:
                loc = df.index.get_loc(t_idx)
                end_loc = min(loc + self.holding_days, len(df))
                raw_signal.iloc[loc:end_loc] = self.signal_strength

            # Clip to valid range (should already be in range, but ensure)
            signals[code] = raw_signal.clip(-1.0, 1.0)

        return signals
