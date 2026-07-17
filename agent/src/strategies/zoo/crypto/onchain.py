
# ============================================================
# 中文名称: 链上指标交易策略
# 简要说明: 通过链上指标（活跃地址、NVT 比率）或 OHLCV 代理指标生成信号。
#           结合成交量动量、波动率压缩和量价背离三个维度，综合评分后
#           映射到 [-1, 1]。
# 典型用途: 加密货币基于链上数据与量价关系的中期趋势判断。
# ============================================================
"""Crypto On-Chain Indicator Trading (crypto_onchain).

Combine on-chain indicators (active addresses, NVT ratio) with OHLCV-based
proxies: volume momentum (accumulation), realized volatility compression
(quiet before move), and price-volume divergence (distribution warning).
Falls back to OHLCV proxies when on-chain columns are unavailable.
"""

from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd

__strategy_meta__ = {
    "id": "crypto_onchain",
    "nickname": "链上指标交易",
    "category": "crypto",
    "description": (
        "On-chain indicator trading: combine volume momentum, realized "
        "volatility compression, and price-volume divergence. Uses "
        "active_addresses / nvt_ratio when available; otherwise OHLCV proxies."
    ),
    "universe": ["crypto"],
    "frequency": ["1D"],
    "columns_required": ["close", "volume"],
    "default_params": {"vol_lookback": 20, "volume_lookback": 14, "divergence_lookback": 10},
    "risk_profile": "high",
    "min_bars": 25,
    "reference": "链上数据分析, 已有 onchain-analysis skill",
    "factors_used": [],
}


