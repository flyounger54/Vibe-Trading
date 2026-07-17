"""Immutable, content-addressed market-data input for deterministic backtests."""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

import pandas as pd


def _copy_frame(frame: pd.DataFrame) -> pd.DataFrame:
    clone = frame.copy(deep=True)
    clone.attrs = copy.deepcopy(frame.attrs)
    return clone


def _utc_index(values: object) -> pd.DatetimeIndex:
    index = pd.DatetimeIndex(pd.to_datetime(values))
    if index.tz is None:
        index = index.tz_localize("UTC")
    return index.tz_convert("UTC")


def _frame_digest(frame: pd.DataFrame) -> str:
    canonical = frame.copy(deep=True)
    canonical.index = _utc_index(canonical.index)
    row_hash = pd.util.hash_pandas_object(canonical, index=True).values.tobytes()
    attrs = json.dumps(frame.attrs, sort_keys=True, default=str, separators=(",", ":"))
    columns = json.dumps([str(column) for column in frame.columns], separators=(",", ":"))
    digest = hashlib.sha256()
    digest.update(columns.encode("utf-8"))
    digest.update(attrs.encode("utf-8"))
    digest.update(row_hash)
    return digest.hexdigest()


@dataclass(frozen=True)
class DataBundle:
    """Frozen data snapshot passed from the runner to an execution engine.

    Frames are deep-copied at construction and every public read returns a new
    copy. The internal mapping is never exposed, so caller mutation cannot
    change the input fingerprint during a run.
    """

    _frames: Mapping[str, pd.DataFrame]
    base_currency: str
    _currencies: Mapping[str, str]
    _fx: Mapping[str, pd.Series]
    fingerprint: str
    provenance: Mapping[str, Mapping[str, Any]]

    @classmethod
    def from_frames(
        cls,
        frames: Mapping[str, pd.DataFrame],
        *,
        base_currency: str,
        fx_rates: Mapping[str, object] | None = None,
    ) -> "DataBundle":
        if not frames:
            raise ValueError("DataBundle requires at least one symbol")
        base = str(base_currency).strip().upper()
        if not base:
            raise ValueError("base_currency must be a non-empty ISO currency code")

        frozen_frames: dict[str, pd.DataFrame] = {}
        currencies: dict[str, str] = {}
        provenance: dict[str, Mapping[str, Any]] = {}
        for symbol in sorted(frames):
            frame = frames[symbol]
            if not isinstance(frame, pd.DataFrame) or frame.empty:
                raise ValueError(f"DataBundle frame for {symbol!r} must be non-empty")
            clone = _copy_frame(frame)
            clone.index = _utc_index(clone.index).rename(frame.index.name or "trade_date")
            clone = clone.sort_index(kind="mergesort")
            frozen_frames[str(symbol)] = clone
            metadata = copy.deepcopy(clone.attrs.get("vibe_metadata") or {})
            currency = str(metadata.get("currency") or base).strip().upper()
            currencies[str(symbol)] = currency
            provenance[str(symbol)] = MappingProxyType(metadata)

        fx = cls._normalize_fx_rates(
            currencies=set(currencies.values()),
            base_currency=base,
            values=fx_rates or {},
        )
        payload = {
            "schema_version": "data-bundle.v1",
            "base_currency": base,
            "symbols": {
                symbol: {
                    "currency": currencies[symbol],
                    "frame": _frame_digest(frame),
                }
                for symbol, frame in frozen_frames.items()
            },
            "fx": {
                currency: _frame_digest(series.to_frame("rate"))
                for currency, series in fx.items()
            },
        }
        fingerprint = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return cls(
            _frames=MappingProxyType(frozen_frames),
            base_currency=base,
            _currencies=MappingProxyType(currencies),
            _fx=MappingProxyType(fx),
            fingerprint=fingerprint,
            provenance=MappingProxyType(provenance),
        )

    @staticmethod
    def _normalize_fx_rates(
        *,
        currencies: set[str],
        base_currency: str,
        values: Mapping[str, object],
    ) -> dict[str, pd.Series]:
        normalized: dict[str, pd.Series] = {}
        for currency in sorted(currencies - {base_currency}):
            raw = values.get(currency)
            if raw is None:
                raw = values.get(f"{currency}/{base_currency}")
            if raw is None:
                raise ValueError(
                    f"missing FX conversion for {currency} to {base_currency}; "
                    "provide fx_rates with base-currency value per unit"
                )
            if isinstance(raw, (int, float)):
                if float(raw) <= 0:
                    raise ValueError(f"FX rate for {currency} must be positive")
                series = pd.Series(
                    [float(raw)], index=pd.DatetimeIndex(["1900-01-01"], tz="UTC")
                )
            elif isinstance(raw, Mapping):
                series = pd.Series({pd.Timestamp(key): float(value) for key, value in raw.items()})
                series.index = _utc_index(series.index)
                series = series.sort_index()
                if series.empty or not (series > 0).all():
                    raise ValueError(f"FX rates for {currency} must be non-empty and positive")
            else:
                raise ValueError(f"unsupported FX rate payload for {currency}: {type(raw).__name__}")
            normalized[currency] = series.astype(float)
        return normalized

    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(self._frames)

    def frame(self, symbol: str) -> pd.DataFrame:
        try:
            return _copy_frame(self._frames[symbol])
        except KeyError as exc:
            raise KeyError(f"symbol {symbol!r} is not present in DataBundle") from exc

    def materialize(self) -> dict[str, pd.DataFrame]:
        return {symbol: _copy_frame(frame) for symbol, frame in self._frames.items()}

    def currency(self, symbol: str) -> str:
        return self._currencies[symbol]

    def fx_rate(self, symbol: str, timestamp: pd.Timestamp) -> float:
        currency = self.currency(symbol)
        if currency == self.base_currency:
            return 1.0
        series = self._fx[currency]
        ts = pd.Timestamp(timestamp)
        ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
        eligible = series.loc[:ts]
        if eligible.empty:
            raise ValueError(
                f"no FX rate for {currency}/{self.base_currency} at or before {ts.isoformat()}"
            )
        return float(eligible.iloc[-1])
