"""Shared Node 12B paper/live market-integrity and daily-loss controls."""

from __future__ import annotations

import fcntl
import json
import math
import os
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, cast

from src.live.enforcement import OrderIntent
from src.live.mandate.model import ExecutionControls
from src.live.paths import broker_dir

_RISK_STATE_SCHEMA = 1
_MAX_RISK_STATE_BYTES = 1024 * 1024
_USD_EQUIVALENTS = frozenset({"USD", "USDT", "USDC"})


class RiskStateError(RuntimeError):
    """Persistent risk state is missing integrity required to trade safely."""


@dataclass(frozen=True)
class MarketSnapshot:
    """A broker quote normalized into USD with source-time provenance."""

    symbol: str
    bid_usd: float | None
    ask_usd: float | None
    last_usd: float | None
    source_ts: datetime
    observed_ts: datetime
    source_currency: str
    fx_rate_to_usd: float
    fx_source_ts: datetime | None = None


@dataclass(frozen=True)
class ExecutionRiskBreach:
    """Stable execution-risk denial contract."""

    code: str
    detail: str
    limit_value: float | None = None
    attempted_value: float | None = None


def _finite_positive(value: object) -> float | None:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) and parsed > 0 else None


def _parse_ts(value: object) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)) or (isinstance(value, str) and value.strip().isdigit()):
        numeric = float(value)
        if numeric > 10_000_000_000:
            numeric /= 1000.0
        try:
            parsed = datetime.fromtimestamp(numeric, tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
    elif isinstance(value, str) and value.strip():
        token = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(token)
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _first(mapping: Mapping[str, Any], keys: tuple[str, ...]) -> object | None:
    return next((mapping[key] for key in keys if key in mapping and mapping[key] not in (None, "")), None)


def _infer_currency(symbol: str, quote: Mapping[str, Any]) -> str | None:
    explicit = _first(quote, ("currency", "quote_currency", "ccy"))
    if explicit:
        return str(explicit).strip().upper()
    token = symbol.strip().upper()
    if token.startswith("HK.") or token.endswith(".HK"):
        return "HKD"
    if token.startswith(("CN.", "SH.", "SZ.")) or token.endswith((".SH", ".SS", ".SZ")):
        return "CNY"
    for separator in ("-", "/"):
        if separator in token:
            suffix = token.rsplit(separator, 1)[-1]
            if suffix in _USD_EQUIVALENTS:
                return suffix
    if token.startswith("US.") or token.endswith(".US") or token.isalpha():
        return "USD"
    return None


def normalize_quote(
    payload: object,
    *,
    symbol: str,
    observed_at: datetime | None = None,
) -> MarketSnapshot | None:
    """Normalize one quote envelope; missing time or FX fails closed."""
    if not isinstance(payload, dict) or str(payload.get("status", "ok")).lower() == "error":
        return None
    quote = payload.get("quote")
    if not isinstance(quote, dict):
        keyed = payload.get(symbol) or payload.get(symbol.strip().upper())
        quote = keyed if isinstance(keyed, dict) else None
    if not isinstance(quote, dict):
        for key in ("quotes", "data", "results"):
            rows = payload.get(key)
            if not isinstance(rows, list):
                continue
            candidates = [row for row in rows if isinstance(row, dict)]
            quote = next(
                (
                    row
                    for row in candidates
                    if str(_first(row, ("symbol", "ticker", "instrument")) or "")
                    .strip()
                    .upper()
                    == symbol.strip().upper()
                ),
                candidates[0] if len(candidates) == 1 else None,
            )
            if quote is not None:
                break
    if not isinstance(quote, dict):
        quote = payload
    if not isinstance(quote, dict):
        return None
    source_ts = _parse_ts(_first(quote, ("source_time", "time", "timestamp", "updated_at", "as_of")))
    if source_ts is None:
        return None
    observed = (observed_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    currency = _infer_currency(symbol, quote)
    if currency is None:
        return None

    fx_ts: datetime | None = None
    if currency in _USD_EQUIVALENTS:
        fx = 1.0
    else:
        parsed_fx = _finite_positive(
            _first(quote, ("fx_rate_to_usd", "usd_rate", "fx_to_usd"))
        )
        fx_ts = _parse_ts(_first(quote, ("fx_time", "fx_timestamp", "fx_as_of")))
        if parsed_fx is None or fx_ts is None:
            return None
        fx = parsed_fx

    def price(keys: tuple[str, ...]) -> float | None:
        raw = _finite_positive(_first(quote, keys))
        return raw * fx if raw is not None else None

    bid = price(("bid", "bid_price", "best_bid"))
    ask = price(("ask", "ask_price", "best_ask"))
    last = price(("last", "last_price", "price", "mark_price", "close", "ltp"))
    if bid is None and ask is None and last is None:
        return None
    return MarketSnapshot(
        symbol=symbol.strip().upper(),
        bid_usd=bid,
        ask_usd=ask,
        last_usd=last,
        source_ts=source_ts,
        observed_ts=observed,
        source_currency=currency,
        fx_rate_to_usd=fx,
        fx_source_ts=fx_ts,
    )


def normalize_order_notional(intent: OrderIntent, quote: MarketSnapshot) -> OrderIntent | None:
    """Return an intent with a conservative authoritative USD notional."""
    executable = quote.ask_usd if intent.side == "buy" else quote.bid_usd
    executable = executable or quote.last_usd
    if executable is None:
        return None
    implied = intent.quantity * executable if intent.quantity is not None else None
    candidates = [value for value in (intent.notional_usd, implied) if value is not None]
    if not candidates or any(_finite_positive(value) is None for value in candidates):
        return None
    return replace(intent, notional_usd=max(float(value) for value in candidates))


def check_execution_risk(
    controls: ExecutionControls,
    intent: OrderIntent,
    quote: MarketSnapshot,
    *,
    now: datetime | None = None,
    daily_loss_usd: float = 0.0,
) -> ExecutionRiskBreach | None:
    """Apply stale-data, clock, price-collar, and daily-loss checks."""
    observed = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    age = (observed - quote.source_ts).total_seconds()
    if age > controls.max_quote_age_seconds:
        return ExecutionRiskBreach(
            "stale_market_data", "quote is older than the authorized maximum",
            controls.max_quote_age_seconds, age,
        )
    if age < -controls.max_clock_drift_seconds:
        return ExecutionRiskBreach(
            "clock_drift", "quote timestamp is too far in the future",
            controls.max_clock_drift_seconds, abs(age),
        )
    if quote.fx_source_ts is not None:
        fx_age = (observed - quote.fx_source_ts).total_seconds()
        if fx_age > controls.max_quote_age_seconds or fx_age < -controls.max_clock_drift_seconds:
            return ExecutionRiskBreach(
                "stale_fx_rate", "FX normalization rate is stale or future-dated",
                controls.max_quote_age_seconds, abs(fx_age),
            )
    if daily_loss_usd >= controls.max_daily_loss_usd:
        return ExecutionRiskBreach(
            "max_daily_loss_usd", "daily account drawdown exceeds the authorized ceiling",
            controls.max_daily_loss_usd, daily_loss_usd,
        )

    bid, ask = quote.bid_usd, quote.ask_usd
    mid = (bid + ask) / 2.0 if bid is not None and ask is not None else quote.last_usd
    if mid is None or mid <= 0:
        return ExecutionRiskBreach("price_unavailable", "quote has no usable reference price")
    proposed: float
    if intent.order_type == "limit":
        limit_native = _finite_positive(intent.limit_price)
        if limit_native is None:
            return ExecutionRiskBreach("limit_price_missing", "limit order has no valid price")
        proposed = limit_native * quote.fx_rate_to_usd
    elif intent.order_type == "market":
        market_price = ask if intent.side == "buy" else bid
        if market_price is None:
            return ExecutionRiskBreach(
                "price_unavailable", "market order requires executable bid/ask pricing"
            )
        proposed = market_price
    else:
        return ExecutionRiskBreach("order_type", "unsupported order type")
    deviation_bps = abs(proposed - mid) / mid * 10_000.0
    if deviation_bps > controls.max_price_deviation_bps:
        return ExecutionRiskBreach(
            "price_deviation", "proposed execution price is outside the authorized collar",
            controls.max_price_deviation_bps, deviation_bps,
        )
    return None


def _rows(payload: object, keys: tuple[str, ...]) -> list[dict[str, Any]] | None:
    if isinstance(payload, list):
        value: object = payload
    elif isinstance(payload, dict):
        value = next((payload[key] for key in keys if isinstance(payload.get(key), list)), None)
        if value is None:
            return [] if not payload else None
    else:
        return None
    if not isinstance(value, list) or not all(isinstance(row, dict) for row in value):
        return None
    return cast(list[dict[str, Any]], value)


def open_order_reservations_usd(
    payload: object,
    *,
    now: datetime | None = None,
    max_fx_age_seconds: float = 30.0,
    max_clock_drift_seconds: float = 5.0,
) -> float | None:
    """Conservatively sum resting-order USD notional; ambiguity denies."""
    rows = _rows(payload, ("open_orders", "orders", "data"))
    if rows is None:
        return None
    total = 0.0
    for row in rows:
        direct = _finite_positive(_first(row, ("remaining_notional_usd", "notional_usd")))
        if direct is not None:
            total += direct
            continue
        qty = _finite_positive(
            _first(
                row,
                ("remaining_quantity", "remaining_qty", "remaining", "quantity", "qty", "amount"),
            )
        )
        price = _finite_positive(_first(row, ("limit_price", "price", "submitted_price", "stop_price")))
        if qty is None or price is None:
            return None
        symbol = str(_first(row, ("symbol", "code", "instrument", "ticker")) or "")
        currency = _infer_currency(symbol, row)
        if currency in _USD_EQUIVALENTS:
            fx = 1.0
        else:
            parsed_fx = _finite_positive(
                _first(row, ("fx_rate_to_usd", "usd_rate"))
            )
            fx_ts = _parse_ts(_first(row, ("fx_time", "fx_timestamp", "fx_as_of")))
            if parsed_fx is None or fx_ts is None:
                return None
            fx = parsed_fx
            observed = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
            fx_age = (observed - fx_ts).total_seconds()
            if fx_age > max_fx_age_seconds or fx_age < -max_clock_drift_seconds:
                return None
        total += qty * price * fx
    return total


def normalize_account_equity_usd(payload: object, *, default_currency: str | None = None) -> float | None:
    """Extract total account equity and require explicit USD normalization."""
    if not isinstance(payload, dict) or str(payload.get("status", "ok")).lower() == "error":
        return None
    account = payload.get("account", payload)
    if not isinstance(account, dict):
        return None
    usd = _finite_positive(_first(account, ("equity_usd", "account_value_usd", "total_equity_usd")))
    if usd is not None:
        return usd
    equity = _finite_positive(_first(account, ("equity", "portfolio_value", "account_value", "total_equity", "totalEq")))
    if equity is None:
        return None
    currency = str(_first(account, ("currency", "ccy")) or default_currency or "").strip().upper()
    if currency in _USD_EQUIVALENTS:
        return equity
    fx = _finite_positive(_first(account, ("fx_rate_to_usd", "usd_rate")))
    return equity * fx if fx is not None else None


def _risk_state_path(broker: str) -> Path:
    return broker_dir(broker) / "risk-state.json"


@contextmanager
def _risk_lock(broker: str) -> Iterator[None]:
    path = broker_dir(broker) / ".risk-state.lock"
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            yield
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        raise


def _load_risk_state(broker: str) -> dict[str, Any]:
    path = _risk_state_path(broker)
    if path.is_symlink():
        raise RiskStateError("risk state must not be a symlink")
    if not path.exists():
        return {"schema_version": _RISK_STATE_SCHEMA, "channels": {}}
    if not path.is_file():
        raise RiskStateError("risk state must be a regular file")
    stat = path.stat()
    if hasattr(os, "getuid") and stat.st_uid != os.getuid():
        raise RiskStateError("risk state has the wrong owner")
    if stat.st_size > _MAX_RISK_STATE_BYTES or stat.st_mode & 0o077:
        raise RiskStateError("risk state permissions or type are unsafe")
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RiskStateError("risk state is corrupt") from exc
    if (
        not isinstance(state, dict)
        or state.get("schema_version") != _RISK_STATE_SCHEMA
        or not isinstance(state.get("channels"), dict)
    ):
        raise RiskStateError("risk state schema is invalid")
    return state


def _write_risk_state(broker: str, state: dict[str, Any]) -> None:
    path = _risk_state_path(broker)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    encoded = (json.dumps(state, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
    if len(encoded) > _MAX_RISK_STATE_BYTES:
        raise RiskStateError("risk state exceeds the safety size limit")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, encoded)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)


def observe_daily_loss(
    broker: str,
    channel: str,
    equity_usd: float,
    *,
    now: datetime | None = None,
) -> float:
    """Persist a UTC-day opening equity and return drawdown across restarts."""
    equity = _finite_positive(equity_usd)
    if equity is None:
        raise RiskStateError("account equity is unavailable")
    observed = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    day = observed.date().isoformat()
    channel_key = str(channel or "").strip()
    if not channel_key or len(channel_key) > 160:
        raise RiskStateError("invalid risk-state channel")

    with _risk_lock(broker):
        state = _load_risk_state(broker)
        channels = state["channels"]
        entry = channels.get(channel_key)
        if not isinstance(entry, dict) or entry.get("date") != day:
            opening = equity
        else:
            parsed_opening = _finite_positive(entry.get("opening_equity_usd"))
            if parsed_opening is None:
                raise RiskStateError("risk state opening equity is invalid")
            opening = parsed_opening
        loss = max(0.0, opening - equity)
        channels[channel_key] = {
            "date": day,
            "opening_equity_usd": opening,
            "last_equity_usd": equity,
            "daily_loss_usd": loss,
            "updated_at": observed.isoformat(timespec="milliseconds"),
        }
        _write_risk_state(broker, state)
        return loss
