"""Global stock data providers: Yahoo Finance + Sina Finance.

Extracted from global-stock-data SKILL.md v1.0.1.

Yahoo Finance v8 chart API: supports US + HK equities at all intervals
(1d/1wk/1mo/5m/15m/1h). Zero crumb required for chart endpoint.

Sina Finance: US stock daily klines only, can go back to 1984.
Used as fallback when Yahoo is unavailable.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone

import requests

from backtest.loaders import yahoo_client

logger = logging.getLogger(__name__)

# ── Yahoo Finance kline (shared throttled client) ───────────────────

_YAHOO_INTERVAL_MAP: dict[str, str] = {
    "1D": "1d", "1W": "1wk", "1M": "1mo",
    "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m", "1H": "1h",
    "1d": "1d", "1wk": "1wk", "1mo": "1mo", "1h": "1h",
}

def stock_kline_yahoo(
    symbol: str,
    start_date: str,
    end_date: str,
    interval: str = "1d",
) -> list[dict]:
    """Fetch klines via Yahoo Finance v8 chart API.

    Args:
        symbol: Yahoo ticker — "AAPL" (US) or "0700.HK" (HK).
        start_date: YYYY-MM-DD.
        end_date: YYYY-MM-DD.
        interval: 1d/1wk/1mo/5m/15m/1h (or Vibe-Trading format 1D/1W etc.).

    Returns:
        List of {date, open, high, low, close, volume} dicts.
    """
    yahoo_interval = _YAHOO_INTERVAL_MAP.get(interval, interval)

    start = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_exclusive = (
        datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        + timedelta(days=1)
    )
    rows = yahoo_client.get_chart(
        symbol,
        interval=yahoo_interval,
        period1=int(start.timestamp()),
        period2=int(end_exclusive.timestamp()),
    )

    is_intraday = yahoo_interval in ("1m", "5m", "15m", "30m", "1h")
    result = []
    for row in rows:
        ts = row["trade_date"]
        bar_time = datetime.fromtimestamp(ts, tz=timezone.utc)
        date_str = (
            bar_time.strftime("%Y-%m-%d %H:%M")
            if is_intraday
            else bar_time.strftime("%Y-%m-%d")
        )
        result.append({
            "date": date_str,
            "open": round(float(row["open"]), 2),
            "high": round(float(row["high"]), 2),
            "low": round(float(row["low"]), 2),
            "close": round(float(row["close"]), 2),
            "volume": int(row.get("volume") or 0),
        })
    return result


# ── Sina Finance US stock kline ─────────────────────────────────────

def us_stock_kline_sina(
    ticker: str,
    start_date: str,
    end_date: str,
    num: int = 500,
) -> list[dict]:
    """Fetch US stock daily klines via Sina Finance (back to 1984).

    Args:
        ticker: Pure ticker like "AAPL", "TSLA".
        start_date: YYYY-MM-DD (for filtering).
        end_date: YYYY-MM-DD (for filtering).
        num: Max number of bars to request.

    Returns:
        List of {date, open, high, low, close, volume} dicts within date range.
    """
    url = "https://stock.finance.sina.com.cn/usstock/api/jsonp.php/var/US_MinKService.getDailyK"
    params = {"symbol": ticker.upper(), "num": num}
    r = requests.get(
        url, params=params,
        headers={"Referer": "https://finance.sina.com.cn/"},
        timeout=15,
    )

    m = re.search(r"\((\[.+\])\)", r.text)
    if not m:
        return []

    items = json.loads(m.group(1))
    result = []
    for item in items:
        d = item.get("d", "")
        if d < start_date or d > end_date:
            continue
        result.append({
            "date": d,
            "open": float(item.get("o", 0)),
            "high": float(item.get("h", 0)),
            "low": float(item.get("l", 0)),
            "close": float(item.get("c", 0)),
            "volume": int(item.get("v", 0)),
        })
    return result


def normalize_yahoo_symbol(code: str) -> str:
    """Convert Vibe-Trading symbol format to Yahoo format.

    AAPL.US -> AAPL
    00700.HK -> 0700.HK
    """
    upper = code.upper()
    if upper.endswith(".US"):
        return upper[:-3]
    if upper.endswith(".HK"):
        numeric = upper[:-3].lstrip("0")
        padded = numeric.zfill(4)
        return f"{padded}.HK"
    return code


def normalize_sina_ticker(code: str) -> str:
    """Convert Vibe-Trading symbol to Sina ticker (bare US ticker)."""
    upper = code.upper()
    if upper.endswith(".US"):
        return upper[:-3]
    return code
