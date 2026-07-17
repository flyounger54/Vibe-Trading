"""A-share data providers: mootdx (TCP) + Tencent Finance (HTTP).

Extracted from a-stock-data SKILL.md v3.2.3. Both sources are free,
no-auth, and not subject to IP bans — safe for high-frequency calls.

mootdx: TCP binary protocol on port 7709, provides OHLCV klines at all
intervals (1m through monthly). Uses ``tdx_client()`` with 3-level
fallback to work around the mootdx 0.11.x BESTIP bug.

Tencent Finance: HTTP GET via ifzq.gtimg.cn, provides forward-adjusted
daily klines. Used as fallback when mootdx TCP is unreachable (e.g.
overseas networks where port 7709 is blocked).
"""

from __future__ import annotations

import json
import logging
import socket
import urllib.request
from typing import Optional

logger = logging.getLogger(__name__)

# ── mootdx TCP servers (tested 2026-06, sorted by latency) ──────────

_TDX_SERVERS = [
    ("119.97.185.59", 7709),
    ("124.70.133.119", 7709),
    ("116.205.183.150", 7709),
    ("123.60.73.44", 7709),
    ("116.205.163.254", 7709),
    ("121.36.225.169", 7709),
    ("123.60.70.228", 7709),
    ("124.71.9.153", 7709),
    ("110.41.147.114", 7709),
    ("124.71.187.122", 7709),
]


def _probe(ip: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except Exception:
        return False


def tdx_client(market: str = "std"):
    """Create a mootdx client with 3-level fallback for the 0.11.x BESTIP bug.

    1) TCP-probe built-in server list, use first reachable;
    2) Fall back to mootdx bestip auto-discovery;
    3) Fall back to bare factory (works when config already has a valid IP);
    4) Raise RuntimeError with clear message.
    """
    from mootdx.quotes import Quotes

    for ip, port in _TDX_SERVERS:
        if _probe(ip, port):
            return Quotes.factory(market=market, server=(ip, port))
    try:
        return Quotes.factory(market=market, bestip=True)
    except Exception:
        pass
    try:
        return Quotes.factory(market=market)
    except Exception as e:
        raise RuntimeError(
            "所有 mootdx 服务器均不可达。海外网络通常全部超时（TCP 7709），"
            "请走国内代理或更新 _TDX_SERVERS 列表。原始错误：%s" % e
        )


def get_prefix(code: str) -> str:
    """6-digit A-share code -> market prefix (sh/sz/bj)."""
    if code.startswith(("6", "9")):
        return "sh"
    elif code.startswith("8"):
        return "bj"
    return "sz"


# ── mootdx kline ────────────────────────────────────────────────────

MOOTDX_FREQ: dict[str, int] = {
    "1D": 4, "1W": 5, "1M": 6,
    "1m": 8, "5m": 0, "15m": 1, "30m": 2, "1H": 3,
}
_BARS_PAGE = 800
_MAX_PAGES = 25


def mootdx_kline(
    symbol: str,
    start_date: str,
    end_date: str,
    interval: str = "1D",
) -> list[dict]:
    """Fetch A-share klines via mootdx TCP.

    Args:
        symbol: 6-digit code (e.g. "600519").
        start_date: YYYY-MM-DD.
        end_date: YYYY-MM-DD.
        interval: 1D/1W/1M/1m/5m/15m/30m/1H.

    Returns:
        List of {date, open, high, low, close, volume} dicts.
    """
    import pandas as pd

    freq = MOOTDX_FREQ.get(interval)
    if freq is None:
        raise ValueError(f"Unsupported mootdx interval: {interval!r}")

    client = tdx_client()

    if interval == "1D":
        df = client.get_k_data(code=symbol, start_date=start_date, end_date=end_date)
        if df is None or df.empty:
            return []
        result = []
        for idx, row in df.iterrows():
            result.append({
                "date": str(idx),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row.get("vol", row.get("volume", 0))),
            })
        return result

    start_ts = pd.Timestamp(start_date)
    end_ts = pd.Timestamp(end_date) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
    chunks: list[pd.DataFrame] = []
    for page in range(_MAX_PAGES):
        df = client.bars(
            symbol=symbol,
            frequency=freq,
            start=page * _BARS_PAGE,
            offset=_BARS_PAGE,
        )
        if df is None or df.empty:
            break
        chunks.append(df)
        first_dt = pd.to_datetime(df["datetime"].iloc[0])
        if first_dt <= start_ts:
            break

    if not chunks:
        return []

    combined = pd.concat(chunks, ignore_index=False)
    if "datetime" in combined.columns:
        combined["dt"] = pd.to_datetime(combined["datetime"])
    else:
        combined["dt"] = pd.to_datetime(combined.index)
    combined = combined[(combined["dt"] >= start_ts) & (combined["dt"] <= end_ts)]

    result = []
    for _, row in combined.iterrows():
        result.append({
            "date": str(row["dt"]),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": float(row.get("vol", row.get("volume", 0))),
        })
    return result


# ── Tencent Finance kline ───────────────────────────────────────────

_TENCENT_KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"


def tencent_kline(
    symbol: str,
    start_date: str,
    end_date: str,
) -> list[dict]:
    """Fetch A-share daily klines via Tencent Finance HTTP API.

    Args:
        symbol: 6-digit code (e.g. "600519").
        start_date: YYYY-MM-DD.
        end_date: YYYY-MM-DD.

    Returns:
        List of {date, open, high, low, close, volume} dicts.
    """
    prefix = get_prefix(symbol)
    tencent_code = f"{prefix}{symbol}"
    url = f"{_TENCENT_KLINE_URL}?param={tencent_code},day,{start_date},{end_date},500,qfq"

    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://web.ifzq.gtimg.cn/",
    })
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read().decode("utf-8")

    data = json.loads(raw)
    stock_data = data.get("data", {})
    if not stock_data:
        return []

    stock_key = next(iter(stock_data), None)
    if not stock_key:
        return []

    klines = stock_data[stock_key].get("qfqday") or stock_data[stock_key].get("day")
    if not klines:
        return []

    result = []
    for k in klines:
        if len(k) >= 6:
            result.append({
                "date": k[0],
                "open": float(k[1]),
                "high": float(k[3]),
                "low": float(k[4]),
                "close": float(k[2]),
                "volume": float(k[5]),
            })
    return result


def normalize_code(code: str) -> str:
    """Normalize A-share code to bare 6-digit form.

    Accepts: 688017, SH688017, sh688017, 688017.SH, 688017.sh, etc.
    Returns: 688017
    """
    c = code.strip().upper()
    for prefix in ("SH", "SZ", "BJ"):
        if c.startswith(prefix) and len(c) > 2:
            return c[2:]
    if "." in c:
        return c.split(".")[0]
    return c
