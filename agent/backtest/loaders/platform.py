"""Canonical multi-market provider contracts and per-symbol orchestration.

This module is deliberately network-agnostic. Provider implementations keep
ownership of HTTP/TCP clients, while :class:`ProviderRegistry` owns selection,
semantic compatibility, bounded circuit state, per-symbol fallback, canonical
UTC normalization, provenance, and structured failure reporting.
"""

from __future__ import annotations

import inspect
import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Protocol, runtime_checkable

import pandas as pd


BAR_SCHEMA_VERSION = "bars.v1"


class Adjustment(str, Enum):
    """Supported price-adjustment semantics.

    ``none`` means the provider's raw exchange prices. ``qfq`` anchors the
    latest price and scales history backwards; ``hfq`` anchors the earliest
    price and scales later history forwards. A provider must advertise the
    requested mode explicitly—fallback is never allowed to change it.
    """

    NONE = "none"
    QFQ = "qfq"
    HFQ = "hfq"


def normalize_adjustment(value: Adjustment | str) -> Adjustment:
    if isinstance(value, Adjustment):
        return value
    try:
        return Adjustment(str(value).strip().lower())
    except ValueError as exc:
        allowed = ", ".join(item.value for item in Adjustment)
        raise ValueError(f"unsupported adjustment {value!r}; expected one of: {allowed}") from exc


@dataclass(frozen=True)
class ProviderCapabilities:
    """Semantic limits advertised by one provider implementation."""

    intervals: frozenset[str] = frozenset({"1D"})
    adjustments: frozenset[Adjustment] = frozenset({Adjustment.NONE})
    supports_pagination: bool = False
    max_history_days: int | None = None

    def supports(self, *, interval: str, adjustment: Adjustment | str) -> bool:
        normalized = normalize_adjustment(adjustment)
        return interval in self.intervals and normalized in self.adjustments


@dataclass(frozen=True)
class BarRequest:
    """Immutable request passed to the fallback orchestrator."""

    symbols: tuple[str, ...]
    market: str
    start_date: str
    end_date: str
    interval: str = "1D"
    fields: tuple[str, ...] = ()
    adjustment: Adjustment = Adjustment.NONE

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbols", tuple(dict.fromkeys(str(item) for item in self.symbols)))
        object.__setattr__(self, "adjustment", normalize_adjustment(self.adjustment))
        if not self.symbols:
            raise ValueError("BarRequest.symbols cannot be empty")
        start = pd.Timestamp(self.start_date)
        end = pd.Timestamp(self.end_date)
        if start > end:
            raise ValueError(f"start_date ({self.start_date}) > end_date ({self.end_date})")


@dataclass(frozen=True)
class ProviderAttempt:
    provider: str
    symbol: str
    status: str
    reason: str | None = None
    elapsed_ms: float = 0.0


@dataclass(frozen=True)
class ProviderFailure:
    code: str
    reason: str
    providers_tried: tuple[str, ...]


@dataclass
class FetchReport:
    data: dict[str, pd.DataFrame]
    attempts: list[ProviderAttempt]
    failures: dict[str, ProviderFailure]
    as_of: str


@runtime_checkable
class ProviderProtocol(Protocol):
    """Runtime-checkable interface for OHLCV providers."""

    name: str
    markets: set[str]
    requires_auth: bool
    provider_version: str
    capabilities: ProviderCapabilities

    def is_available(self) -> bool: ...

    def fetch(
        self,
        codes: list[str],
        start_date: str,
        end_date: str,
        *,
        interval: str = "1D",
        fields: list[str] | None = None,
        adjustment: str = "none",
    ) -> dict[str, pd.DataFrame]: ...


@dataclass(frozen=True)
class _MarketMetadata:
    exchange_timezone: str
    currency: str
    session: str
    daily_close_hour: int | None


_MARKET_METADATA: dict[str, _MarketMetadata] = {
    "a_share": _MarketMetadata("Asia/Shanghai", "CNY", "regular", 15),
    "us_equity": _MarketMetadata("America/New_York", "USD", "regular", 16),
    "hk_equity": _MarketMetadata("Asia/Hong_Kong", "HKD", "regular", 16),
    "crypto": _MarketMetadata("UTC", "USDT", "24x7", None),
    "futures": _MarketMetadata("Asia/Shanghai", "CNY", "exchange-defined", None),
    "fund": _MarketMetadata("Asia/Shanghai", "CNY", "regular", 15),
    "macro": _MarketMetadata("UTC", "", "publication", None),
    "forex": _MarketMetadata("UTC", "", "24x5", None),
}


