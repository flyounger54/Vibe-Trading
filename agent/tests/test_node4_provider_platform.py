"""Node 4 contracts for the multi-market provider platform.

All fixtures and providers in this module are local and deterministic. The
suite must never touch a real market-data endpoint.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from backtest.loaders.platform import (
    BAR_SCHEMA_VERSION,
    Adjustment,
    BarRequest,
    ProviderCapabilities,
    ProviderProtocol,
    ProviderRegistry,
    canonicalize_bar_frame,
)
from backtest.loaders.registry import FALLBACK_CHAINS, LOADER_REGISTRY, FallbackLoader


FIXTURES = Path(__file__).parent / "fixtures" / "market_data"


def _frame(value: float = 10.123456) -> pd.DataFrame:
    frame = pd.DataFrame(
        {
            "open": [value],
            "high": [value + 1],
            "low": [value - 1],
            "close": [value + 0.25],
            "volume": [1000.5],
        },
        index=pd.DatetimeIndex(["2024-01-02"], name="trade_date"),
    )
    return frame


class _PrimaryProvider:
    name = "primary"
    markets = {"us_equity"}
    requires_auth = False
    provider_version = "fixture-primary-v1"
    capabilities = ProviderCapabilities(
        intervals=frozenset({"1D"}),
        adjustments=frozenset({Adjustment.NONE}),
    )

    def is_available(self) -> bool:
        return True

    def fetch(self, codes, start_date, end_date, *, interval="1D", fields=None, adjustment="none"):
        return {"A.US": _frame()} if "A.US" in codes else {}


class _FallbackProvider:
    name = "fallback"
    markets = {"us_equity"}
    requires_auth = False
    provider_version = "fixture-fallback-v1"
    capabilities = ProviderCapabilities(
        intervals=frozenset({"1D"}),
        adjustments=frozenset({Adjustment.NONE, Adjustment.QFQ}),
    )

    def is_available(self) -> bool:
        return True

    def fetch(self, codes, start_date, end_date, *, interval="1D", fields=None, adjustment="none"):
        return {code: _frame(20.987654) for code in codes}


def _registry(*, threshold: int = 3) -> ProviderRegistry:
    return ProviderRegistry(
        providers={"primary": _PrimaryProvider, "fallback": _FallbackProvider},
        fallback_chains={"us_equity": ["primary", "fallback"]},
        circuit_failure_threshold=threshold,
        circuit_reset_seconds=60,
    )


def test_provider_protocol_is_runtime_checkable() -> None:
    assert isinstance(_PrimaryProvider(), ProviderProtocol)


def test_fallback_is_applied_per_symbol_without_losing_batch_winners() -> None:
    report = _registry().fetch(
        BarRequest(
            symbols=("A.US", "B.US"),
            market="us_equity",
            start_date="2024-01-01",
            end_date="2024-01-31",
        )
    )

    assert set(report.data) == {"A.US", "B.US"}
    assert report.data["A.US"].attrs["vibe_metadata"]["provider"] == "primary"
    assert report.data["B.US"].attrs["vibe_metadata"]["provider"] == "fallback"
    assert report.failures == {}
    assert [(item.provider, item.symbol, item.status) for item in report.attempts] == [
        ("primary", "A.US", "success"),
        ("primary", "B.US", "empty"),
        ("fallback", "B.US", "success"),
    ]


def test_legacy_loader_adapter_uses_same_per_symbol_orchestrator() -> None:
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setitem(LOADER_REGISTRY, "primary", _PrimaryProvider)
        monkeypatch.setitem(LOADER_REGISTRY, "fallback", _FallbackProvider)
        monkeypatch.setitem(FALLBACK_CHAINS, "us_equity", ["primary", "fallback"])
        loader = FallbackLoader("us_equity", preferred="primary")
        data = loader.fetch(["A.US", "B.US"], "2024-01-01", "2024-01-31")

    assert set(data) == {"A.US", "B.US"}
    assert data["A.US"].attrs["vibe_metadata"]["provider"] == "primary"
    assert data["B.US"].attrs["vibe_metadata"]["provider"] == "fallback"
    assert loader.last_report is not None and loader.last_report.failures == {}


def test_fallback_never_changes_adjustment_semantics() -> None:
    report = _registry().fetch(
        BarRequest(
            symbols=("A.US",),
            market="us_equity",
            start_date="2024-01-01",
            end_date="2024-01-31",
            adjustment=Adjustment.QFQ,
        )
    )

    assert report.data["A.US"].attrs["vibe_metadata"]["provider"] == "fallback"
    assert report.data["A.US"].attrs["vibe_metadata"]["adjustment"] == "qfq"
    assert report.attempts[0].status == "unsupported_adjustment"


def test_no_semantically_compatible_provider_returns_structured_failure() -> None:
    registry = ProviderRegistry(
        providers={"primary": _PrimaryProvider},
        fallback_chains={"us_equity": ["primary"]},
    )
    report = registry.fetch(
        BarRequest(
            symbols=("A.US",),
            market="us_equity",
            start_date="2024-01-01",
            end_date="2024-01-31",
            adjustment=Adjustment.HFQ,
        )
    )

    assert report.data == {}
    assert report.failures["A.US"].code == "no_compatible_provider"
    assert "hfq" in report.failures["A.US"].reason


@pytest.mark.parametrize(
    "market,filename,first_event,currency,timezone",
    [
        ("a_share", "a_share.csv", "2024-01-02T07:00:00+00:00", "CNY", "Asia/Shanghai"),
        ("us_equity", "us_equity.csv", "2024-01-02T21:00:00+00:00", "USD", "America/New_York"),
        ("hk_equity", "hk_equity.csv", "2024-01-02T08:00:00+00:00", "HKD", "Asia/Hong_Kong"),
        ("crypto", "crypto.csv", "2024-01-02T00:00:00+00:00", "USDT", "UTC"),
    ],
)
def test_four_market_golden_fixtures_have_canonical_utc_event_time(
    market: str,
    filename: str,
    first_event: str,
    currency: str,
    timezone: str,
) -> None:
    raw = pd.read_csv(FIXTURES / filename)
    raw = raw.set_index(pd.to_datetime(raw.pop("trade_date")))
    raw.index.name = "trade_date"

    frame = canonicalize_bar_frame(
        raw,
        symbol="FIXTURE",
        provider="golden",
        market=market,
        interval="1D",
        adjustment=Adjustment.NONE,
        start_date="2024-01-01",
        end_date="2024-01-31",
    )

    assert frame.index.tz is not None
    assert frame.index[0].isoformat() == first_event
    assert frame.attrs["vibe_metadata"] == {
        **frame.attrs["vibe_metadata"],
        "schema_version": BAR_SCHEMA_VERSION,
        "currency": currency,
        "exchange_timezone": timezone,
        "event_timezone": "UTC",
    }
    assert frame.iloc[0]["open"] == pytest.approx(float(raw.iloc[0]["open"]))


def test_quality_gate_deduplicates_sorts_and_rejects_invalid_ohlc_without_rounding() -> None:
    frame = pd.DataFrame(
        {
            "open": [11.0, 10.0, 10.123456, 12.0],
            "high": [12.0, 11.0, 11.123456, 10.0],
            "low": [10.0, 9.0, 9.123456, 11.0],
            "close": [11.5, 10.5, 10.623456, 12.0],
            "volume": [1, 2, 3, 4],
        },
        index=pd.DatetimeIndex(
            ["2024-01-03", "2024-01-02", "2024-01-02", "2024-01-04"],
            name="trade_date",
        ),
    )

    clean = canonicalize_bar_frame(
        frame,
        symbol="600519.SH",
        provider="fixture",
        market="a_share",
        interval="1D",
        adjustment="none",
        start_date="2024-01-01",
        end_date="2024-01-31",
    )

    assert len(clean) == 2
    assert clean.index.is_monotonic_increasing and clean.index.is_unique
    assert clean.iloc[0]["open"] == pytest.approx(10.123456)
    quality = clean.attrs["vibe_quality"]
    assert quality["duplicate_bars"] == 1
    assert quality["invalid_ohlc_bars"] == 1
    assert quality["was_out_of_order"] is True


def test_quality_gate_reports_gaps_and_stale_series() -> None:
    frame = pd.concat([_frame(10.0), _frame(11.0)])
    frame.index = pd.DatetimeIndex(["2024-01-01", "2024-01-10"], name="trade_date")
    clean = canonicalize_bar_frame(
        frame,
        symbol="BTC-USDT",
        provider="fixture",
        market="crypto",
        interval="1D",
        adjustment="none",
        start_date="2024-01-01",
        end_date="2024-01-20",
    )
    assert clean.attrs["vibe_quality"]["gap_count"] == 1
    assert clean.attrs["vibe_quality"]["stale_days"] == 10
    assert clean.attrs["vibe_quality"]["is_stale"] is True


class _ExplodingProvider(_PrimaryProvider):
    name = "exploding"

    def fetch(self, *args, **kwargs):
        raise TimeoutError("fixture timeout")


class _EmptyProvider(_PrimaryProvider):
    name = "empty"

    def fetch(self, *args, **kwargs):
        return {}


def test_circuit_breaker_opens_and_health_is_queryable() -> None:
    registry = ProviderRegistry(
        providers={"exploding": _ExplodingProvider},
        fallback_chains={"us_equity": ["exploding"]},
        circuit_failure_threshold=2,
        circuit_reset_seconds=60,
    )
    request = BarRequest(
        symbols=("A.US",),
        market="us_equity",
        start_date="2024-01-01",
        end_date="2024-01-31",
    )

    registry.fetch(request)
    registry.fetch(request)
    third = registry.fetch(request)

    assert third.attempts[0].status == "circuit_open"
    health = registry.health_snapshot()["exploding"]
    assert health["circuit_open"] is True
    assert health["consecutive_failures"] == 2
    assert 0.0 <= health["health_score"] < 1.0


def test_repeated_empty_batches_degrade_health_and_open_circuit() -> None:
    registry = ProviderRegistry(
        providers={"empty": _EmptyProvider},
        fallback_chains={"us_equity": ["empty"]},
        circuit_failure_threshold=2,
    )
    request = BarRequest(
        symbols=("A.US",),
        market="us_equity",
        start_date="2024-01-01",
        end_date="2024-01-31",
    )
    registry.fetch(request)
    registry.fetch(request)
    assert registry.fetch(request).attempts[0].status == "circuit_open"
    assert registry.health_snapshot()["empty"]["failures"] == 2
