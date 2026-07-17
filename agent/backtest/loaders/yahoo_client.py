"""Shared Yahoo Finance public-API client.

This module is intentionally a provider client, not a registered OHLCV loader.
The consolidated ``global`` loader and the news/profile/options/search tools use
it as their single Yahoo HTTP boundary.  Every request is throttled through the
shared loader HTTP client and all functions return plain Python objects.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional

import requests

from backtest.loaders._http import (
    resolve_min_interval,
    throttled_get,
    throttled_get_json,
)

logger = logging.getLogger(__name__)

HOST_KEY = "yahoo"

_CHART_BASE = "https://query1.finance.yahoo.com/v8/finance/chart"
_QUOTE_SUMMARY_BASE = "https://query2.finance.yahoo.com/v10/finance/quoteSummary"
_OPTIONS_BASE = "https://query2.finance.yahoo.com/v7/finance/options"
_SEARCH_BASE = "https://query2.finance.yahoo.com/v1/finance/search"
_CRUMB_URL = "https://query2.finance.yahoo.com/v1/test/getcrumb"
_COOKIE_URL = "https://fc.yahoo.com"

_MIN_INTERVAL_ENV = "VIBE_TRADING_YAHOO_MIN_INTERVAL"
_DEFAULT_MIN_INTERVAL_S = 0.6
_QUOTE_FIELDS = ("open", "high", "low", "close", "volume")


def _min_interval() -> float:
    """Resolve the Yahoo request spacing, honoring the env override."""
    return resolve_min_interval(_MIN_INTERVAL_ENV, _DEFAULT_MIN_INTERVAL_S)


def map_symbol(symbol: str) -> str:
    """Translate a Vibe-Trading symbol into Yahoo's ticker convention."""
    cleaned = symbol.strip()
    upper = cleaned.upper()
    if upper.endswith(".US"):
        return cleaned[: -len(".US")]
    if upper.endswith(".HK"):
        base = cleaned[: -len(".HK")]
        digits = base.lstrip("0") or "0"
        return f"{digits.zfill(4)}.HK"
    return cleaned


class _CrumbStore:
    """Process-wide cookie jar and crumb token for gated Yahoo endpoints."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._crumb: Optional[str] = None
        self._cookies: Dict[str, str] = {}

    def get(self, *, force_refresh: bool = False) -> tuple[str, Dict[str, str]]:
        """Return a cached crumb/cookie pair, refreshing when requested."""
        with self._lock:
            if force_refresh or self._crumb is None:
                self._crumb, self._cookies = self._handshake()
            return self._crumb, dict(self._cookies)

    def _handshake(self) -> tuple[str, Dict[str, str]]:
        cookie_resp = throttled_get(
            _COOKIE_URL,
            host_key=HOST_KEY,
            min_interval=_min_interval(),
        )
        cookies = requests.utils.dict_from_cookiejar(cookie_resp.cookies)
        crumb_resp = throttled_get(
            _CRUMB_URL,
            host_key=HOST_KEY,
            min_interval=_min_interval(),
            headers={"Cookie": _cookie_header(cookies)} if cookies else None,
        )
        crumb_resp.raise_for_status()
        crumb = (crumb_resp.text or "").strip()
        if not crumb:
            raise ValueError("Yahoo returned an empty crumb token")
        return crumb, cookies


def _cookie_header(cookies: Dict[str, str]) -> str:
    return "; ".join(f"{name}={value}" for name, value in cookies.items())


_CRUMB_STORE = _CrumbStore()


def get_chart(
    symbol: str,
    *,
    interval: str = "1d",
    period1: Optional[int] = None,
    period2: Optional[int] = None,
    range_: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Fetch Yahoo v8 OHLCV bars as ascending normalized rows."""
    yahoo_symbol = map_symbol(symbol)
    params: Dict[str, Any] = {"interval": interval}
    if range_:
        params["range"] = range_
    else:
        if period1 is not None:
            params["period1"] = int(period1)
        if period2 is not None:
            params["period2"] = int(period2)

    payload = throttled_get_json(
        f"{_CHART_BASE}/{yahoo_symbol}",
        host_key=HOST_KEY,
        min_interval=_min_interval(),
        params=params,
    )
    return _parse_chart(payload, yahoo_symbol)


