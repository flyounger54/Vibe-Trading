
# ============================================================
# 中文名称: 海龟交易系统
# 简要说明: 经典海龟交易法则——20 日突破入场、10 日突破出场，信号幅度与
#           ATR 归一化波动率成反比 (1 / ATR_ratio)，实现波动率自适应仓位。
#           信号值 ∈ [-1, 1]，NaN 安全，无前视偏差。
# 典型用途: 期货、加密货币等高波动市场的趋势跟踪与仓位管理。
# ============================================================
"""Turtle Trading System (trend_turtle).

Classic Turtle Trading rules from Curtis Faith's "Way of the Turtle":
- Entry: 20-day breakout (price exceeds prior 20-day high/low).
- Exit: 10-day breakout in the opposite direction.
- Position sizing: signal amplitude ∝ 1 / ATR-normalised volatility,
  clamped to [-1, 1].
"""

from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd

__strategy_meta__ = {
    "id": "trend_turtle",
    "nickname": "海龟交易系统",
    "category": "trend",
    "description": (
        "Classic Turtle Trading system: 20-day breakout entry, 10-day "
        "breakout exit, ATR-based position sizing where signal amplitude "
        "is proportional to 1 / ATR-normalised volatility."
    ),
    "universe": ["futures", "crypto"],
    "frequency": ["1D"],
    "columns_required": ["close", "high", "low"],
    "default_params": {"entry_period": 20, "exit_period": 10, "atr_period": 20},
    "risk_profile": "high",
    "min_bars": 30,
    "reference": "Curtis Faith, Way of the Turtle, 2007",
    "factors_used": ["atr"],
}


def _true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    """计算真实波幅 (True Range)。"""
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr


class SignalEngine:
    """海龟交易系统信号引擎。"""

    def __init__(self, **params: Any) -> None:
        self.entry_period: int = int(params.get("entry_period", 20))
        self.exit_period: int = int(params.get("exit_period", 10))
        self.atr_period: int = int(params.get("atr_period", 20))
        if self.entry_period < 1 or self.exit_period < 1 or self.atr_period < 1:
            raise ValueError("all period parameters must be >= 1")

    def generate(
        self, data_map: Dict[str, pd.DataFrame]
    ) -> Dict[str, pd.Series]:
        """为每个标的生成海龟交易信号。

        信号方向由突破决定，信号幅度由 ATR 归一化波动率的倒数调节。
        低波动 → 信号幅度大 (加仓); 高波动 → 信号幅度小 (减仓)。

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

            # ATR 计算
            tr = _true_range(high, low, close)
            atr = tr.rolling(window=self.atr_period, min_periods=self.atr_period).mean()

            # ATR 归一化比率: ATR / close，衡量相对波动率
            atr_ratio = atr / close.where(close.abs() > 1e-12, other=np.nan)

            # 入场通道 (不含当日)
            entry_high = high.shift(1).rolling(
                window=self.entry_period, min_periods=self.entry_period
            ).max()
            entry_low = low.shift(1).rolling(
                window=self.entry_period, min_periods=self.entry_period
            ).min()

            # 出场通道 (不含当日)
            exit_low = low.shift(1).rolling(
                window=self.exit_period, min_periods=self.exit_period
            ).min()
            exit_high = high.shift(1).rolling(
                window=self.exit_period, min_periods=self.exit_period
            ).max()

            n = len(close)
            sig = np.full(n, np.nan)
            direction = 0.0  # +1 多头, -1 空头, 0 空仓

            for i in range(n):
                # 通道或 ATR 数据不足时跳过
                if np.isnan(entry_high.iat[i]) or np.isnan(entry_low.iat[i]):
                    continue
                if np.isnan(atr_ratio.iat[i]) or atr_ratio.iat[i] <= 0:
                    sig[i] = 0.0
                    continue

                c = close.iat[i]

                # 入场判断
                if direction <= 0 and c > entry_high.iat[i]:
                    direction = 1.0
                elif direction >= 0 and c < entry_low.iat[i]:
                    direction = -1.0
                # 出场判断
                elif direction > 0 and not np.isnan(exit_low.iat[i]) and c < exit_low.iat[i]:
                    direction = 0.0
                elif direction < 0 and not np.isnan(exit_high.iat[i]) and c > exit_high.iat[i]:
                    direction = 0.0

                # 信号幅度: 波动率越低 → 幅度越大 (海龟仓位管理)
                # 使用 ATR 比率的中位数作为基准进行归一化
                # amplitude = clamp(median_atr_ratio / current_atr_ratio, 0, 1)
                # 此处先记录方向和 ATR 比率，后续向量化计算幅度
                sig[i] = direction * atr_ratio.iat[i]

            result = pd.Series(sig, index=close.index)

            # 向量化幅度归一化: 用滚动中位数作为 ATR 基准
            abs_result = result.abs()
            # 仅对有仓位的区间计算幅度
            has_position = abs_result > 1e-12

            if has_position.any():
                # ATR ratio 的滚动中位数作为 "正常" 波动率基准
                median_atr = atr_ratio.rolling(
                    window=self.atr_period * 2, min_periods=self.atr_period
                ).median()

                # amplitude = median / current，低波动放大，高波动缩小
                amplitude = (median_atr / atr_ratio).clip(lower=0.0, upper=1.0)
                amplitude = amplitude.fillna(0.0)

                # 重建信号: 方向 × 幅度
                direction_series = np.sign(result)
                result = direction_series * amplitude

                # 无仓位区间保持 0
                result = result.where(has_position, other=0.0)

            # 保留原始 NaN (数据不足区间)
            warmup_mask = entry_high.isna() | entry_low.isna() | atr.isna()
            result = result.where(~warmup_mask, other=np.nan)

            signals[code] = result

        return signals
