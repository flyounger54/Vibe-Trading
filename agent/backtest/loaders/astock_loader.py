"""A-share loader: TDX TCP + Tencent HTTP with explicit price semantics.

Wraps the battle-tested data functions from a-stock-data into Vibe-Trading's
DataLoaderProtocol. Raw requests use tdxpy over TDX TCP; explicit qfq requests
use Tencent Finance. The two paths never silently substitute for one another.
"""

from __future__ import annotations

import logging
import importlib.util
from typing import Dict, List, Optional

import pandas as pd

from backtest.loaders.base import cached_loader_fetch, validate_date_range
from backtest.loaders.providers.astock import (
    MOOTDX_FREQ,
    mootdx_kline,
    normalize_code,
    tencent_kline,
)
from backtest.loaders.registry import register
from backtest.loaders.platform import Adjustment, BAR_SCHEMA_VERSION, ProviderCapabilities

logger = logging.getLogger(__name__)


def _is_a_share(code: str) -> bool:
    upper = code.upper()
    if upper.endswith((".SH", ".SZ", ".BJ")):
        return True
    return len(code) == 6 and code.isdigit()


def _rows_to_dataframe(rows: list[dict]) -> Optional[pd.DataFrame]:
    if not rows:
        return None
    df = pd.DataFrame(rows)
    df["trade_date"] = pd.to_datetime(df["date"])
    df = df.set_index("trade_date").sort_index()
    for col in ("open", "high", "low", "close", "volume"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df[["open", "high", "low", "close", "volume"]].dropna(
        subset=["open", "high", "low", "close"]
    )
    return df if not df.empty else None


@register
class DataLoader:
    """A-share OHLCV loader backed by TDX TCP + Tencent Finance."""

    name = "astock"
    markets = {"a_share"}
    requires_auth = False
    provider_version = "astock-mootdx-tencent-v1"
    capabilities = ProviderCapabilities(
        intervals=frozenset(MOOTDX_FREQ),
        adjustments=frozenset({Adjustment.NONE, Adjustment.QFQ}),
        supports_pagination=True,
    )

    def is_available(self) -> bool:
        # Tencent uses only the Python standard library; mootdx is optional and
        # its real status is exposed separately instead of being hidden.
        return callable(tencent_kline)

    @staticmethod
    def availability_diagnostics() -> dict[str, object]:
        return {
            "available": callable(tencent_kline),
            "tdxpy_installed": importlib.util.find_spec("tdxpy") is not None,
            "mootdx_compatibility": "not-used: mootdx 0.11.7 conflicts with httpx>=0.28",
            "tencent_transport": "stdlib-urllib",
        }

    def fetch(
        self,
        codes: List[str],
        start_date: str,
        end_date: str,
        *,
        interval: str = "1D",
        fields: Optional[List[str]] = None,
        adjustment: str = "none",
    ) -> Dict[str, pd.DataFrame]:
        validate_date_range(start_date, end_date)
        normalized_adjustment = Adjustment(adjustment)
        if not self.capabilities.supports(interval=interval, adjustment=normalized_adjustment):
            raise ValueError(f"astock does not support interval={interval}, adjustment={adjustment}")

        result: Dict[str, pd.DataFrame] = {}
        for code in codes:
            if not _is_a_share(code):
                logger.debug("astock: skipping non-A-share symbol %s", code)
                continue
            try:
                df = cached_loader_fetch(
                    source=self.name,
                    symbol=code,
                    timeframe=interval,
                    start_date=start_date,
                    end_date=end_date,
                    fields=None,
                    schema_version=BAR_SCHEMA_VERSION,
                    provider_version=self.provider_version,
                    adjustment=normalized_adjustment.value,
                    fetch=lambda c=code: self._fetch_one(
                        c, start_date, end_date, interval, normalized_adjustment
                    ),
                )
                if df is not None and not df.empty:
                    result[code] = df
            except Exception as exc:
                logger.warning("astock failed for %s: %s", code, exc)
        return result

    def _fetch_one(
        self, code: str, start_date: str, end_date: str, interval: str,
        adjustment: Adjustment,
    ) -> Optional[pd.DataFrame]:
        symbol = normalize_code(code)

        # Raw prices and Tencent's forward-adjusted prices are intentionally
        # separate paths. Internal fallback must never change adjustment.
        if adjustment is Adjustment.NONE:
            try:
                rows = mootdx_kline(symbol, start_date, end_date, interval)
                if rows:
                    return _rows_to_dataframe(rows)
            except Exception as exc:
                logger.debug("astock: mootdx failed for %s: %s", symbol, exc)
            return None

        if adjustment is Adjustment.QFQ and interval in ("1D", "1W", "1M"):
            try:
                rows = tencent_kline(symbol, start_date, end_date)
                if rows:
                    return _rows_to_dataframe(rows)
            except Exception as exc:
                logger.warning("astock: Tencent qfq failed for %s: %s", symbol, exc)

        return None