def _parse_chart(payload: Any, yahoo_symbol: str) -> List[Dict[str, Any]]:
    chart = (payload or {}).get("chart") or {}
    error = chart.get("error")
    if error:
        description = error.get("description") if isinstance(error, dict) else error
        raise ValueError(f"Yahoo chart error for {yahoo_symbol}: {description}")

    results = chart.get("result") or []
    if not results:
        return []
    result = results[0] or {}
    timestamps = result.get("timestamp") or []
    quotes = (((result.get("indicators") or {}).get("quote")) or [{}])[0] or {}

    rows: List[Dict[str, Any]] = []
    for index, ts in enumerate(timestamps):
        values = {field: _at(quotes.get(field), index) for field in _QUOTE_FIELDS}
        if any(values[field] is None for field in ("open", "high", "low", "close")):
            continue
        row: Dict[str, Any] = {"trade_date": ts}
        row.update({field: _to_float(values[field]) for field in _QUOTE_FIELDS})
        rows.append(row)
    return rows


def _at(series: Any, index: int) -> Any:
    if isinstance(series, list) and 0 <= index < len(series):
        return series[index]
    return None


def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def get_quote_summary(symbol: str, modules: List[str]) -> Dict[str, Any]:
    """Fetch quote-summary modules with one crumb refresh on HTTP 401."""
    yahoo_symbol = map_symbol(symbol)
    payload = _quote_summary_request(
        yahoo_symbol, ",".join(modules), force_refresh=False
    )
    return _parse_quote_summary(payload, yahoo_symbol)


def _quote_summary_request(
    yahoo_symbol: str, modules_param: str, *, force_refresh: bool
) -> Any:
    crumb, cookies = _CRUMB_STORE.get(force_refresh=force_refresh)
    headers = {"Cookie": _cookie_header(cookies)} if cookies else None
    response = throttled_get(
        f"{_QUOTE_SUMMARY_BASE}/{yahoo_symbol}",
        host_key=HOST_KEY,
        min_interval=_min_interval(),
        params={"modules": modules_param, "crumb": crumb},
        headers=headers,
    )
    if response.status_code == 401 and not force_refresh:
        logger.info("Yahoo quoteSummary 401 for %s; refreshing crumb", yahoo_symbol)
        return _quote_summary_request(
            yahoo_symbol, modules_param, force_refresh=True
        )
    response.raise_for_status()
    return response.json()


def _parse_quote_summary(payload: Any, yahoo_symbol: str) -> Dict[str, Any]:
    summary = (payload or {}).get("quoteSummary") or {}
    error = summary.get("error")
    if error:
        description = error.get("description") if isinstance(error, dict) else error
        raise ValueError(f"Yahoo quoteSummary error for {yahoo_symbol}: {description}")
    results = summary.get("result") or []
    return (results[0] or {}) if results else {}


def get_options(symbol: str, *, expiration: Optional[int] = None) -> Dict[str, Any]:
    """Fetch a Yahoo option chain with one crumb refresh on HTTP 401."""
    yahoo_symbol = map_symbol(symbol)
    payload = _options_request(yahoo_symbol, expiration, force_refresh=False)
    return _parse_options(payload, yahoo_symbol)


def _options_request(
    yahoo_symbol: str, expiration: Optional[int], *, force_refresh: bool
) -> Any:
    crumb, cookies = _CRUMB_STORE.get(force_refresh=force_refresh)
    headers = {"Cookie": _cookie_header(cookies)} if cookies else None
    params: Dict[str, Any] = {"crumb": crumb}
    if expiration is not None:
        params["date"] = int(expiration)
    response = throttled_get(
        f"{_OPTIONS_BASE}/{yahoo_symbol}",
        host_key=HOST_KEY,
        min_interval=_min_interval(),
        params=params,
        headers=headers,
    )
    if response.status_code == 401 and not force_refresh:
        logger.info("Yahoo options 401 for %s; refreshing crumb", yahoo_symbol)
        return _options_request(yahoo_symbol, expiration, force_refresh=True)
    response.raise_for_status()
    return response.json()


def _parse_options(payload: Any, yahoo_symbol: str) -> Dict[str, Any]:
    chain = (payload or {}).get("optionChain") or {}
    error = chain.get("error")
    if error:
        description = error.get("description") if isinstance(error, dict) else error
        raise ValueError(f"Yahoo options error for {yahoo_symbol}: {description}")
    results = chain.get("result") or []
    return (results[0] or {}) if results else {}


def search(query: str) -> List[Dict[str, Any]]:
    """Look up matching instruments through Yahoo's search endpoint."""
    payload = throttled_get_json(
        _SEARCH_BASE,
        host_key=HOST_KEY,
        min_interval=_min_interval(),
        params={"q": query},
    )
    quotes = (payload or {}).get("quotes") or []
    return [quote for quote in quotes if isinstance(quote, dict)]
