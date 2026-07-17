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
from datetime import datetime
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# ── Yahoo Finance session + crumb manager ───────────────────────────

_yahoo_session: Optional[requests.Session] = None

_YAHOO_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


def get_yahoo_session() -> requests.Session:
    """Get a Yahoo Finance session with cached cookie+crumb."""
    global _yahoo_session
    if _yahoo_session and hasattr(_yahoo_session, "_crumb"):
        return _yahoo_session

    s = requests.Session()
    s.headers["User-Agent"] = _YAHOO_UA

    s.get("https://fc.yahoo.com", timeout=10)

    r = s.get("https://query2.finance.yahoo.com/v1/test/getcrumb", timeout=10)
    r.raise_for_status()
    s._crumb = r.text  # type: ignore[attr-defined]

    _yahoo_session = s
    return s


# ── Yahoo Finance kline (v8 chart API, zero crumb) ──────────────────

_YAHOO_INTERVAL_MAP: dict[str, str] = {
    "1D": "1d", "1W": "1wk", "1M": "1mo",
    "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m", "1H": "1h",
    "1d": "1d", "1wk": "1wk", "1mo": "1mo", "1h": "1h",
}

_YAHOO_RANGE_MAP: dict[str, str] = {
    "1d": "5d", "1wk": "2y", "1mo": "5y",
    "1m": "7d", "5m": "60d", "15m": "60d", "30m": "60d", "1h": "2y",
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

    from datetime import timezone
    start_ts = int(datetime.strptime(start_date, "%Y-%m-%d").replace(
        tzinfo=timezone.utc).timestamp())
    end_ts = int(datetime.strptime(end_date, "%Y-%m-%d").replace(
        hour=23, minute=59, second=59, tzinfo=timezone.utc).timestamp())

    url = f"https://query2.finance.yahoo.com/v8/finance/chart/{symbol}"
    params = {
        "interval": yahoo_interval,
        "period1": start_ts,
        "period2": end_ts,
    }
    r = requests.get(url, params=params, headers={"User-Agent": _YAHOO_UA}, timeout=15)
    r.raise_for_status()

    d = r.json()
    chart = d.get("chart", {}).get("result", [{}])[0]
    timestamps = chart.get("timestamp", [])
    quote = chart.get("indicators", {}).get("quote", [{}])[0]

    if not timestamps:
        return []

    is_intraday = yahoo_interval in ("1m", "5m", "15m", "30m", "1h")
    result = []
    for i, ts in enumerate(timestamps):
        o = quote.get("open", [None] * len(timestamps))[i]
        h = quote.get("high", [None] * len(timestamps))[i]
        lo = quote.get("low", [None] * len(timestamps))[i]
        c = quote.get("close", [None] * len(timestamps))[i]
        v = quote.get("volume", [None] * len(timestamps))[i]
        if o is None or c is None:
            continue
        date_str = (
            datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
            if is_intraday
            else datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
        )
        result.append({
            "date": date_str,
            "open": round(o, 2),
            "high": round(h, 2) if h else 0,
            "low": round(lo, 2) if lo else 0,
            "close": round(c, 2),
            "volume": int(v) if v else 0,
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
