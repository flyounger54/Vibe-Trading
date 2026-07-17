"""A-share loader: mootdx TCP + Tencent HTTP with internal fallback.

Wraps the battle-tested data functions from a-stock-data into Vibe-Trading's
DataLoaderProtocol. Prioritizes mootdx (TCP, no IP ban, all intervals) and
falls back to Tencent Finance (HTTP, no IP ban, daily only) when TCP is
unreachable (e.g. overseas networks blocking port 7709).
"""

from __future__ import annotations

import logging
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
    """A-share OHLCV loader backed by mootdx + Tencent Finance."""

    name = "astock"
    markets = {"a_share"}
    requires_auth = False

    def is_available(self) -> bool:
        return True

    def fetch(
        self,
        codes: List[str],
        start_date: str,
        end_date: str,
        *,
        interval: str = "1D",
        fields: Optional[List[str]] = None,
    ) -> Dict[str, pd.DataFrame]:
        validate_date_range(start_date, end_date)

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
                    fetch=lambda c=code: self._fetch_one(c, start_date, end_date, interval),
                )
                if df is not None and not df.empty:
                    result[code] = df
            except Exception as exc:
                logger.warning("astock failed for %s: %s", code, exc)
        return result

    def _fetch_one(
        self, code: str, start_date: str, end_date: str, interval: str,
    ) -> Optional[pd.DataFrame]:
        symbol = normalize_code(code)

        if interval not in MOOTDX_FREQ and interval != "1D":
            logger.warning("astock: unsupported interval %s, falling back to 1D", interval)
            interval = "1D"

        # Try mootdx first (TCP, all intervals)
        try:
            rows = mootdx_kline(symbol, start_date, end_date, interval)
            if rows:
                return _rows_to_dataframe(rows)
            logger.debug("astock: mootdx returned empty for %s, trying tencent", symbol)
        except Exception as exc:
            logger.debug("astock: mootdx failed for %s (%s), trying tencent", symbol, exc)

        # Tencent fallback (HTTP, daily only)
        if interval in ("1D", "1W", "1M"):
            try:
                rows = tencent_kline(symbol, start_date, end_date)
                if rows:
                    return _rows_to_dataframe(rows)
            except Exception as exc:
                logger.warning("astock: tencent also failed for %s: %s", symbol, exc)

        return None