class SignalEngine:
    """链上指标交易信号引擎。"""

    def __init__(self, **params: Any) -> None:
        self.vol_lookback: int = int(params.get("vol_lookback", 20))
        self.volume_lookback: int = int(params.get("volume_lookback", 14))
        self.divergence_lookback: int = int(params.get("divergence_lookback", 10))
        if self.vol_lookback < 2:
            raise ValueError(f"vol_lookback must be >= 2, got {self.vol_lookback}")
        if self.volume_lookback < 2:
            raise ValueError(
                f"volume_lookback must be >= 2, got {self.volume_lookback}"
            )
        if self.divergence_lookback < 2:
            raise ValueError(
                f"divergence_lookback must be >= 2, got {self.divergence_lookback}"
            )

    @staticmethod
    def _normalize_to_range(series: pd.Series) -> pd.Series:
        """Normalize a series to [-1, 1] using tanh-like rescaling.

        Uses 2 * (rank_pct - 0.5) within a rolling window equivalent,
        then clips to [-1, 1]. NaN-safe.
        """
        ranked = series.rank(pct=True)
        # Map [0, 1] percentile to [-1, 1]
        normalized = 2.0 * ranked - 1.0
        return normalized.where(series.notna(), other=np.nan)

    def _volume_momentum_score(self, volume: pd.Series) -> pd.Series:
        """Volume momentum: rising volume = accumulation, falling = distribution.

        Compute the ratio of short-term volume MA to long-term volume MA.
        Ratio > 1 means increasing volume (bullish accumulation).
        Ratio < 1 means decreasing volume (bearish distribution).
        Map to [-1, 1].
        """
        short_window = max(self.volume_lookback // 3, 2)
        long_window = self.volume_lookback

        vol_short_ma = volume.rolling(
            window=short_window, min_periods=short_window
        ).mean()
        vol_long_ma = volume.rolling(
            window=long_window, min_periods=long_window
        ).mean()

        # Ratio of short to long volume MA; NaN-safe division
        ratio = vol_short_ma / vol_long_ma.replace(0, np.nan)

        # Center around 1.0: ratio > 1 -> positive, ratio < 1 -> negative
        # Use log to symmetrize: log(ratio) is 0 at ratio=1
        log_ratio = np.log(ratio.where(ratio > 0, other=np.nan))

        # Normalize via tanh for smooth [-1, 1] mapping
        # Scale factor: typical log_ratio variance ~ 0.3-0.5
        score = np.tanh(log_ratio * 3.0)

        return score.where(vol_long_ma.notna(), other=np.nan)

    def _vol_compression_score(self, close: pd.Series) -> pd.Series:
        """Realized volatility compression: quiet periods precede big moves.

        Compare short-term realized vol to long-term realized vol.
        When short-term vol is much lower than long-term, a breakout is
        likely (bullish signal). When short-term vol spikes, trend may
        be exhausting (bearish signal).
        """
        returns = close.pct_change()

        short_window = max(self.vol_lookback // 3, 2)
        long_window = self.vol_lookback

        vol_short = returns.rolling(
            window=short_window, min_periods=short_window
        ).std()
        vol_long = returns.rolling(
            window=long_window, min_periods=long_window
        ).std()

        # Vol ratio: short/long. Low ratio = compression = anticipate move
        vol_ratio = vol_short / vol_long.replace(0, np.nan)

        # Invert and center: compression (ratio < 1) -> bullish, expansion -> bearish
        # log(1/ratio) = -log(ratio): positive when compressed, negative when expanded
        log_inv_ratio = -np.log(vol_ratio.where(vol_ratio > 0, other=np.nan))

        score = np.tanh(log_inv_ratio * 2.0)

        return score.where(vol_long.notna(), other=np.nan)

    def _pv_divergence_score(
        self, close: pd.Series, volume: pd.Series
    ) -> pd.Series:
        """Price-volume divergence: price up + volume down = distribution warning.

        Compute rolling correlation between price change direction and
        volume change direction. Positive correlation = confirmation (bullish).
        Negative correlation = divergence (bearish warning).
        """
        price_ret = close.pct_change()
        vol_ret = volume.pct_change()

        # Rolling correlation between price returns and volume changes
        corr = price_ret.rolling(
            window=self.divergence_lookback, min_periods=self.divergence_lookback
        ).corr(vol_ret)

        # Positive correlation -> volume confirms price -> bullish
        # Negative correlation -> volume diverges from price -> bearish
        score = corr.clip(-1.0, 1.0)

        return score.where(corr.notna(), other=np.nan)

    def _onchain_direct_score(self, df: pd.DataFrame) -> pd.Series:
        """Generate score from actual on-chain columns when available.

        active_addresses: rising addresses = growing network = bullish.
        nvt_ratio: low NVT = undervalued relative to transaction volume = bullish;
                   high NVT = overvalued = bearish.
        """
        scores = []

        if "active_addresses" in df.columns:
            addr = df["active_addresses"].astype(float)
            addr_ma = addr.rolling(
                window=self.volume_lookback, min_periods=self.volume_lookback
            ).mean()
            # Rising addresses relative to MA -> bullish
            ratio = addr / addr_ma.replace(0, np.nan)
            log_ratio = np.log(ratio.where(ratio > 0, other=np.nan))
            addr_score = np.tanh(log_ratio * 3.0)
            addr_score = addr_score.where(addr_ma.notna(), other=np.nan)
            scores.append(addr_score)

        if "nvt_ratio" in df.columns:
            nvt = df["nvt_ratio"].astype(float)
            nvt_ma = nvt.rolling(
                window=self.vol_lookback, min_periods=self.vol_lookback
            ).mean()
            # Low NVT relative to MA -> undervalued -> bullish
            # High NVT -> overvalued -> bearish
            ratio = nvt / nvt_ma.replace(0, np.nan)
            log_ratio = np.log(ratio.where(ratio > 0, other=np.nan))
            # Invert: low ratio (undervalued) -> positive signal
            nvt_score = np.tanh(-log_ratio * 2.0)
            nvt_score = nvt_score.where(nvt_ma.notna(), other=np.nan)
            scores.append(nvt_score)

        if not scores:
            return pd.Series(np.nan, index=df.index, dtype=float)

        # Average available on-chain scores
        stacked = pd.concat(scores, axis=1)
        return stacked.mean(axis=1)

    def generate(
        self, data_map: Dict[str, pd.DataFrame]
    ) -> Dict[str, pd.Series]:
        """Generate on-chain indicator signals.

        Returns:
            Dict mapping ticker -> pd.Series of signals in [-1.0, 1.0].
        """
        signals: Dict[str, pd.Series] = {}

        for code, df in data_map.items():
            if "close" not in df.columns or "volume" not in df.columns:
                continue

            close = df["close"].astype(float)
            volume = df["volume"].astype(float)

            # Core OHLCV-proxy scores (always computed)
            vol_mom = self._volume_momentum_score(volume)
            vol_comp = self._vol_compression_score(close)
            pv_div = self._pv_divergence_score(close, volume)

            # Check for actual on-chain data
            has_onchain = (
                "active_addresses" in df.columns or "nvt_ratio" in df.columns
            )

            if has_onchain:
                onchain_score = self._onchain_direct_score(df)
                # Weighted average: on-chain data gets higher weight when present
                # on-chain: 40%, vol_momentum: 20%, vol_compression: 20%, pv_div: 20%
                components = pd.concat(
                    [
                        onchain_score.rename("onchain"),
                        vol_mom.rename("vol_mom"),
                        vol_comp.rename("vol_comp"),
                        pv_div.rename("pv_div"),
                    ],
                    axis=1,
                )
                weights = pd.Series(
                    [0.4, 0.2, 0.2, 0.2],
                    index=["onchain", "vol_mom", "vol_comp", "pv_div"],
                )
                # Weighted mean, NaN-aware: only average non-NaN components
                weighted_sum = (components * weights).sum(axis=1)
                weight_sum = components.notna().astype(float).mul(weights).sum(axis=1)
                composite = weighted_sum / weight_sum.replace(0, np.nan)
            else:
                # Equal-weight average of three OHLCV proxy scores
                components = pd.concat(
                    [vol_mom, vol_comp, pv_div], axis=1
                )
                # NaN-aware mean across the three components
                composite = components.mean(axis=1)

            # Final clip to [-1, 1]
            composite = composite.clip(-1.0, 1.0)

            signals[code] = composite

        return signals
