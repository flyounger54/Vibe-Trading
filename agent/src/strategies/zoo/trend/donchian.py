
# ============================================================
# 中文名称: 唐奇安通道突破策略
# 简要说明: 价格突破 N 日最高价做多，跌破 M 日最低价做空/平仓。
#           Richard Donchian 经典通道突破系统，信号值 ∈ [-1, 1]，NaN 安全。
# 典型用途: 中长期趋势跟踪，捕捉通道突破后的趋势延续行情。
# ============================================================
"""Donchian Channel Breakout (trend_donchian).

Price breaking above the N-period high generates a long signal;
breaking below the M-period low generates a short/flat signal.
Between breakouts the most recent signal is held.
"""

from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd

__strategy_meta__ = {
    "id": "trend_donchian",
    "nickname": "唐奇安通道突破",
    "category": "trend",
    "description": (
        "Donchian channel breakout: price above the N-day high channel "
        "triggers a long signal; price below the M-day low channel "
        "triggers a short signal. Signals are held until the opposite breakout."
    ),
    "universe": ["equity_us", "equity_cn", "equity_hk", "crypto", "futures"],
    "frequency": ["1D"],
    "columns_required": ["close", "high", "low"],
    "default_params": {"channel_period": 20, "exit_period": 10},
    "risk_profile": "medium",
    "min_bars": 25,
    "reference": "",
    "factors_used": [],
}


class SignalEngine:
    """唐奇安通道突破信号引擎。"""

    def __init__(self, **params: Any) -> None:
        self.channel_period: int = int(params.get("channel_period", 20))
        self.exit_period: int = int(params.get("exit_period", 10))
        if self.channel_period < 1 or self.exit_period < 1:
            raise ValueError("channel_period and exit_period must be >= 1")

    def generate(
        self, data_map: Dict[str, pd.DataFrame]
    ) -> Dict[str, pd.Series]:
        """为每个标的生成唐奇安通道突破信号。

        Returns:
            Dict mapping ticker -> pd.Series of signals in [-1.0, 1.0].
        """
        signals: Dict[str, pd.Series] = {}

        for code, df in data_map.items():
            required = {"close", "high", "low"}
            if not required.issubset(df.columns):
                continue

            close: pd.Series = df["close"].astype(float)
            high: pd.Series = df["high"].astype(float)
            low: pd.Series = df["low"].astype(float)

            # 入场通道: N 日最高价 / 最低价 (不含当日，避免前视偏差)
            upper_channel = high.shift(1).rolling(
                window=self.channel_period, min_periods=self.channel_period
            ).max()
            lower_channel = low.shift(1).rolling(
                window=self.channel_period, min_periods=self.channel_period
            ).min()

            # 出场通道: M 日最低价 / 最高价
            exit_lower = low.shift(1).rolling(
                window=self.exit_period, min_periods=self.exit_period
            ).min()
            exit_upper = high.shift(1).rolling(
                window=self.exit_period, min_periods=self.exit_period
            ).max()

            n = len(close)
            sig = np.full(n, np.nan)
            pos = 0.0  # 当前仓位状态: +1 多头, -1 空头, 0 空仓

            for i in range(n):
                # 通道数据不足时跳过
                if np.isnan(upper_channel.iat[i]) or np.isnan(lower_channel.iat[i]):
                    continue

                c = close.iat[i]

                if pos <= 0 and c > upper_channel.iat[i]:
                    # 突破上轨 → 做多
                    pos = 1.0
                elif pos >= 0 and c < lower_channel.iat[i]:
                    # 突破下轨 → 做空
                    pos = -1.0
                elif pos > 0 and not np.isnan(exit_lower.iat[i]) and c < exit_lower.iat[i]:
                    # 多头出场
                    pos = 0.0
                elif pos < 0 and not np.isnan(exit_upper.iat[i]) and c > exit_upper.iat[i]:
                    # 空头出场
                    pos = 0.0

                sig[i] = pos

            result = pd.Series(sig, index=close.index)
            signals[code] = result

        return signals
