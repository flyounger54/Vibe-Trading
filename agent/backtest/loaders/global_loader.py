"""Global stock loader: Yahoo Finance + Sina Finance with internal fallback.

Wraps the data functions from global-stock-data into Vibe-Trading's
DataLoaderProtocol. Yahoo Finance v8 chart API covers both US and HK equities
at all intervals. Sina Finance serves as a US-only daily fallback.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

import pandas as pd

from backtest.loaders.base import cached_loader_fetch, validate_date_range
from backtest.loaders.providers.global_stock import (
    normalize_sina_ticker,
    normalize_yahoo_symbol,
    stock_kline_yahoo,
    us_stock_kline_sina,
)
from backtest.loaders.registry import register

logger = logging.getLogger(__name__)


def _is_us(code: str) -> bool:
    return code.upper().endswith(".US")


def _is_hk(code: str) -> bool:
    return code.upper().endswith(".HK")


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
    """US + HK equity OHLCV loader backed by Yahoo + Sina Finance."""

    name = "global"
    markets = {"us_equity", "hk_equity"}
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
            if not (_is_us(code) or _is_hk(code)):
                logger.debug("global: skipping non-US/HK symbol %s", code)
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
                logger.warning("global failed for %s: %s", code, exc)
        return result

    def _fetch_one(
        self, code: str, start_date: str, end_date: str, interval: str,
    ) -> Optional[pd.DataFrame]:
        yahoo_symbol = normalize_yahoo_symbol(code)

        # Try Yahoo first (US + HK, all intervals)
        try:
            rows = stock_kline_yahoo(yahoo_symbol, start_date, end_date, interval)
            if rows:
                return _rows_to_dataframe(rows)
            logger.debug("global: yahoo returned empty for %s", code)
        except Exception as exc:
            logger.debug("global: yahoo failed for %s (%s)", code, exc)

        # Sina fallback (US daily only)
        if _is_us(code) and interval in ("1D", "1d"):
            try:
                ticker = normalize_sina_ticker(code)
                rows = us_stock_kline_sina(ticker, start_date, end_date)
                if rows:
                    return _rows_to_dataframe(rows)
            except Exception as exc:
                logger.warning("global: sina also failed for %s: %s", code, exc)

        return None
