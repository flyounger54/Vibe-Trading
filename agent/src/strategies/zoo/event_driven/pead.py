
# ============================================================
# 中文名称: 盈余公告后漂移策略
# 简要说明: 盈余公告后价格向意外方向持续漂移。通过异常放量+异常收益检测盈余
#           事件（或直接使用 earnings_surprise 列），信号方向与意外收益一致，
#           持有 N 天后衰减。信号值 ∈ [-1, 1]，NaN 安全。
# 典型用途: 事件驱动型策略，捕捉财报公告后的价格漂移。
# ============================================================
"""Post-Earnings Announcement Drift (ev_pead).

After an earnings surprise (abnormal volume + abnormal return), price
tends to drift in the direction of the surprise. Signal = direction of
the surprise return, held for N days.
"""

from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd

__strategy_meta__ = {
    "id": "ev_pead",
    "nickname": "盈余公告后漂移",
    "category": "event_driven",
    "description": (
        "Post-Earnings Announcement Drift: after an earnings surprise "
        "(proxied by abnormal volume + abnormal return), drift continues "
        "in the same direction. Signal held for N days after the event."
    ),
    "universe": ["equity_cn", "equity_us"],
    "frequency": ["1D"],
    "columns_required": ["close", "volume"],
    "default_params": {
        "holding_days": 20,
        "vol_mult": 3.0,
        "ret_mult": 2.0,
        "vol_lookback": 60,
    },
    "risk_profile": "medium",
    "min_bars": 65,
    "reference": "Ball & Brown, An Empirical Evaluation of Accounting Income Numbers, 1968",
    "factors_used": [],
}


class SignalEngine:
    """盈余公告后漂移信号引擎。"""

    def __init__(self, **params: Any) -> None:
        self.holding_days: int = int(params.get("holding_days", 20))
        self.vol_mult: float = float(params.get("vol_mult", 3.0))
        self.ret_mult: float = float(params.get("ret_mult", 2.0))
        self.vol_lookback: int = int(params.get("vol_lookback", 60))
        if self.holding_days < 1:
            raise ValueError(f"holding_days must be >= 1, got {self.holding_days}")
        if self.vol_lookback < 2:
            raise ValueError(f"vol_lookback must be >= 2, got {self.vol_lookback}")

    def _detect_events(
        self, close: pd.Series, volume: pd.Series
    ) -> pd.Series:
        """Detect earnings events via abnormal volume + abnormal return.

        Returns a Series of event directions: +1.0, -1.0, or 0.0.
        """
        ret = close.pct_change()

        # Rolling statistics (use lookback *before* current bar to avoid lookahead)
        vol_ma = volume.rolling(
            window=self.vol_lookback, min_periods=self.vol_lookback
        ).mean().shift(1)
        ret_std = ret.rolling(
            window=self.vol_lookback, min_periods=self.vol_lookback
        ).std().shift(1)

        # Abnormal volume: current volume > vol_mult * rolling average
        abnormal_vol = volume > (self.vol_mult * vol_ma)

        # Abnormal return: |return| > ret_mult * rolling std
        abnormal_ret = ret.abs() > (self.ret_mult * ret_std)

        # Event = both conditions met
        event_mask = abnormal_vol & abnormal_ret

        # Direction = sign of the return on the event day
        direction = np.sign(ret)

        event_signal = pd.Series(0.0, index=close.index)
        event_signal = event_signal.where(~event_mask, direction)

        # NaN where rolling stats are not ready
        not_ready = vol_ma.isna() | ret_std.isna()
        event_signal = event_signal.where(~not_ready, other=np.nan)

        return event_signal

    def _apply_holding(self, event_signal: pd.Series) -> pd.Series:
        """Hold event signal for holding_days, then decay to zero.

        When a new event occurs during a holding period, the new event
        replaces the old signal.
        """
        signal = pd.Series(np.nan, index=event_signal.index)
        active_dir = 0.0
        remaining = 0

        for i in range(len(event_signal)):
            ev = event_signal.iloc[i]

            if pd.isna(ev):
                signal.iloc[i] = np.nan
                continue

            # New event detected — reset holding counter
            if ev != 0.0:
                active_dir = float(ev)
                remaining = self.holding_days

            if remaining > 0:
                signal.iloc[i] = active_dir
                remaining -= 1
            else:
                signal.iloc[i] = 0.0

        return signal

    def generate(
        self, data_map: Dict[str, pd.DataFrame]
    ) -> Dict[str, pd.Series]:
        """Generate PEAD signals for each instrument.

        Returns:
            Dict mapping ticker -> pd.Series of signals in [-1.0, 1.0].
        """
        signals: Dict[str, pd.Series] = {}

        for code, df in data_map.items():
            if "close" not in df.columns:
                continue

            close = df["close"].astype(float)

            # Path 1: Direct earnings_surprise column
            if "earnings_surprise" in df.columns:
                surprise = df["earnings_surprise"].astype(float)
                # Use sign of surprise as event direction
                event_signal = pd.Series(0.0, index=close.index)
                has_surprise = surprise.notna() & (surprise != 0.0)
                event_signal = event_signal.where(
                    ~has_surprise, np.sign(surprise)
                )
                event_signal = event_signal.where(surprise.notna(), other=np.nan)
            else:
                # Path 2: Detect from volume + return
                if "volume" not in df.columns:
                    continue
                volume = df["volume"].astype(float)
                event_signal = self._detect_events(close, volume)

            # Apply holding period
            signal = self._apply_holding(event_signal)

            # Final clamp
            signal = signal.clip(-1.0, 1.0)
            signals[code] = signal

        return signals
