"""Base backtest engine with shared bar-by-bar execution loop.

All market engines inherit from BaseEngine and override market-rule methods.
The shared run_backtest() handles: data loading → signal generation →
pre-compute target weights (with optimizer) → bar-by-bar execution with
market rule enforcement → metrics → artifacts.
"""

from __future__ import annotations

import importlib
import json
import logging
import re as _re
import sys
from abc import ABC, abstractmethod
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import pandas as pd

from backtest.data_bundle import DataBundle
from backtest.loaders.rsshub_events import (
    FeedSpec,
    RSSHubEventProvider,
    enrich_price_frames_with_events,
    feed_specs_from_config,
)
from backtest.loaders.tushare_fundamentals import (
    TushareFundamentalProvider,
    enrich_price_frames_with_fundamentals,
)
from backtest.metrics import (
    by_exit_reason_stats,
    by_symbol_stats,
    calc_metrics,
)
from backtest.models import EquitySnapshot, Position, TradeRecord
from backtest.position_sizing.loader import load_position_sizer
from backtest.position_sizing.models import SizingContext, StopState
from backtest.position_sizing.protocol import PositionSizer

logger = logging.getLogger(__name__)


class BacktestExecutionError(RuntimeError):
    """Structured correctness failure that invalidates the entire run."""

    def __init__(
        self, code: str, message: str, *, symbol: str = "", timestamp: object = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.symbol = symbol
        self.timestamp = str(timestamp) if timestamp is not None else ""

    def as_dict(self) -> Dict[str, str]:
        return {
            "code": self.code,
            "message": str(self),
            "symbol": self.symbol,
            "timestamp": self.timestamp,
        }


def _run_card_data_sources(config: Dict[str, Any], loader: Any) -> List[str]:
    """Return source names for run-card evidence."""
    configured = config.get("_run_card_effective_sources")
    if isinstance(configured, list):
        return [str(source) for source in configured if str(source).strip()]
    if isinstance(configured, str) and configured.strip():
        return [configured.strip()]

    loader_name = getattr(loader, "name", None)
    if loader_name:
        return [str(loader_name)]

    source = config.get("source")
    return [str(source)] if source else []


# ─── Market detection (lightweight, for signal alignment only) ───

_CRYPTO_RE = _re.compile(r"^[A-Z]+-USDT$|^[A-Z]+/USDT$", _re.I)
_FOREX_RE = _re.compile(r"^[A-Z]{3}/[A-Z]{3}$|^[A-Z]{6}\.FX$")


def _detect_market_for_align(code: str) -> str:
    """Lightweight market detection for ffill_limit calculation."""
    if _CRYPTO_RE.match(code):
        return "crypto"
    if _FOREX_RE.match(code):
        return "forex"
    return "equity"


# ─── Signal alignment (reused from daily_portfolio logic) ───


def _align(
    data_map: Dict[str, pd.DataFrame],
    signal_map: Dict[str, pd.Series],
    codes: List[str],
    optimizer: Optional[Callable] = None,
) -> tuple:
    """Build aligned date index, close matrix, target-position matrix, return matrix.

    Signal is shifted by 1 bar (next-bar-open semantics) then normalised so
    ``sum(abs(weights)) <= 1.0``.

    Args:
        data_map: code -> OHLCV DataFrame.
        signal_map: code -> signal Series.
        codes: Valid instrument codes.
        optimizer: Optional weight optimiser ``(ret, pos, dates) -> pos``.

    Returns:
        (dates, close_df, positions_df, returns_df)
    """
    all_dates: set = set()
    for c in codes:
        all_dates.update(data_map[c].index)
    dates = pd.DatetimeIndex(sorted(all_dates))

    close = pd.DataFrame(index=dates, columns=codes, dtype=float)
    for c in codes:
        close[c] = data_map[c]["close"].reindex(dates)

    # ffill with limit to avoid masking long suspensions (e.g. 3-week halt)
    # Cross-market needs larger limit (Chinese New Year can be 9-10 bars)
    ffill_limit = 10 if len({_detect_market_for_align(c) for c in codes}) > 1 else 5
    close = close.ffill(limit=ffill_limit)

    # Drop symbols that are entirely NaN (no data overlap with date range)
    all_nan_cols = [c for c in codes if close[c].isna().all()]
    if all_nan_cols:
        logger.warning("Symbols dropped (no usable price data): %s", all_nan_cols)
        codes = [c for c in codes if c not in all_nan_cols]
        if not codes:
            raise ValueError("All symbols have no data in the requested date range")
        close = close[codes]

    pos = pd.DataFrame(0.0, index=dates, columns=codes)
    for c in codes:
        # Shift on each symbol's OWN trading calendar, then ffill to unified
        own_dates = data_map[c].index
        raw = signal_map[c].reindex(own_dates).fillna(0.0).clip(-1.0, 1.0)
        shifted = raw.shift(1).fillna(0.0)
        pos[c] = shifted.reindex(dates).ffill(limit=ffill_limit).fillna(0.0)

    ret = close.pct_change().fillna(0.0)

    if optimizer is not None:
        pos = optimizer(ret, pos, dates)

    scale = pos.abs().sum(axis=1).clip(lower=1.0)
    pos = pos.div(scale, axis=0)

    return dates, close, pos, ret


def _load_optimizer(config: Dict[str, Any]) -> Optional[Callable]:
    """Dynamically load an optimizer function from config.

    Args:
        config: Backtest configuration.

    Returns:
        Optimizer callable, or None.
    """
    opt_name = config.get("optimizer")
    if not opt_name:
        return None
    opt_params = config.get("optimizer_params") or {}
    try:
        mod = importlib.import_module(f"backtest.optimizers.{opt_name}")
        return lambda ret, pos, dates: mod.optimize(ret, pos, dates, **opt_params)
    except (ImportError, AttributeError) as e:
        print(f"[WARN] Failed to load optimizer '{opt_name}': {e}, falling back to equal weight")
        return None


def _normalise_fundamental_fields(config: Dict[str, Any]) -> dict[str, list[str]]:
    """Read the optional statement-table field map from backtest config."""
    raw_fields = config.get("fundamental_fields")
    if raw_fields in (None, {}):
        return {}
    if not isinstance(raw_fields, dict):
        raise ValueError("fundamental_fields must map table names to field-name lists")

    normalized: dict[str, list[str]] = {}
    for table, fields in raw_fields.items():
        if not isinstance(table, str) or not table.strip():
            raise ValueError("fundamental_fields table names must be non-empty strings")
        if fields is None:
            continue
        if isinstance(fields, str) or not isinstance(fields, Iterable):
            raise ValueError(f"fundamental_fields[{table!r}] must be a list of field names")

        field_list = list(fields)
        if not field_list:
            continue
        invalid = [field for field in field_list if not isinstance(field, str) or not field.strip()]
        if invalid:
            raise ValueError(f"fundamental_fields[{table!r}] contains invalid field names")
        normalized[table.strip()] = field_list
    return normalized


def _maybe_enrich_fundamentals(
    data_map: Dict[str, pd.DataFrame],
    config: Dict[str, Any],
) -> Dict[str, pd.DataFrame]:
    """Attach configured Tushare statement fields before signal generation."""
    fields_by_table = _normalise_fundamental_fields(config)
    if not fields_by_table:
        return data_map

    try:
        provider = TushareFundamentalProvider()
        return enrich_price_frames_with_fundamentals(
            data_map,
            provider,
            fields_by_table,
            as_of=config.get("end_date", ""),
            periods=config.get("fundamental_periods"),
        )
    except Exception as exc:
        raise RuntimeError(
            f"fundamental_fields requested but Tushare enrichment failed: {exc}"
        ) from exc


def _event_feed_specs(config: Dict[str, Any]) -> List[FeedSpec]:
    """Parse the optional ``event_feeds`` feed definitions from backtest config.

    ``event_feeds`` is a list of feed-definition dicts (there is no built-in
    catalogue) — each with ``name``/``route_template``/``event_type`` and an
    optional ``code_style``. An empty/absent value means "no event enrichment".
    """
    raw_feeds = config.get("event_feeds")
    if raw_feeds in (None, [], {}):
        return []
    if not isinstance(raw_feeds, (list, tuple)):
        raise ValueError("event_feeds must be a list of feed definitions")
    return feed_specs_from_config(raw_feeds)


def _maybe_enrich_events(
    data_map: Dict[str, pd.DataFrame],
    config: Dict[str, Any],
) -> Dict[str, pd.DataFrame]:
    """Attach a point-in-time-safe ``event_score`` column before signal generation."""
    specs = _event_feed_specs(config)
    if not specs:
        return data_map

    try:
        provider = RSSHubEventProvider(feeds=specs)
        if not provider.is_available():
            raise RuntimeError(f"RSSHub base URL not configured (set ${'RSSHUB_BASE_URL'})")
        return enrich_price_frames_with_events(
            data_map,
            provider,
            as_of=config.get("end_date", ""),
            decay_lambda=float(config.get("event_decay_lambda", 0.1)),
            lookback=int(config.get("event_lookback", 30)),
        )
    except Exception as exc:
        raise RuntimeError(
            f"event_feeds requested but RSSHub enrichment failed: {exc}"
        ) from exc


# ─── Cash flow parsing ───


def _parse_cash_flows(config: Dict[str, Any]) -> Dict[pd.Timestamp, float]:
    """Parse optional ``position_sizing.cash_flows`` into a date→amount map."""
    ps_config = config.get("position_sizing") or {}
    raw = ps_config.get("cash_flows") or []
    flows: Dict[pd.Timestamp, float] = {}
    for entry in raw:
        try:
            ts = pd.Timestamp(entry["date"])
            ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
            amount = float(entry["amount"])
            if not pd.notna(amount):
                raise ValueError("amount must be finite")
            flows[ts] = flows.get(ts, 0.0) + amount
        except (KeyError, ValueError, TypeError) as exc:
            raise ValueError(f"invalid cash_flow entry {entry!r}: {exc}") from exc
    return flows


# ─── Base Engine ───


class BaseEngine(ABC):
    """Abstract base for all market engines.

    Subclasses override market-rule methods:
      - can_execute: whether a trade is allowed by market rules
      - round_size: lot-size rounding
      - calc_commission: fee structure
      - apply_slippage: slippage model
      - on_bar: per-bar hooks (funding fees, liquidation, etc.)
    """

    def __init__(self, config: dict):
        self.config = config
        self.initial_capital: float = config.get("initial_cash", 1_000_000)
        self.default_leverage: float = config.get("leverage", 1.0)
        self.capital: float = self.initial_capital
        self.positions: Dict[str, Position] = {}
        self.trades: List[TradeRecord] = []
        self.equity_snapshots: List[EquitySnapshot] = []
        self._bar_idx: int = 0
        self._active_symbol: str = ""  # set by _rebalance/_close_position for subclass use

        # Position sizing (opt-in — None means legacy behaviour)
        self._position_sizer: Optional[PositionSizer] = None
        self._peak_equity: float = self.initial_capital
        self._stop_tracker: Dict[str, StopState] = {}
        self._cash_flows: Dict[pd.Timestamp, float] = {}
        self._cash_ledger: List[Dict[str, Any]] = []
        self._data_bundle: Optional[DataBundle] = None

        participation = config.get("max_volume_participation")
        if participation is not None and not 0 < float(participation) <= 1:
            raise ValueError("max_volume_participation must be in (0, 1]")
        self._max_volume_participation = (
            float(participation) if participation is not None else None
        )
        self._price_lookback: int = config.get("position_sizing", {}).get("price_lookback", 60)

    # ── Market rule interface (subclass must implement) ──

    @abstractmethod
    def can_execute(self, symbol: str, direction: int, bar: pd.Series) -> bool:
        """Whether market rules allow this trade.

        Args:
            symbol: Instrument identifier.
            direction: 1 (long), -1 (short), 0 (close).
            bar: Current bar data (OHLCV + extras).

        Returns:
            True if allowed.
        """

    @abstractmethod
    def round_size(self, raw_size: float, price: float) -> float:
        """Round position size per market lot rules.

        Args:
            raw_size: Desired size.
            price: Current price.

        Returns:
            Rounded size.
        """

    @abstractmethod
    def calc_commission(self, size: float, price: float, direction: int, is_open: bool) -> float:
        """Calculate commission for a trade.

        Args:
            size: Trade size.
            price: Execution price.
            direction: 1 or -1.
            is_open: True for opening, False for closing.

        Returns:
            Commission amount.
        """

    @abstractmethod
    def apply_slippage(self, price: float, direction: int) -> float:
        """Apply slippage to execution price.

        Args:
            price: Raw price.
            direction: 1 (buying / covering short) or -1 (selling / shorting).

        Returns:
            Slipped price.
        """

    def on_bar(self, symbol: str, bar: pd.Series, timestamp: pd.Timestamp) -> None:
        """Per-bar market-rule hook (funding fees, liquidation, etc.).

        Default: no-op. Override in subclass as needed.
        """

    # ── PnL / margin calculation hooks ──
    # Override in FuturesBaseEngine to inject contract multiplier.

    def _calc_pnl(
        self, symbol: str, direction: int, size: float,
        entry_price: float, exit_price: float,
    ) -> float:
        """Realised PnL for a closed position."""
        return direction * size * (exit_price - entry_price)

    def _calc_margin(
        self, symbol: str, size: float, price: float, leverage: float,
    ) -> float:
        """Margin (collateral) required for a position."""
        return size * price / leverage

    def _calc_raw_size(
        self, symbol: str, target_notional: float, price: float,
    ) -> float:
        """Convert target notional exposure to number of units/contracts."""
        return target_notional / price

    # ── Base-currency accounting ──

    def _fx_rate(self, symbol: str, timestamp: pd.Timestamp) -> float:
        """Point-in-time instrument-currency to base-currency conversion."""
        if self._data_bundle is None:
            return 1.0
        return self._data_bundle.fx_rate(symbol, timestamp)

    def _instrument_currency(self, symbol: str) -> str:
        if self._data_bundle is None:
            return str(self.config.get("base_currency") or "").upper()
        return self._data_bundle.currency(symbol)

    def _base_currency(self) -> str:
        if self._data_bundle is not None:
            return self._data_bundle.base_currency
        return str(self.config.get("base_currency") or "").upper()

    def _position_margin_local(self, pos: Position) -> float:
        if pos.entry_margin_local is not None:
            return pos.entry_margin_local
        return self._calc_margin(
            pos.symbol, pos.size, pos.entry_price, pos.leverage,
        )

    def _position_entry_margin_base(self, pos: Position) -> float:
        if pos.entry_margin_base is not None:
            return pos.entry_margin_base
        return self._position_margin_local(pos) * pos.entry_fx_rate

    def _position_margin_base(
        self, pos: Position, timestamp: pd.Timestamp,
    ) -> float:
        return self._position_margin_local(pos) * self._fx_rate(pos.symbol, timestamp)

    def _position_notional_base(
        self, pos: Position, price: float, timestamp: pd.Timestamp,
    ) -> float:
        local_notional = self._calc_margin(
            pos.symbol, pos.size, price, pos.leverage,
        ) * pos.leverage
        return local_notional * self._fx_rate(pos.symbol, timestamp)

    def _apply_instrument_cash(
        self,
        symbol: str,
        amount_local: float,
        timestamp: pd.Timestamp,
        kind: str,
    ) -> float:
        """Apply a funding/swap adjustment and retain auditable FX evidence."""
        fx_rate = self._fx_rate(symbol, timestamp)
        amount_base = float(amount_local) * fx_rate
        self.capital += amount_base
        self._cash_ledger.append({
            "timestamp": timestamp,
            "kind": kind,
            "symbol": symbol,
            "amount_local": float(amount_local),
            "currency": self._instrument_currency(symbol),
            "fx_rate": fx_rate,
            "amount_base": amount_base,
            "base_currency": self._base_currency(),
        })
        return amount_base

    def _bar_is_tradeable(self, bar: pd.Series) -> bool:
        """Return false for explicit suspensions and zero-volume sessions."""
        for field in ("is_suspended", "suspended", "halted"):
            if field in bar.index and bool(bar.get(field)):
                return False
        if self.config.get("zero_volume_is_suspension", True):
            volume = bar.get("volume")
            if volume is not None and pd.notna(volume) and float(volume) <= 0:
                return False
        return True

    def _cap_fill_size(self, requested: float, bar: pd.Series) -> float:
        """Apply an optional volume-participation cap to an order fill."""
        if self._max_volume_participation is None:
            return requested
        volume = bar.get("volume")
        if volume is None or pd.isna(volume) or float(volume) < 0:
            raise BacktestExecutionError(
                "invalid_volume", "partial-fill model requires non-negative volume",
                symbol=self._active_symbol, timestamp=getattr(bar, "name", None),
            )
        capped = min(requested, float(volume) * self._max_volume_participation)
        return self.round_size(capped, float(bar.get("open", bar.get("close", 0))))

    # ── Main entry ──

    def run_backtest(
        self,
        config: Dict[str, Any],
        data_bundle: DataBundle,
        signal_engine: Any,
        run_dir: Path,
        bars_per_year: int = 252,
    ) -> Dict[str, Any]:
        """Full backtest pipeline.

        Signature matches ``daily_portfolio.run_backtest`` for drop-in replacement.

        Args:
            config: Backtest configuration dict.
            data_bundle: Immutable, content-addressed input snapshot.
            signal_engine: SignalEngine with ``generate()`` method.
            run_dir: Artifacts output directory.
            bars_per_year: Annualisation factor.

        Returns:
            Metrics dictionary.
        """
        codes = config.get("codes", [])
        interval = config.get("interval", "1D")
        if not isinstance(data_bundle, DataBundle):
            raise TypeError("run_backtest requires an immutable DataBundle")
        self._data_bundle = data_bundle
        data_map = data_bundle.materialize()

        # 2. Generate signals
        signal_map = signal_engine.generate(data_map)
        if not isinstance(signal_map, dict):
            print(json.dumps({"error": (
                f"SignalEngine.generate() must return Dict[str, pd.Series], "
                f"got {type(signal_map).__name__}. "
                "Return a dict mapping symbol codes to pandas Series of signals."
            )}))
            sys.exit(1)
        for _code, _sig in signal_map.items():
            if not isinstance(_sig, pd.Series):
                print(json.dumps({"error": (
                    f"SignalEngine.generate() returned {type(_sig).__name__} for '{_code}', "
                    "expected pd.Series. Each value must be a pandas Series with DatetimeIndex."
                )}))
                sys.exit(1)
        if config.get("lookahead_sentinel", False):
            from backtest.lookahead import assert_no_lookahead
            assert_no_lookahead(
                signal_engine,
                data_map,
                signal_map,
                max_checks=int(config.get("lookahead_max_checks", 8)),
            )
        valid_codes = sorted(c for c in signal_map if c in data_map)
        if not valid_codes:
            print(json.dumps({"error": "No valid signals generated"}))
            sys.exit(1)

        # 3. Pre-compute target weights (with optimizer)
        opt_fn = _load_optimizer(config)
        dates, close_df, target_pos, ret_df = _align(
            data_map, signal_map, valid_codes, optimizer=opt_fn,
        )

        # Sync codes after _align may have dropped all-NaN symbols
        valid_codes = [c for c in valid_codes if c in target_pos.columns]

        # 3b. Load position sizer (opt-in — backward compatible)
        self._position_sizer = load_position_sizer(config)
        self._peak_equity = self.initial_capital
        self._stop_tracker = {}
        self._cash_flows = _parse_cash_flows(config)
        self._cash_ledger = []

        # 4. Bar-by-bar execution
        self._execute_bars(dates, data_map, close_df, target_pos, valid_codes)

        # 5. Build output series
        equity_series = pd.Series(
            [s.equity for s in self.equity_snapshots],
            index=[s.timestamp for s in self.equity_snapshots],
        )
        bench_ret = ret_df.mean(axis=1) if ret_df.shape[1] > 0 else pd.Series(0.0, index=dates)
        benchmark_metadata = {}

        # ── External benchmark fetch ──────────────────────────────────────────
        bench_ticker = config.get("benchmark")
        if bench_ticker and bench_ticker != "auto":
            from backtest.benchmark import resolve_benchmark
            bench_result = resolve_benchmark(
                strategy_codes=codes,
                source=config.get("source", "astock"),
                start_date=config.get("start_date", ""),
                end_date=config.get("end_date", ""),
                interval=interval,
                explicit=bench_ticker,
            )
            if bench_result is not None:
                bench_ret = bench_result.ret_series.reindex(dates).fillna(0.0)
                benchmark_metadata = {
                    "benchmark_ticker": bench_result.ticker,
                    "benchmark_return": bench_result.total_ret,
                }
        # ── External benchmark fetch ──────────────────────────────────────────

        bench_equity = self.initial_capital * (1 + bench_ret).cumprod()

        # 6. Metrics
        m = calc_metrics(equity_series, self.trades, self.initial_capital, bars_per_year, bench_ret)
        m.update(benchmark_metadata)
        m["by_symbol"] = by_symbol_stats(self.trades)
        m["by_exit_reason"] = by_exit_reason_stats(self.trades)

        # 7. Validation (optional — triggered by config["validation"])
        if config.get("validation"):
            from backtest.validation import run_validation
            v_results = run_validation(
                config, equity_series, self.trades, self.initial_capital, bars_per_year,
            )
            m["validation"] = v_results
            # Write validation.json artifact
            v_path = run_dir / "artifacts" / "validation.json"
            v_path.write_text(json.dumps(v_results, indent=2, ensure_ascii=False), encoding="utf-8")

        # 8. Artifacts
        self._write_artifacts(
            run_dir, data_map, dates, equity_series, bench_equity, bench_ret,
            target_pos, m, valid_codes,
        )

        # 9. Deterministic manifest + Trust Layer run card
        from backtest.manifest import write_run_manifest
        strategy_path = run_dir / "code" / "signal_engine.py"
        write_run_manifest(
            run_dir, config, data_bundle, strategy_path=strategy_path,
        )

        from backtest.run_card import write_run_card
        write_run_card(
            run_dir,
            config,
            m,
            data_sources=_run_card_data_sources(config, data_bundle),
            strategy_path=strategy_path,
        )

        # Print scalar metrics (skip nested dicts for JSON compat)
        print(json.dumps({k: v for k, v in m.items() if not isinstance(v, dict)}, indent=2))
        return m

    # ── Execution loop ──

    def _execute_bars(
        self,
        dates: pd.DatetimeIndex,
        data_map: Dict[str, pd.DataFrame],
        close_df: pd.DataFrame,
        target_pos: pd.DataFrame,
        codes: List[str],
    ) -> None:
        """Execute decisions made at ``t`` close at the earliest ``t+1`` open.

        ``target_pos`` is already shifted by :func:`_align`.  Position sizing
        receives the previous bar as its decision context, while orders use
        only the current bar's open. Stops are evaluated in chronological
        order: opening gaps, rebalancing, then intraday high/low movement.
        """
        for i, ts in enumerate(dates):
            self._bar_idx = i
            decision_ts = dates[i - 1] if i > 0 else None

            # a. Cash flow injection/withdrawal before the session opens.
            if ts in self._cash_flows:
                amount = self._cash_flows[ts]
                self.capital += amount
                self._cash_ledger.append({
                    "timestamp": ts,
                    "kind": "external_cash_flow",
                    "symbol": "",
                    "amount_local": amount,
                    "currency": self._base_currency(),
                    "fx_rate": 1.0,
                    "amount_base": amount,
                    "base_currency": self._base_currency(),
                })

            # b. Existing positions can be stopped by an opening gap before
            # any signal order at the same open.
            if self._position_sizer is not None:
                self._check_all_stops(close_df, data_map, ts, phase="gap")

            # c. Rebalance each symbol to target weight
            equity_ts = decision_ts if decision_ts is not None else ts
            equity = self._calc_equity(close_df, equity_ts)
            self._peak_equity = max(self._peak_equity, equity)

            for c in codes:
                target_w = float(target_pos.at[ts, c]) if ts in target_pos.index else 0.0

                # Apply position sizer to adjust weight + set stops. The
                # context is intentionally pinned to the preceding bar.
                if (
                    self._position_sizer is not None
                    and abs(target_w) > 1e-9
                    and decision_ts is not None
                ):
                    ctx = self._build_sizing_context(
                        c, target_w, decision_ts, equity, close_df, data_map,
                    )
                    result = self._position_sizer.size(ctx)
                    target_w = result.target_weight

                    if c not in self.positions and abs(target_w) > 1e-9:
                        self._stop_tracker[c] = StopState(
                            symbol=c,
                            stop_loss=result.stop_loss,
                            take_profit=result.take_profit,
                            trailing_stop_distance=result.trailing_stop_distance,
                            trailing_stop_high=self._safe_price(
                                close_df, decision_ts, c, 0,
                            ),
                            exit_bar=(
                                i + result.exit_time_bars
                                if result.exit_time_bars is not None
                                else None
                            ),
                        )

                # Rebalance exceptions are correctness failures and must abort
                # the run; silently continuing creates a false-success ledger.
                try:
                    self._rebalance(
                        c, target_w, data_map.get(c), ts, equity,
                        decision_ts=decision_ts,
                    )
                except BacktestExecutionError:
                    raise
                except Exception as exc:
                    raise BacktestExecutionError(
                        "rebalance_failed",
                        f"rebalance failed for {c} at {ts}: {exc}",
                        symbol=c,
                        timestamp=ts,
                    ) from exc

            # c2. Intraday stops use the current high/low only after open fills.
            if self._position_sizer is not None:
                self._check_all_stops(close_df, data_map, ts, phase="intraday")

            # c3. End-of-bar hooks (funding, swap, liquidation) run only after
            # open fills and intraday price-path rules for this same bar.
            for c in codes:
                if ts in data_map[c].index:
                    self.on_bar(c, data_map[c].loc[ts], ts)

            # Explicit delisting metadata terminates the position at the last
            # published close instead of valuing it indefinitely on stale data.
            for c in list(self.positions):
                metadata = data_map[c].attrs.get("vibe_metadata") or {}
                raw_delisting = metadata.get("delisting_date")
                if not raw_delisting:
                    continue
                delisting_ts = pd.Timestamp(raw_delisting)
                delisting_ts = (
                    delisting_ts.tz_localize("UTC")
                    if delisting_ts.tzinfo is None
                    else delisting_ts.tz_convert("UTC")
                )
                if ts.normalize() >= delisting_ts.normalize():
                    pos = self.positions[c]
                    raw_price = self._safe_price(close_df, ts, c, pos.entry_price)
                    exit_price = self.apply_slippage(raw_price, -pos.direction)
                    self._close_position(c, exit_price, ts, "delisting")
                    self._stop_tracker.pop(c, None)

            # The final liquidation is part of the final bar's ledger and must
            # occur before its equity snapshot and metrics are produced.
            if i == len(dates) - 1:
                for c in list(self.positions):
                    pos = self.positions[c]
                    symbol_frame = data_map.get(c)
                    has_final_bar = symbol_frame is not None and ts in symbol_frame.index
                    if (
                        not has_final_bar
                        and self.config.get("missing_final_bar_policy", "last_known") == "error"
                    ):
                        raise BacktestExecutionError(
                            "missing_final_bar",
                            f"cannot liquidate {c}: no bar at final timestamp {ts}",
                            symbol=c,
                            timestamp=ts,
                        )
                    raw_price = self._safe_price(close_df, ts, c, pos.entry_price)
                    exit_price = self.apply_slippage(raw_price, -pos.direction)
                    self._close_position(c, exit_price, ts, "end_of_backtest")
                    self._stop_tracker.pop(c, None)

            # d. Record equity snapshot
            snap_equity = self._calc_equity(close_df, ts)
            total_unrealized = 0.0
            for p in self.positions.values():
                cp = self._safe_price(close_df, ts, p.symbol, p.entry_price)
                entry_margin = self._position_entry_margin_base(p)
                current_margin = self._position_margin_base(p, ts)
                price_pnl = self._calc_pnl(
                    p.symbol, p.direction, p.size, p.entry_price, cp,
                ) * self._fx_rate(p.symbol, ts)
                total_unrealized += current_margin + price_pnl - entry_margin
            self.equity_snapshots.append(EquitySnapshot(
                timestamp=ts,
                capital=self.capital,
                unrealized=total_unrealized,
                equity=snap_equity,
                positions=len(self.positions),
            ))

    def _calc_equity(self, close_df: pd.DataFrame, ts: pd.Timestamp) -> float:
        """Total equity = free cash + sum(margin + unrealised) per position."""
        equity = self.capital
        for sym, pos in self.positions.items():
            cp = self._safe_price(close_df, ts, sym, pos.entry_price)
            margin = self._position_margin_base(pos, ts)
            unrealized = self._calc_pnl(
                sym, pos.direction, pos.size, pos.entry_price, cp,
            ) * self._fx_rate(sym, ts)
            equity += margin + unrealized
        return equity

    def _rebalance(
        self,
        symbol: str,
        target_weight: float,
        df: Optional[pd.DataFrame],
        ts: pd.Timestamp,
        equity: float,
        decision_ts: Optional[pd.Timestamp] = None,
    ) -> None:
        """Adjust position for *symbol* toward *target_weight*.

        Supports incremental add/reduce when position sizing is active.
        """
        self._active_symbol = symbol
        target_dir = 1 if target_weight > 1e-9 else (-1 if target_weight < -1e-9 else 0)
        current_pos = self.positions.get(symbol)

        # Nothing to do
        if current_pos is None and target_dir == 0:
            return
        if df is None or ts not in df.index:
            return

        bar = df.loc[ts]
        if not self._bar_is_tradeable(bar):
            return

        # Close if target is flat or direction changed
        if current_pos is not None:
            need_close = target_dir == 0 or target_dir != current_pos.direction
            if need_close:
                if self.can_execute(symbol, 0, bar):
                    open_price = float(bar.get("open", bar.get("close", 0)))
                    price = self.apply_slippage(open_price, -current_pos.direction)
                    fill_size = self._cap_fill_size(current_pos.size, bar)
                    self._close_position(
                        symbol, price, ts, "signal", size=fill_size,
                    )
                    if symbol in self.positions:
                        return
                    self._stop_tracker.pop(symbol, None)
                else:
                    return  # blocked (e.g. limit-down can't sell)

            # Incremental adjust: same direction, weight changed
            elif self._position_sizer is not None and target_dir == current_pos.direction:
                mark_ts = decision_ts if decision_ts is not None else ts
                mark_price = current_pos.entry_price
                if df is not None:
                    eligible = df.loc[:mark_ts]
                    if not eligible.empty:
                        mark_price = float(eligible.iloc[-1].get("close", mark_price))
                current_notional = self._position_notional_base(
                    current_pos,
                    mark_price,
                    mark_ts,
                )
                current_w = current_notional / equity if equity > 1e-9 else 0.0
                delta = abs(target_weight) - current_w
                if delta > 0.01:
                    self._add_to_position(
                        symbol, delta, bar, ts, equity, decision_ts=decision_ts,
                    )
                    return
                elif delta < -0.01:
                    self._reduce_position(
                        symbol, abs(delta), bar, ts, equity,
                        decision_ts=decision_ts,
                    )
                    return
                else:
                    return  # delta too small, skip

        # Open new if target non-zero and no remaining position
        if target_dir != 0 and symbol not in self.positions:
            if not self.can_execute(symbol, target_dir, bar):
                return  # blocked (e.g. A-share no-short)

            open_price = float(bar.get("open", bar.get("close", 0)))
            if open_price <= 0:
                return

            slipped = self.apply_slippage(open_price, target_dir)
            leverage = self.default_leverage
            sizing_ts = decision_ts if decision_ts is not None else ts
            decision_fx = self._fx_rate(symbol, sizing_ts)
            execution_fx = self._fx_rate(symbol, ts)
            target_notional_base = abs(target_weight) * equity * leverage
            target_notional_local = target_notional_base / decision_fx
            raw_size = self._calc_raw_size(symbol, target_notional_local, slipped)
            size = self.round_size(raw_size, slipped)
            size = self._cap_fill_size(size, bar)
            if size <= 0:
                return

            margin_local = self._calc_margin(symbol, size, slipped, leverage)
            comm_local = self.calc_commission(size, slipped, target_dir, is_open=True)
            margin = margin_local * execution_fx
            comm = comm_local * execution_fx

            # Capital check — reduce if insufficient
            if margin + comm > self.capital:
                available = self.capital - comm
                if available <= 0:
                    return
                size = self.round_size(
                    self._calc_raw_size(
                        symbol, available * leverage / execution_fx, slipped,
                    ), slipped,
                )
                if size <= 0:
                    return
                margin_local = self._calc_margin(symbol, size, slipped, leverage)
                comm_local = self.calc_commission(
                    size, slipped, target_dir, is_open=True,
                )
                margin = margin_local * execution_fx
                comm = comm_local * execution_fx

            self.capital -= (margin + comm)
            self.positions[symbol] = Position(
                symbol=symbol,
                direction=target_dir,
                entry_price=slipped,
                entry_time=ts,
                size=size,
                leverage=leverage,
                entry_bar_idx=self._bar_idx,
                entry_commission=comm,
                entry_fx_rate=execution_fx,
                entry_margin_local=margin_local,
                entry_margin_base=margin,
            )

    def _close_position(
        self,
        symbol: str,
        exit_price: float,
        exit_time: pd.Timestamp,
        reason: str,
        size: Optional[float] = None,
    ) -> None:
        """Close all or part of a position and record a net base-currency trade."""
        self._active_symbol = symbol
        pos = self.positions.get(symbol)
        if pos is None:
            return

        close_size = pos.size if size is None else min(max(float(size), 0.0), pos.size)
        if close_size <= 0:
            return
        fraction = close_size / pos.size

        price_pnl_local = self._calc_pnl(
            symbol, pos.direction, close_size, pos.entry_price, exit_price,
        )
        exit_fx = self._fx_rate(symbol, exit_time)
        entry_margin_base = self._position_entry_margin_base(pos) * fraction
        margin_local = self._position_margin_local(pos) * fraction
        exit_margin_base = margin_local * exit_fx
        gross_pnl = (
            exit_margin_base + price_pnl_local * exit_fx - entry_margin_base
        )
        exit_comm = self.calc_commission(
            close_size, exit_price, pos.direction, is_open=False,
        ) * exit_fx
        entry_commission = pos.entry_commission * fraction
        total_commission = entry_commission + exit_comm
        net_pnl = gross_pnl - total_commission
        pnl_pct = (
            net_pnl / entry_margin_base * 100
            if entry_margin_base > 1e-9
            else 0.0
        )

        self.capital += entry_margin_base + gross_pnl - exit_comm

        remaining = pos.size - close_size
        if remaining <= 1e-9:
            self.positions.pop(symbol, None)
        else:
            self.positions[symbol] = Position(
                symbol=pos.symbol,
                direction=pos.direction,
                entry_price=pos.entry_price,
                entry_time=pos.entry_time,
                size=remaining,
                leverage=pos.leverage,
                entry_bar_idx=pos.entry_bar_idx,
                entry_commission=pos.entry_commission - entry_commission,
                entry_fx_rate=pos.entry_fx_rate,
                entry_margin_local=self._position_margin_local(pos) - margin_local,
                entry_margin_base=(
                    self._position_entry_margin_base(pos) - entry_margin_base
                ),
            )

        holding_bars = max(self._bar_idx - pos.entry_bar_idx, 0)
        holding_days = max(
            float((exit_time - pos.entry_time).total_seconds() / 86400), 0.0,
        )

        self.trades.append(TradeRecord(
            symbol=symbol,
            direction=pos.direction,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            entry_time=pos.entry_time,
            exit_time=exit_time,
            size=close_size,
            leverage=pos.leverage,
            pnl=net_pnl,
            gross_pnl=gross_pnl,
            pnl_pct=pnl_pct,
            exit_reason=reason,
            holding_bars=holding_bars,
            holding_days=holding_days,
            commission=total_commission,
            currency=self._instrument_currency(symbol),
            base_currency=self._base_currency(),
            fx_rate=exit_fx,
        ))

    # ── Position sizing helpers ──

    def _check_all_stops(
        self,
        close_df: pd.DataFrame,
        data_map: Dict[str, pd.DataFrame],
        ts: pd.Timestamp,
        phase: str = "all",
    ) -> None:
        """Check gap and/or intraday stop conditions in deterministic order."""
        if phase not in {"all", "gap", "intraday"}:
            raise ValueError(f"unsupported stop evaluation phase: {phase}")
        for symbol in list(self.positions.keys()):
            stop = self._stop_tracker.get(symbol)
            if stop is None:
                continue

            pos = self.positions[symbol]
            bar = data_map[symbol].loc[ts] if (
                symbol in data_map and ts in data_map[symbol].index
            ) else None

            # Must pass market rules before we can close
            if (
                bar is None
                or not self._bar_is_tradeable(bar)
                or not self.can_execute(symbol, 0, bar)
            ):
                continue

            trigger = self._stop_trigger(stop, pos, bar, self._bar_idx, phase)
            if trigger is not None:
                exit_reason, raw_exit_price = trigger
                exec_price = self.apply_slippage(
                    raw_exit_price,
                    -pos.direction,
                )
                fill_size = self._cap_fill_size(pos.size, bar)
                self._close_position(
                    symbol, exec_price, ts, exit_reason, size=fill_size,
                )
                if symbol not in self.positions:
                    self._stop_tracker.pop(symbol, None)
            else:
                # Update trailing stop high-water mark
                if stop.trailing_stop_distance is not None and phase in {"all", "intraday"}:
                    high = float(bar.get("high", bar.get("close", pos.entry_price)))
                    low = float(bar.get("low", bar.get("close", pos.entry_price)))
                    new_high = max(stop.trailing_stop_high, high) if pos.direction == 1 else stop.trailing_stop_high
                    new_low = min(stop.trailing_stop_high, low) if pos.direction == -1 else stop.trailing_stop_high
                    new_hwm = new_high if pos.direction == 1 else new_low
                    if new_hwm != stop.trailing_stop_high:
                        new_sl = (
                            new_hwm - stop.trailing_stop_distance
                            if pos.direction == 1
                            else new_hwm + stop.trailing_stop_distance
                        )
                        self._stop_tracker[symbol] = StopState(
                            symbol=symbol,
                            stop_loss=new_sl,
                            take_profit=stop.take_profit,
                            trailing_stop_distance=stop.trailing_stop_distance,
                            trailing_stop_high=new_hwm,
                            exit_bar=stop.exit_bar,
                        )

    def _stop_trigger(
        self,
        stop: StopState,
        pos: Position,
        bar: pd.Series,
        bar_idx: int,
        phase: str,
    ) -> Optional[tuple[str, float]]:
        """Return the reason and raw chronological execution price."""
        open_price = float(bar.get("open", bar.get("close", pos.entry_price)))

        if phase in {"all", "gap"}:
            if stop.exit_bar is not None and bar_idx >= stop.exit_bar:
                return "time_exit", open_price
            if stop.stop_loss is not None and (
                (pos.direction == 1 and open_price <= stop.stop_loss)
                or (pos.direction == -1 and open_price >= stop.stop_loss)
            ):
                return "stop_loss", open_price
            if stop.take_profit is not None and (
                (pos.direction == 1 and open_price >= stop.take_profit)
                or (pos.direction == -1 and open_price <= stop.take_profit)
            ):
                return "take_profit", open_price

        if phase not in {"all", "intraday"}:
            return None

        high = float(bar.get("high", bar.get("close", open_price)))
        low = float(bar.get("low", bar.get("close", open_price)))
        stop_hit = stop.stop_loss is not None and (
            (pos.direction == 1 and low <= stop.stop_loss)
            or (pos.direction == -1 and high >= stop.stop_loss)
        )
        profit_hit = stop.take_profit is not None and (
            (pos.direction == 1 and high >= stop.take_profit)
            or (pos.direction == -1 and low <= stop.take_profit)
        )
        if stop_hit and profit_hit:
            policy = self.config.get("stop_collision_policy", "stop_first")
            if policy == "take_profit_first":
                return "take_profit", float(stop.take_profit)
            if policy != "stop_first":
                raise ValueError(f"unsupported stop_collision_policy: {policy}")
            return "stop_loss", float(stop.stop_loss)
        if stop_hit:
            return "stop_loss", float(stop.stop_loss)
        if profit_hit:
            return "take_profit", float(stop.take_profit)
        return None

    @staticmethod
    def _evaluate_stop(
        stop: StopState, pos: Position, price: float, bar_idx: int,
    ) -> Optional[str]:
        """Return exit reason if any stop/exit condition is triggered, else None."""
        if stop.stop_loss is not None:
            if pos.direction == 1 and price <= stop.stop_loss:
                return "stop_loss"
            if pos.direction == -1 and price >= stop.stop_loss:
                return "stop_loss"

        if stop.take_profit is not None:
            if pos.direction == 1 and price >= stop.take_profit:
                return "take_profit"
            if pos.direction == -1 and price <= stop.take_profit:
                return "take_profit"

        if stop.exit_bar is not None and bar_idx >= stop.exit_bar:
            return "time_exit"

        return None

    def _build_sizing_context(
        self,
        symbol: str,
        signal_weight: float,
        ts: pd.Timestamp,
        equity: float,
        close_df: pd.DataFrame,
        data_map: Dict[str, pd.DataFrame],
    ) -> SizingContext:
        """Assemble read-only context for the position sizer."""
        current_pos = self.positions.get(symbol)
        price = self._safe_price(close_df, ts, symbol, 0.0)

        # Current weight of this symbol
        if current_pos is not None and equity > 1e-9:
            pos_val = self._position_notional_base(current_pos, price, ts)
            current_weight = pos_val / equity
        else:
            current_weight = 0.0

        # Total exposure across all positions
        total_exposure = 0.0
        if equity > 1e-9:
            for p in self.positions.values():
                p_price = self._safe_price(close_df, ts, p.symbol, p.entry_price)
                total_exposure += abs(
                    self._position_notional_base(p, p_price, ts)
                ) / equity

        # Price history slice for volatility calculation
        df = data_map.get(symbol)
        price_history = None
        if df is not None:
            idx_pos = df.index.get_indexer([ts], method="pad")
            if len(idx_pos) > 0 and idx_pos[0] >= 0:
                end = idx_pos[0] + 1
                start = max(0, end - self._price_lookback)
                price_history = df.iloc[start:end]

        bar = df.loc[ts] if (df is not None and ts in df.index) else pd.Series(dtype=float)

        # Recent trades (last 50)
        recent = tuple(self.trades[-50:])

        return SizingContext(
            timestamp=ts,
            symbol=symbol,
            signal_weight=signal_weight,
            current_price=price,
            bar=bar,
            equity=equity,
            capital=self.capital,
            initial_equity=self.initial_capital,
            current_position=current_pos,
            current_weight=current_weight,
            all_positions=tuple(self.positions.values()),
            total_exposure=total_exposure,
            recent_trades=recent,
            peak_equity=self._peak_equity,
            bar_idx=self._bar_idx,
            price_history=price_history,
        )

    def _add_to_position(
        self,
        symbol: str,
        delta_weight: float,
        bar: pd.Series,
        ts: pd.Timestamp,
        equity: float,
        decision_ts: Optional[pd.Timestamp] = None,
    ) -> None:
        """Add to an existing position (pyramid / scale-in)."""
        pos = self.positions.get(symbol)
        if pos is None:
            return
        if not self.can_execute(symbol, pos.direction, bar):
            return

        open_price = float(bar.get("open", bar.get("close", 0)))
        if open_price <= 0:
            return

        slipped = self.apply_slippage(open_price, pos.direction)
        sizing_ts = decision_ts if decision_ts is not None else ts
        decision_fx = self._fx_rate(symbol, sizing_ts)
        execution_fx = self._fx_rate(symbol, ts)
        add_notional_local = delta_weight * equity * pos.leverage / decision_fx
        add_raw = self._calc_raw_size(symbol, add_notional_local, slipped)
        add_size = self.round_size(add_raw, slipped)
        add_size = self._cap_fill_size(add_size, bar)
        if add_size <= 0:
            return

        margin_local = self._calc_margin(symbol, add_size, slipped, pos.leverage)
        margin = margin_local * execution_fx
        comm = self.calc_commission(
            add_size, slipped, pos.direction, is_open=True,
        ) * execution_fx
        if margin + comm > self.capital:
            return

        # Weighted average entry price
        total_size = pos.size + add_size
        avg_price = (pos.entry_price * pos.size + slipped * add_size) / total_size

        self.capital -= (margin + comm)
        self.positions[symbol] = Position(
            symbol=symbol,
            direction=pos.direction,
            entry_price=avg_price,
            entry_time=pos.entry_time,
            size=total_size,
            leverage=pos.leverage,
            entry_bar_idx=pos.entry_bar_idx,
            entry_commission=pos.entry_commission + comm,
            entry_fx_rate=(
                (self._position_entry_margin_base(pos) + margin)
                / (self._position_margin_local(pos) + margin_local)
            ),
            entry_margin_local=self._position_margin_local(pos) + margin_local,
            entry_margin_base=self._position_entry_margin_base(pos) + margin,
        )

    def _reduce_position(
        self,
        symbol: str,
        delta_weight: float,
        bar: pd.Series,
        ts: pd.Timestamp,
        equity: float,
        decision_ts: Optional[pd.Timestamp] = None,
    ) -> None:
        """Reduce an existing position (scale-out), recording a partial trade."""
        pos = self.positions.get(symbol)
        if pos is None:
            return
        if not self.can_execute(symbol, 0, bar):
            return

        open_price = float(bar.get("open", bar.get("close", 0)))
        if open_price <= 0:
            return

        slipped = self.apply_slippage(open_price, -pos.direction)
        sizing_ts = decision_ts if decision_ts is not None else ts
        decision_fx = self._fx_rate(symbol, sizing_ts)
        reduce_notional_local = delta_weight * equity * pos.leverage / decision_fx
        reduce_raw = self._calc_raw_size(symbol, reduce_notional_local, slipped)
        reduce_size = self.round_size(reduce_raw, slipped)
        reduce_size = self._cap_fill_size(reduce_size, bar)
        if reduce_size <= 0:
            return
        reduce_size = min(reduce_size, pos.size)

        # Record partial trade
        fraction = reduce_size / pos.size
        exit_fx = self._fx_rate(symbol, ts)
        price_pnl_local = self._calc_pnl(
            symbol, pos.direction, reduce_size, pos.entry_price, slipped,
        )
        margin_local = self._position_margin_local(pos) * fraction
        entry_margin_base = self._position_entry_margin_base(pos) * fraction
        exit_margin_base = margin_local * exit_fx
        gross_pnl = exit_margin_base + price_pnl_local * exit_fx - entry_margin_base
        exit_comm = self.calc_commission(
            reduce_size, slipped, pos.direction, is_open=False,
        ) * exit_fx

        self.capital += entry_margin_base + gross_pnl - exit_comm
        entry_comm_portion = pos.entry_commission * fraction
        total_commission = entry_comm_portion + exit_comm
        net_pnl = gross_pnl - total_commission
        pnl_pct = (
            net_pnl / entry_margin_base * 100
            if entry_margin_base > 1e-9
            else 0.0
        )
        holding_days = max(
            float((ts - pos.entry_time).total_seconds() / 86400), 0.0,
        )

        self.trades.append(TradeRecord(
            symbol=symbol,
            direction=pos.direction,
            entry_price=pos.entry_price,
            exit_price=slipped,
            entry_time=pos.entry_time,
            exit_time=ts,
            size=reduce_size,
            leverage=pos.leverage,
            pnl=net_pnl,
            gross_pnl=gross_pnl,
            pnl_pct=pnl_pct,
            exit_reason="partial_close",
            holding_bars=max(self._bar_idx - pos.entry_bar_idx, 0),
            holding_days=holding_days,
            commission=total_commission,
            currency=self._instrument_currency(symbol),
            base_currency=self._base_currency(),
            fx_rate=exit_fx,
        ))

        remaining = pos.size - reduce_size
        if remaining < 1e-9:
            self.positions.pop(symbol, None)
            self._stop_tracker.pop(symbol, None)
        else:
            self.positions[symbol] = Position(
                symbol=symbol,
                direction=pos.direction,
                entry_price=pos.entry_price,
                entry_time=pos.entry_time,
                size=remaining,
                leverage=pos.leverage,
                entry_bar_idx=pos.entry_bar_idx,
                entry_commission=pos.entry_commission - entry_comm_portion,
                entry_fx_rate=pos.entry_fx_rate,
                entry_margin_local=self._position_margin_local(pos) - margin_local,
                entry_margin_base=(
                    self._position_entry_margin_base(pos) - entry_margin_base
                ),
            )

    # ── Artifacts ──

    def _write_artifacts(
        self,
        run_dir: Path,
        data_map: Dict[str, pd.DataFrame],
        dates: pd.DatetimeIndex,
        equity_series: pd.Series,
        bench_equity: pd.Series,
        bench_ret: pd.Series,
        target_pos: pd.DataFrame,
        metrics: dict,
        codes: List[str],
    ) -> None:
        """Write CSV artifacts compatible with daily_portfolio format."""
        out = run_dir / "artifacts"
        out.mkdir(parents=True, exist_ok=True)

        # OHLCV per symbol
        for code, df in data_map.items():
            df.to_csv(out / f"ohlcv_{code}.csv")

        # Equity curve
        port_ret = equity_series.pct_change().fillna(0.0)
        peak = equity_series.cummax()
        dd = (equity_series - peak) / peak.replace(0, 1)
        eq_df = pd.DataFrame({
            "ret": port_ret,
            "equity": equity_series,
            "drawdown": dd,
            "benchmark_equity": bench_equity.reindex(dates),
            "active_ret": port_ret - bench_ret.reindex(dates).fillna(0.0),
        }, index=dates)
        eq_df.index.name = "timestamp"
        eq_df.to_csv(out / "equity.csv")

        # Position weights (target, for compatibility)
        target_pos.index.name = "timestamp"
        target_pos.to_csv(out / "positions.csv")

        # Trades (compatible format)
        trade_rows = []
        for t in self.trades:
            # Entry event
            trade_rows.append({
                "timestamp": str(t.entry_time.date()) if hasattr(t.entry_time, "date") else str(t.entry_time),
                "code": t.symbol,
                "side": "buy" if t.direction == 1 else "sell",
                "price": round(t.entry_price, 4),
                "qty": round(t.size, 6),
                "reason": "signal",
                "pnl": 0.0,
                "gross_pnl": 0.0,
                "commission": 0.0,
                "holding_days": 0,
                "return_pct": 0.0,
                "currency": t.currency,
                "base_currency": t.base_currency,
                "fx_rate": t.fx_rate,
            })
            # Exit event
            try:
                hold_days = (t.exit_time - t.entry_time).days
            except Exception:
                hold_days = 0
            trade_rows.append({
                "timestamp": str(t.exit_time.date()) if hasattr(t.exit_time, "date") else str(t.exit_time),
                "code": t.symbol,
                "side": "sell" if t.direction == 1 else "buy",
                "price": round(t.exit_price, 4),
                "qty": round(t.size, 6),
                "reason": t.exit_reason,
                "pnl": round(t.pnl, 4),
                "gross_pnl": round(t.gross_pnl or 0.0, 4),
                "commission": round(t.commission, 4),
                "holding_days": t.holding_days if t.holding_days is not None else hold_days,
                "return_pct": round(t.pnl_pct, 2),
                "currency": t.currency,
                "base_currency": t.base_currency,
                "fx_rate": t.fx_rate,
            })

        trade_cols = [
            "timestamp", "code", "side", "price", "qty", "reason", "pnl",
            "gross_pnl", "commission", "holding_days", "return_pct",
            "currency", "base_currency", "fx_rate",
        ]
        pd.DataFrame(trade_rows or [], columns=trade_cols).to_csv(out / "trades.csv", index=False)

        cash_columns = [
            "timestamp", "kind", "symbol", "amount_local", "currency",
            "fx_rate", "amount_base", "base_currency",
        ]
        pd.DataFrame(self._cash_ledger, columns=cash_columns).to_csv(
            out / "cash_ledger.csv", index=False,
        )

        # Metrics
        flat_metrics = {k: v for k, v in metrics.items() if not isinstance(v, dict)}
        pd.DataFrame([flat_metrics]).to_csv(out / "metrics.csv", index=False)

    # ── Helpers ──

    @staticmethod
    def _safe_price(
        close_df: pd.DataFrame,
        ts: pd.Timestamp,
        symbol: str,
        fallback: float,
    ) -> float:
        """Get close price with fallback."""
        if ts in close_df.index and symbol in close_df.columns:
            val = close_df.at[ts, symbol]
            if pd.notna(val):
                return float(val)
        return fallback