def market_metadata(market: str) -> dict[str, Any]:
    meta = _MARKET_METADATA.get(market, _MarketMetadata("UTC", "", "unknown", None))
    return {
        "exchange_timezone": meta.exchange_timezone,
        "currency": meta.currency,
        "session": meta.session,
        "daily_close_hour": meta.daily_close_hour,
    }


def _invalid_ohlc_mask(frame: pd.DataFrame) -> pd.Series:
    required = ("open", "high", "low", "close")
    if not all(column in frame.columns for column in required):
        return pd.Series(True, index=frame.index)
    open_, high, low, close = (frame[column] for column in required)
    return (
        open_.isna()
        | high.isna()
        | low.isna()
        | close.isna()
        | (high < low)
        | (high < open_)
        | (high < close)
        | (low > open_)
        | (low > close)
        | (open_ <= 0)
        | (high <= 0)
        | (low <= 0)
        | (close <= 0)
    )


def _canonical_event_index(index: pd.Index, *, market: str, interval: str) -> pd.DatetimeIndex:
    values = pd.DatetimeIndex(pd.to_datetime(index, errors="coerce"))
    if values.isna().any():
        raise ValueError("provider returned an invalid event timestamp")
    meta = _MARKET_METADATA.get(market, _MarketMetadata("UTC", "", "unknown", None))
    if values.tz is None:
        is_daily_label = interval.lower() in {"1d", "1w", "1mth", "1mo"}
        if is_daily_label and meta.daily_close_hour is not None and all(item == pd.Timestamp(0).time() for item in values.time):
            values = values + pd.Timedelta(hours=meta.daily_close_hour)
        values = values.tz_localize(meta.exchange_timezone, ambiguous="raise", nonexistent="raise")
    return values.tz_convert("UTC").rename("trade_date")


def _quality_gaps(index: pd.DatetimeIndex, *, market: str, interval: str) -> int:
    if len(index) < 2:
        return 0
    deltas = pd.Series(index[1:] - index[:-1])
    if interval.lower() != "1d":
        return 0
    threshold = pd.Timedelta(hours=36 if market == "crypto" else 96)
    return int((deltas > threshold).sum())


def _staleness(index: pd.DatetimeIndex, *, market: str, end_date: str) -> tuple[int, bool]:
    meta = _MARKET_METADATA.get(market, _MarketMetadata("UTC", "", "unknown", None))
    last_local_date = index[-1].tz_convert(meta.exchange_timezone).date()
    requested_end = pd.Timestamp(end_date).date()
    stale_days = max(0, (requested_end - last_local_date).days)
    tolerance = 1 if market == "crypto" else 4
    return stale_days, stale_days > tolerance


