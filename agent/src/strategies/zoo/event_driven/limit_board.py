
# ============================================================
# 中文名称: 打板策略
# 简要说明: A股涨停板追涨策略。检测涨停事件（主板≥9.8%，创业板/科创板≥19.5%），
#           涨停次日买入持有 N 天。使用半仓信号(0.5)控制高风险。
#           信号值 ∈ [0, 1]，NaN 安全。
# 典型用途: A股短线事件驱动策略，追逐强势涨停股。
# ============================================================
"""A-Share Limit-Up Board Strategy (ev_limit_board).

Detect limit-up events on A-shares (main board >=9.8%, ChiNext/STAR
>=19.5%). Buy next day after a limit-up and hold for N days. Signal
strength is capped at 0.5 (half position) due to high risk.
"""

from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd

__strategy_meta__ = {
    "id": "ev_limit_board",
    "nickname": "打板策略",
    "category": "event_driven",
    "description": (
        "A-share limit-up board strategy: detect daily limit-up events "
        "(main board >=9.8%, ChiNext/STAR >=19.5%), buy next day and hold "
        "for N days. Signal strength = 0.5 (half position due to high risk)."
    ),
    "universe": ["equity_cn"],
    "frequency": ["1D"],
    "columns_required": ["close"],
    "default_params": {
        "holding_days": 3,
        "signal_strength": 0.5,
        "main_board_pct": 0.098,
        "chinext_pct": 0.195,
    },
    "risk_profile": "high",
    "min_bars": 5,
    "reference": "A股涨停板打板策略, Tushare打板专题数据",
    "factors_used": [],
}


class SignalEngine:
    """打板策略信号引擎。"""

    def __init__(self, **params: Any) -> None:
        self.holding_days: int = int(params.get("holding_days", 3))
        self.signal_strength: float = float(params.get("signal_strength", 0.5))
        self.main_board_pct: float = float(params.get("main_board_pct", 0.098))
        self.chinext_pct: float = float(params.get("chinext_pct", 0.195))
        if self.holding_days < 1:
            raise ValueError(f"holding_days must be >= 1, got {self.holding_days}")
        if not 0.0 < self.signal_strength <= 1.0:
            raise ValueError(
                f"signal_strength must be in (0, 1], got {self.signal_strength}"
            )

    def _is_chinext_or_star(self, code: str) -> bool:
        """Heuristic: ChiNext codes start with '30', STAR with '688'."""
        stripped = code.split(".")[0].lstrip("0")
        raw = code.split(".")[0]
        return raw.startswith("30") or raw.startswith("688")

    def _detect_limit_up(
        self, close: pd.Series, code: str
    ) -> pd.Series:
        """Detect limit-up events from daily returns.

        Returns a boolean Series: True on limit-up days.
        """
        ret = close.pct_change()
        threshold = (
            self.chinext_pct if self._is_chinext_or_star(code) else self.main_board_pct
        )
        limit_up = ret >= threshold

        # First bar has NaN return — not a limit-up
        limit_up = limit_up.where(ret.notna(), other=False)
        return limit_up

    def generate(
        self, data_map: Dict[str, pd.DataFrame]
    ) -> Dict[str, pd.Series]:
        """Generate limit-up board signals for each instrument.

        Returns:
            Dict mapping ticker -> pd.Series of signals in [0.0, 1.0].
            Signal is long-only (no short signal for limit-up chasing).
        """
        signals: Dict[str, pd.Series] = {}

        for code, df in data_map.items():
            if "close" not in df.columns:
                continue

            close = df["close"].astype(float)

            # Path 1: Direct limit_up column
            if "limit_up" in df.columns:
                limit_up = df["limit_up"].astype(bool).fillna(False)
            else:
                # Path 2: Detect from return
                limit_up = self._detect_limit_up(close, code)

            # Buy next day after limit-up, hold for N days
            # Shift by 1 to avoid buying on the limit-up day itself (no lookahead)
            trigger = limit_up.shift(1)
            trigger = trigger.where(trigger.notna(), False).astype(bool)

            signal = pd.Series(0.0, index=close.index)
            remaining = 0

            for i in range(len(signal)):
                if trigger.iloc[i]:
                    remaining = self.holding_days

                if remaining > 0:
                    signal.iloc[i] = self.signal_strength
                    remaining -= 1

            # First bar: NaN (no prior data to detect limit-up)
            signal.iloc[0] = np.nan

            # Clamp to [0, 1]
            signal = signal.clip(0.0, 1.0)
            signals[code] = signal

        return signals
