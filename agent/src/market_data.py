"""Shared market data helpers for MCP and local agent tools."""

from __future__ import annotations

import json
import logging
import math
import re
from datetime import datetime, timezone
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_MAX_ROWS = 250

# Symbol -> preferred source. The matched source is the head of its market's
# fallback chain (registry.FALLBACK_CHAINS), so an unavailable preferred source
# still degrades gracefully to the rest of the chain. US/HK equities route to
# the throttle-tolerant Yahoo public endpoint first (lower IP-ban risk than the
# yfinance SDK), A-shares to the Tencent quote endpoint.
_SOURCE_PATTERNS = [
    (re.compile(r"^local:", re.I), "local"),
    (re.compile(r"^\d{6}\.(SZ|SH|BJ)$", re.I), "astock"),
    (re.compile(r"^[A-Z]+\.US$", re.I), "global"),
    (re.compile(r"^\d{3,5}\.HK$", re.I), "global"),
    (re.compile(r"^[A-Z]+-USDT$", re.I), "okx"),
    (re.compile(r"^[A-Z]+/USDT$", re.I), "ccxt"),
]


def detect_source(code: str) -> str:
    """Infer the best loader source for a normalized symbol."""
    for pattern, source in _SOURCE_PATTERNS:
        if pattern.match(code):
            return source
    return "tushare"


def detect_market(code: str) -> str:
    """Infer the canonical market key used by the provider platform."""
    clean = code.split(":", 1)[-1] if code.lower().startswith("local:") else code
    upper = clean.upper()
    if re.match(r"^\d{6}\.(SZ|SH|BJ)$", upper):
        return "a_share"
    if upper.endswith(".US"):
        return "us_equity"
    if re.match(r"^\d{3,5}\.HK$", upper):
        return "hk_equity"
    if re.match(r"^[A-Z]+[-/][A-Z]+$", upper):
        return "crypto"
    return "a_share"


def get_loader(source: str):
    """Get loader class via registry with fallback support."""
    from backtest.loaders.registry import get_loader_cls_with_fallback

    return get_loader_cls_with_fallback(source)


def cap_rows(records: list, max_rows: int) -> list | dict[str, object]:
    """Bound a per-symbol row list to keep tool payloads within budget."""
    n = len(records)
    if max_rows < 0:
        max_rows = DEFAULT_MAX_ROWS
    if max_rows == 0 or n <= max_rows:
        return records
    step = math.ceil(n / max_rows)
    sampled = records[::step]
    if sampled[-1] is not records[-1]:
        sampled = sampled + [records[-1]]
    return {
        "rows": n,
        "returned": len(sampled),
        "truncated": True,
        "policy": f"every-{step}th-row (even stride; last bar pinned)",
        "hint": "narrow the date range, coarsen interval, or set max_rows=0 for all rows",
        "data": sampled,
    }


def _json_safe(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def fetch_market_data(
    *,
    codes: list[str],
    start_date: str,
    end_date: str,
    source: str = "auto",
    interval: str = "1D",
    adjustment: str = "none",
    max_rows: int = DEFAULT_MAX_ROWS,
    loader_resolver: Callable[[str], type] = get_loader,
) -> dict[str, Any]:
    """Fetch normalized OHLCV data through the repository loader layer."""
    results: dict[str, Any] = {}

    platform_mode = loader_resolver is get_loader
    if platform_mode:
        from backtest.loaders.registry import FallbackLoader, _ensure_registered

        _ensure_registered()
        platform_groups: dict[tuple[str, str], list[str]] = {}
        for code in codes:
            preferred = detect_source(code) if source == "auto" else source
            platform_groups.setdefault((detect_market(code), preferred), []).append(code)
        groups = {f"{market}:{preferred}": items for (market, preferred), items in platform_groups.items()}
    elif source == "auto":
        groups: dict[str, list[str]] = {}
        for code in codes:
            src = detect_source(code)
            groups.setdefault(src, []).append(code)
    else:
        groups = {source: list(codes)}

    metadata: dict[str, Any] = {
        "schema_version": "market-data-response.v1",
        "as_of": datetime.now(timezone.utc).isoformat(),
        "symbols": {},
        "failures": {},
    }
    for src, src_codes in groups.items():
        if platform_mode:
            market, preferred = src.split(":", 1)
            loader = FallbackLoader(market, preferred=preferred)
        else:
            loader_cls = loader_resolver(src)
            loader = loader_cls()
        try:
            fetch_kwargs: dict[str, Any] = {"interval": interval}
            if platform_mode:
                fetch_kwargs["adjustment"] = adjustment
            data_map = loader.fetch(src_codes, start_date, end_date, **fetch_kwargs)
        except Exception:
            logger.exception(
                "market-data loader %r failed for %s; codes fall through to _unresolved",
                src,
                src_codes,
            )
            data_map = {}
        for symbol, df in data_map.items():
            records = df.reset_index().to_dict(orient="records")
            for row in records:
                for key, value in row.items():
                    row[key] = _json_safe(value)
            results[symbol] = cap_rows(records, max_rows)
            metadata["symbols"][symbol] = {
                "provenance": dict(df.attrs.get("vibe_metadata") or {}),
                "quality": dict(df.attrs.get("vibe_quality") or {}),
            }
        report = getattr(loader, "last_report", None)
        if report is not None:
            metadata["as_of"] = report.as_of
            metadata["failures"].update(
                {
                    symbol: {
                        "code": failure.code,
                        "reason": failure.reason,
                        "providers_tried": list(failure.providers_tried),
                    }
                    for symbol, failure in report.failures.items()
                }
            )

    unresolved = [code for code in codes if code not in results]
    if unresolved:
        results["_unresolved"] = unresolved

    results["_meta"] = metadata

    return results


def fetch_market_data_json(**kwargs: Any) -> str:
    """Fetch market data and return strict JSON."""
    return json.dumps(fetch_market_data(**kwargs), ensure_ascii=False, indent=2, allow_nan=False)
