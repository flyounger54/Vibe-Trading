
# ============================================================
# 中文名称: 资金费率套利策略
# 简要说明: 利用永续合约 funding_rate 进行套利：费率极负时做多（空头付费），
#           费率极正时做空。无 funding_rate 列时回退到价格均值回归估算。
# 典型用途: 加密货币永续合约的资金费率套利。
# ============================================================
"""Crypto Funding Rate Arbitrage (crypto_funding_arb).

When the perpetual funding rate is very negative (shorts pay longs), go
long spot. When very positive (longs pay shorts), go short. Falls back
to basis estimation via price mean-reversion if no funding_rate column.
"""

from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd

__strategy_meta__ = {
    "id": "crypto_funding_arb",
    "nickname": "资金费率套利",
    "category": "crypto",
    "description": (
        "Go long when funding rate is very negative (shorts paying longs), "
        "go short when very positive. Falls back to price mean-reversion "
        "basis estimation if funding_rate column is unavailable."
    ),
    "universe": ["crypto"],
    "frequency": ["1D"],
    "columns_required": ["close"],
    "default_params": {"entry_threshold": 0.01, "exit_threshold": 0.005, "ma_period": 20},
    "risk_profile": "medium",
    "min_bars": 25,
    "reference": "永续合约资金费率套利",
    "factors_used": [],
}


class SignalEngine:
    """资金费率套利信号引擎。"""

    def __init__(self, **params: Any) -> None:
        self.entry_threshold: float = float(params.get("entry_threshold", 0.01))
        self.exit_threshold: float = float(params.get("exit_threshold", 0.005))
        self.ma_period: int = int(params.get("ma_period", 20))
        if self.entry_threshold <= 0:
            raise ValueError(
                f"entry_threshold must be > 0, got {self.entry_threshold}"
            )
        if self.exit_threshold <= 0:
            raise ValueError(
                f"exit_threshold must be > 0, got {self.exit_threshold}"
            )
        if self.ma_period < 2:
            raise ValueError(f"ma_period must be >= 2, got {self.ma_period}")

    def _signal_from_funding(self, funding: pd.Series) -> pd.Series:
        """Generate signal directly from funding rate column."""
        funding = funding.astype(float)

        # Signal strength proportional to how far funding exceeds threshold
        # Negative funding -> long (+), positive funding -> short (-)
        raw = pd.Series(0.0, index=funding.index)

        # Entry: funding below -entry_threshold -> go long
        long_mask = funding < -self.entry_threshold
        long_strength = ((-funding - self.entry_threshold) / self.entry_threshold).clip(
            upper=1.0
        )
        raw = raw.where(~long_mask, long_strength)

        # Entry: funding above +entry_threshold -> go short
        short_mask = funding > self.entry_threshold
        short_strength = ((funding - self.entry_threshold) / self.entry_threshold).clip(
            upper=1.0
        )
        raw = raw.where(~short_mask, -short_strength)

        # Between exit thresholds: fade toward zero (hold reduced position)
        mid_mask = funding.abs() <= self.exit_threshold
        raw = raw.where(~mid_mask, 0.0)

        raw = raw.where(funding.notna(), other=np.nan)
        return raw.clip(-1.0, 1.0)

    def _signal_from_basis(self, close: pd.Series) -> pd.Series:
        """Fallback: mean-reversion basis estimation from price."""
        ma = close.rolling(window=self.ma_period, min_periods=self.ma_period).mean()

        # Basis proxy: deviation from MA as fraction of MA
        basis = (close - ma) / ma.replace(0, np.nan)

        # Invert: when price is far above MA (positive basis), go short;
        # when far below MA (negative basis), go long — mimicking funding arb
        raw = pd.Series(0.0, index=close.index)

        long_mask = basis < -self.entry_threshold
        long_strength = ((-basis - self.entry_threshold) / self.entry_threshold).clip(
            upper=1.0
        )
        raw = raw.where(~long_mask, long_strength)

        short_mask = basis > self.entry_threshold
        short_strength = ((basis - self.entry_threshold) / self.entry_threshold).clip(
            upper=1.0
        )
        raw = raw.where(~short_mask, -short_strength)

        mid_mask = basis.abs() <= self.exit_threshold
        raw = raw.where(~mid_mask, 0.0)

        raw = raw.where(ma.notna(), other=np.nan)
        return raw.clip(-1.0, 1.0)

    def generate(
        self, data_map: Dict[str, pd.DataFrame]
    ) -> Dict[str, pd.Series]:
        """Generate funding-rate arbitrage signals.

        Returns:
            Dict mapping ticker -> pd.Series of signals in [-1.0, 1.0].
        """
        signals: Dict[str, pd.Series] = {}

        for code, df in data_map.items():
            if "close" not in df.columns:
                continue

            if "funding_rate" in df.columns:
                signals[code] = self._signal_from_funding(df["funding_rate"])
            else:
                close = df["close"].astype(float)
                signals[code] = self._signal_from_basis(close)

        return signals
