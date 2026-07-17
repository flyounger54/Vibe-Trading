"""Bounded, versioned, observable cache contracts for node 4."""

from __future__ import annotations

import json
import statistics
import time

import pandas as pd
import pytest

from backtest.loaders import base


def _frame(value: float) -> pd.DataFrame:
    frame = pd.DataFrame(
        {"open": [value], "high": [value], "low": [value], "close": [value], "volume": [1.0]},
        index=pd.DatetimeIndex(["2020-01-02"], name="trade_date", tz="UTC"),
    )
    frame.attrs["vibe_metadata"] = {"schema_version": "bars.v1", "provider": "fixture"}
    return frame


def test_cache_key_partitions_schema_provider_version_and_adjustment() -> None:
    kwargs = {
        "source": "provider-a",
        "symbol": "A.US",
        "timeframe": "1D",
        "start_date": "2020-01-01",
        "end_date": "2020-01-31",
        "fields": None,
        "schema_version": "bars.v1",
        "provider_version": "a-v1",
        "adjustment": "none",
    }
    baseline = base.make_loader_cache_key(**kwargs)
    assert base.make_loader_cache_key(**{**kwargs, "schema_version": "bars.v2"}) != baseline
    assert base.make_loader_cache_key(**{**kwargs, "provider_version": "a-v2"}) != baseline
    assert base.make_loader_cache_key(**{**kwargs, "adjustment": "qfq"}) != baseline


def test_memory_cache_is_lru_bounded(monkeypatch) -> None:
    monkeypatch.setenv("VIBE_TRADING_DATA_MEMORY_MAX_ENTRIES", "2")
    monkeypatch.setenv(base.LOADER_CACHE_ENV, "off")
    base.loader_memory_cache_clear()

    for index, symbol in enumerate(("A.US", "B.US", "C.US"), start=1):
        base.cached_loader_fetch(
            source="fixture",
            symbol=symbol,
            timeframe="1D",
            start_date="2020-01-01",
            end_date="2020-01-31",
            fields=None,
            fetch=lambda value=float(index): _frame(value),
        )

    stats = base.loader_cache_stats()
    assert stats["l1_entries"] == 2
    assert stats["l1_evictions"] == 1


def test_corrupt_disk_entry_is_quarantined_and_removed(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv(base.LOADER_CACHE_ENV, "1")
    base.loader_memory_cache_clear()
    kwargs = {
        "source": "fixture",
        "symbol": "A.US",
        "timeframe": "1D",
        "start_date": "2020-01-01",
        "end_date": "2020-01-31",
        "fields": None,
    }
    path = base.loader_cache_path(**kwargs)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"not parquet")
    metadata = base._loader_cache_metadata_path(path)
    metadata.write_text(json.dumps({"version": 999}), encoding="utf-8")

    assert base.loader_cache_get(**kwargs) is None
    assert not path.exists()
    assert not metadata.exists()
    assert base.loader_cache_stats()["corrupt_entries_removed"] >= 1


def test_disk_prune_enforces_ttl_and_capacity(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    root = tmp_path / ".vibe-trading" / "cache" / "loaders" / "fixture"
    root.mkdir(parents=True)
    now = time.time()
    for index, age in enumerate((1000, 10, 5)):
        parquet = root / f"{index}.parquet"
        parquet.write_bytes(b"x" * 20)
        meta = parquet.with_suffix(".parquet.json")
        meta.write_text(json.dumps({"stored_at": now - age}), encoding="utf-8")

    result = base.loader_cache_prune(max_bytes=25, max_entries=1, ttl_seconds=100, now=now)

    assert result["removed_entries"] == 3
    assert result["remaining_entries"] == 0


def test_cache_latency_contracts(tmp_path, monkeypatch) -> None:
    """Acceptance budgets: L1 P95 <=10ms and warm L2 P95 <=150ms."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv(base.LOADER_CACHE_ENV, "1")
    base.loader_memory_cache_clear()
    kwargs = {
        "source": "fixture",
        "symbol": "LATENCY.US",
        "timeframe": "1D",
        "start_date": "2020-01-01",
        "end_date": "2020-01-31",
        "fields": None,
    }
    base.loader_cache_put(**kwargs, frame=_frame(10.0))
    assert base.loader_cache_get(**kwargs) is not None  # warm imports and filesystem

    l2_samples = []
    for _ in range(25):
        started = time.perf_counter()
        assert base.loader_cache_get(**kwargs) is not None
        l2_samples.append((time.perf_counter() - started) * 1000)

    base.loader_memory_cache_clear()
    base.cached_loader_fetch(**kwargs, fetch=lambda: _frame(10.0))
    l1_samples = []
    for _ in range(200):
        started = time.perf_counter()
        base.cached_loader_fetch(**kwargs, fetch=lambda: pytest.fail("unexpected L3 fetch"))
        l1_samples.append((time.perf_counter() - started) * 1000)

    assert statistics.quantiles(l1_samples, n=20)[18] <= 10.0
    assert statistics.quantiles(l2_samples, n=20)[18] <= 150.0