def canonicalize_bar_frame(
    frame: pd.DataFrame,
    *,
    symbol: str,
    provider: str,
    market: str,
    interval: str,
    adjustment: Adjustment | str,
    start_date: str,
    end_date: str,
    provider_version: str = "unknown",
) -> pd.DataFrame:
    """Normalize a provider frame to the versioned UTC OHLCV contract.

    Invalid OHLC rows and negative-volume rows are rejected. Duplicate event
    times keep the provider's last observation after a stable sort. Numeric
    values retain full provider precision; no display rounding occurs here.
    """

    if not isinstance(frame, pd.DataFrame):
        raise TypeError("provider result must be a pandas DataFrame")
    if frame.empty:
        return frame.copy()

    clean = frame.copy()
    original_index = pd.DatetimeIndex(pd.to_datetime(clean.index, errors="coerce"))
    was_out_of_order = not original_index.is_monotonic_increasing
    clean.index = _canonical_event_index(clean.index, market=market, interval=interval)
    clean = clean.sort_index(kind="mergesort")
    duplicate_bars = int(clean.index.duplicated(keep="last").sum())
    clean = clean[~clean.index.duplicated(keep="last")]

    required = ["open", "high", "low", "close"]
    missing = [column for column in required if column not in clean.columns]
    if missing:
        raise ValueError(f"provider frame missing required OHLC columns: {missing}")
    columns = required + (["volume"] if "volume" in clean.columns else [])
    clean = clean[columns]
    for column in columns:
        clean[column] = pd.to_numeric(clean[column], errors="coerce")
    if "volume" not in clean.columns:
        clean["volume"] = 0.0

    invalid_ohlc = _invalid_ohlc_mask(clean)
    invalid_volume = clean["volume"].isna() | (clean["volume"] < 0)
    invalid_ohlc_bars = int(invalid_ohlc.sum())
    invalid_volume_bars = int(invalid_volume.sum())
    clean = clean[~(invalid_ohlc | invalid_volume)].copy()
    if clean.empty:
        raise ValueError("provider frame contains no valid OHLCV bars")

    normalized_adjustment = normalize_adjustment(adjustment)
    meta = _MARKET_METADATA.get(market, _MarketMetadata("UTC", "", "unknown", None))
    as_of = clean.index[-1].isoformat()
    stale_days, is_stale = _staleness(clean.index, market=market, end_date=end_date)
    quality = {
        "duplicate_bars": duplicate_bars,
        "invalid_ohlc_bars": invalid_ohlc_bars,
        "invalid_volume_bars": invalid_volume_bars,
        "gap_count": _quality_gaps(clean.index, market=market, interval=interval),
        "stale_days": stale_days,
        "is_stale": is_stale,
        "was_out_of_order": was_out_of_order,
        "bar_count": len(clean),
    }
    clean.attrs["vibe_metadata"] = {
        "schema_version": BAR_SCHEMA_VERSION,
        "provider": provider,
        "provider_version": provider_version,
        "symbol": symbol,
        "market": market,
        "interval": interval,
        "adjustment": normalized_adjustment.value,
        "exchange_timezone": meta.exchange_timezone,
        "event_timezone": "UTC",
        "currency": meta.currency,
        "session": meta.session,
        "start_date": str(start_date),
        "end_date": str(end_date),
        "as_of": as_of,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    clean.attrs["vibe_quality"] = quality
    return clean


@dataclass
class _ProviderHealth:
    successes: int = 0
    failures: int = 0
    consecutive_failures: int = 0
    total_latency_ms: float = 0.0
    last_error: str | None = None
    circuit_open_until: float = 0.0


class ProviderRegistry:
    """Provider registry plus health-aware, per-symbol fallback orchestrator."""

    def __init__(
        self,
        *,
        providers: Mapping[str, type[Any]] | None = None,
        fallback_chains: Mapping[str, list[str]] | None = None,
        circuit_failure_threshold: int = 3,
        circuit_reset_seconds: float = 60.0,
    ) -> None:
        self.providers: dict[str, type[Any]] = dict(providers or {})
        self.fallback_chains: dict[str, list[str]] = {
            key: list(value) for key, value in (fallback_chains or {}).items()
        }
        self.circuit_failure_threshold = max(1, int(circuit_failure_threshold))
        self.circuit_reset_seconds = max(0.1, float(circuit_reset_seconds))
        self._health: dict[str, _ProviderHealth] = {}

    def register(self, provider: type[Any]) -> type[Any]:
        name = str(getattr(provider, "name", "")).strip()
        if not name:
            raise ValueError("provider class must define a non-empty name")
        self.providers[name] = provider
        return provider

    def _candidate_names(self, market: str, preferred: str | None) -> list[str]:
        chain = list(self.fallback_chains.get(market, ()))
        if preferred:
            if preferred == "local":
                return [preferred]
            return [preferred, *(name for name in chain if name != preferred)]
        return chain

    def _health_state(self, provider: str) -> _ProviderHealth:
        return self._health.setdefault(provider, _ProviderHealth())

    def _circuit_is_open(self, provider: str, now: float) -> bool:
        state = self._health_state(provider)
        if state.circuit_open_until <= now:
            state.circuit_open_until = 0.0
            return False
        return True

    def _record_success(self, provider: str, elapsed_ms: float) -> None:
        state = self._health_state(provider)
        state.successes += 1
        state.consecutive_failures = 0
        state.total_latency_ms += elapsed_ms
        state.last_error = None
        state.circuit_open_until = 0.0

    def _record_failure(self, provider: str, elapsed_ms: float, error: BaseException) -> None:
        state = self._health_state(provider)
        state.failures += 1
        state.consecutive_failures += 1
        state.total_latency_ms += elapsed_ms
        state.last_error = f"{type(error).__name__}: {error}"
        if state.consecutive_failures >= self.circuit_failure_threshold:
            state.circuit_open_until = time.monotonic() + self.circuit_reset_seconds

    @staticmethod
    def _capabilities(loader: Any) -> ProviderCapabilities:
        value = getattr(loader, "capabilities", None)
        return value if isinstance(value, ProviderCapabilities) else ProviderCapabilities()

    @staticmethod
    def _call_fetch(loader: Any, request: BarRequest, symbols: list[str]) -> dict[str, pd.DataFrame]:
        parameters = inspect.signature(loader.fetch).parameters.values()
        supports_adjustment = any(
            item.name == "adjustment" or item.kind == inspect.Parameter.VAR_KEYWORD
            for item in parameters
        )
        kwargs: dict[str, Any] = {
            "interval": request.interval,
            "fields": list(request.fields) or None,
        }
        if supports_adjustment:
            kwargs["adjustment"] = request.adjustment.value
        return loader.fetch(
            symbols,
            request.start_date,
            request.end_date,
            **kwargs,
        )

    def fetch(self, request: BarRequest, *, preferred: str | None = None) -> FetchReport:
        unresolved = list(request.symbols)
        data: dict[str, pd.DataFrame] = {}
        attempts: list[ProviderAttempt] = []
        names = self._candidate_names(request.market, preferred)
        now = time.monotonic()

        for name in names:
            if not unresolved:
                break
            provider_cls = self.providers.get(name)
            if provider_cls is None:
                attempts.extend(
                    ProviderAttempt(name, symbol, "not_registered", "provider is not registered")
                    for symbol in unresolved
                )
                continue
            if self._circuit_is_open(name, now):
                attempts.extend(
                    ProviderAttempt(name, symbol, "circuit_open", "provider circuit is open")
                    for symbol in unresolved
                )
                continue
            try:
                loader = provider_cls()
            except Exception as exc:  # noqa: BLE001 - construction failure is a provider failure
                self._record_failure(name, 0.0, exc)
                attempts.extend(
                    ProviderAttempt(name, symbol, "unavailable", str(exc)) for symbol in unresolved
                )
                continue
            try:
                available = bool(loader.is_available())
            except Exception as exc:  # noqa: BLE001 - diagnostics must not abort fallback
                self._record_failure(name, 0.0, exc)
                attempts.extend(
                    ProviderAttempt(name, symbol, "unavailable", str(exc)) for symbol in unresolved
                )
                continue
            if not available:
                attempts.extend(
                    ProviderAttempt(name, symbol, "unavailable", "provider reported unavailable")
                    for symbol in unresolved
                )
                continue

            if request.market not in set(getattr(loader, "markets", ())):
                attempts.extend(
                    ProviderAttempt(
                        name,
                        symbol,
                        "incompatible_market",
                        f"provider does not serve market {request.market}",
                    )
                    for symbol in unresolved
                )
                continue

            capabilities = self._capabilities(loader)
            if request.interval not in capabilities.intervals:
                attempts.extend(
                    ProviderAttempt(
                        name,
                        symbol,
                        "unsupported_interval",
                        f"provider does not support interval {request.interval}",
                    )
                    for symbol in unresolved
                )
                continue
            if request.adjustment not in capabilities.adjustments:
                attempts.extend(
                    ProviderAttempt(
                        name,
                        symbol,
                        "unsupported_adjustment",
                        f"provider does not support adjustment {request.adjustment.value}",
                    )
                    for symbol in unresolved
                )
                continue

            started = time.perf_counter()
            try:
                fetched = self._call_fetch(loader, request, list(unresolved)) or {}
            except Exception as exc:  # noqa: BLE001 - provider failure falls through by contract
                elapsed_ms = (time.perf_counter() - started) * 1000
                self._record_failure(name, elapsed_ms, exc)
                attempts.extend(
                    ProviderAttempt(name, symbol, "error", str(exc), elapsed_ms)
                    for symbol in unresolved
                )
                continue
            elapsed_ms = (time.perf_counter() - started) * 1000
            next_unresolved: list[str] = []
            accepted = 0
            for symbol in unresolved:
                frame = fetched.get(symbol)
                if frame is None or not isinstance(frame, pd.DataFrame) or frame.empty:
                    attempts.append(ProviderAttempt(name, symbol, "empty", "no bars returned", elapsed_ms))
                    next_unresolved.append(symbol)
                    continue
                try:
                    canonical = canonicalize_bar_frame(
                        frame,
                        symbol=symbol,
                        provider=name,
                        provider_version=str(getattr(loader, "provider_version", "unknown")),
                        market=request.market,
                        interval=request.interval,
                        adjustment=request.adjustment,
                        start_date=request.start_date,
                        end_date=request.end_date,
                    )
                except Exception as exc:  # noqa: BLE001 - invalid data falls through
                    attempts.append(ProviderAttempt(name, symbol, "invalid_data", str(exc), elapsed_ms))
                    next_unresolved.append(symbol)
                    continue
                data[symbol] = canonical
                accepted += 1
                attempts.append(ProviderAttempt(name, symbol, "success", elapsed_ms=elapsed_ms))
            if accepted:
                self._record_success(name, elapsed_ms)
            else:
                self._record_failure(
                    name,
                    elapsed_ms,
                    RuntimeError("provider returned no valid bars for the requested batch"),
                )
            unresolved = next_unresolved

        failures: dict[str, ProviderFailure] = {}
        for symbol in unresolved:
            symbol_attempts = [item for item in attempts if item.symbol == symbol]
            statuses = {item.status for item in symbol_attempts}
            semantic_only = bool(statuses) and statuses <= {
                "unsupported_adjustment",
                "unsupported_interval",
                "not_registered",
                "incompatible_market",
            }
            code = "no_compatible_provider" if semantic_only else "all_providers_failed"
            details = "; ".join(
                f"{item.provider}:{item.status}{f' ({item.reason})' if item.reason else ''}"
                for item in symbol_attempts
            ) or "fallback chain is empty"
            failures[symbol] = ProviderFailure(
                code=code,
                reason=(
                    f"no provider can satisfy interval={request.interval}, "
                    f"adjustment={request.adjustment.value}: {details}"
                    if semantic_only
                    else details
                ),
                providers_tried=tuple(item.provider for item in symbol_attempts),
            )

        return FetchReport(
            data=data,
            attempts=attempts,
            failures=failures,
            as_of=datetime.now(timezone.utc).isoformat(),
        )

    def health_snapshot(self) -> dict[str, dict[str, Any]]:
        now = time.monotonic()
        names = set(self.providers) | set(self._health)
        snapshot: dict[str, dict[str, Any]] = {}
        for name in sorted(names):
            state = self._health_state(name)
            total = state.successes + state.failures
            success_ratio = state.successes / total if total else 1.0
            failure_penalty = min(0.75, state.consecutive_failures * 0.25)
            score = max(0.0, min(1.0, success_ratio - failure_penalty))
            snapshot[name] = {
                "successes": state.successes,
                "failures": state.failures,
                "consecutive_failures": state.consecutive_failures,
                "health_score": round(score, 4),
                "average_latency_ms": round(state.total_latency_ms / total, 3) if total else 0.0,
                "last_error": state.last_error,
                "circuit_open": state.circuit_open_until > now,
                "circuit_retry_after_seconds": max(0.0, round(state.circuit_open_until - now, 3)),
            }
        return snapshot


def provenance_from_frame(frame: pd.DataFrame) -> dict[str, Any]:
    """Return JSON-safe provenance and quality metadata for one frame."""

    meta = dict(frame.attrs.get("vibe_metadata") or {})
    quality = dict(frame.attrs.get("vibe_quality") or {})
    for mapping in (meta, quality):
        for key, value in tuple(mapping.items()):
            if isinstance(value, float) and not math.isfinite(value):
                mapping[key] = None
    return {"provenance": meta, "quality": quality}
