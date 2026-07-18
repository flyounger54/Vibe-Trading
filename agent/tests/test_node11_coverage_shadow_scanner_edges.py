"""Branch-complete edge cases for deterministic shadow signal scanning."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

import src.shadow_account.scanner as scanner
from src.shadow_account.models import ShadowProfile, ShadowRule


pytestmark = pytest.mark.unit
_TARGET = date(2026, 4, 6)


def _rule(entry: dict[str, object] | None = None) -> ShadowRule:
    return ShadowRule(
        rule_id="R1",
        human_text="edge rule",
        entry_condition=entry or {"market": "us"},
        exit_condition={},
        holding_days_range=(1, 2),
        support_count=1,
        coverage_rate=0.5,
        sample_trades=("AAPL@2026-01-01",),
    )


def _profile(rule: ShadowRule) -> ShadowProfile:
    return ShadowProfile(
        shadow_id="shadow_edges",
        created_at="2026-01-01T00:00:00Z",
        journal_hash="hash",
        source_market="us",
        profitable_roundtrips=1,
        total_roundtrips=1,
        date_range=("2026-01-01", "2026-02-01"),
        profile_text="edges",
        rules=(rule,),
        preferred_markets=("us",),
        typical_holding_days=(1.0, 2.0),
    )


def _bars(
    closes: list[object],
    volumes: list[object] | None = None,
    *,
    index: object | None = None,
) -> pd.DataFrame:
    data: dict[str, list[object]] = {"close": closes}
    if volumes is not None:
        data["volume"] = volumes
    return pd.DataFrame(data, index=index)


def test_scan_target_invalid_market_zero_cap_and_fetcher_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invalid = _profile(_rule({"market": "unsupported"}))
    assert scanner.scan_today_signals(invalid, target_date=None) == []

    valid = _profile(_rule({"market": "us"}))
    assert scanner.scan_today_signals(valid, target_date=_TARGET, per_market=-1) == []

    calls: list[tuple[object, ...]] = []

    def one_argument_fetcher(*args: object) -> pd.DataFrame:
        calls.append(args)
        if len(args) != 1:
            raise TypeError("legacy signature")
        return _bars([10.0, 11.0], [1.0, 2.0])

    monkeypatch.setattr(scanner, "_LIQUID_BASKETS", {"us": ["AAPL"]})
    assert scanner.scan_today_signals(
        valid, target_date=_TARGET, fetcher=one_argument_fetcher
    )[0]["symbol"] == "AAPL"
    assert [len(call) for call in calls] == [3, 2, 1]

    def broken_fetcher(*args: object) -> pd.DataFrame:
        raise RuntimeError("offline")

    assert scanner._get_price_frame("A", "us", _TARGET, None, None) is None
    injected = _bars([1.0, 2.0])
    assert scanner._get_price_frame(
        "A", "us", _TARGET, {"A": injected}, broken_fetcher
    ) is injected
    assert scanner._get_price_frame(
        "A", "us", _TARGET, None, broken_fetcher
    ) is None


def test_normalize_bars_column_date_index_and_numeric_edges() -> None:
    assert scanner._normalize_bars(pd.DataFrame(), _TARGET) is None
    assert scanner._normalize_bars(pd.DataFrame({"open": [1, 2]}), _TARGET) is None

    dated = pd.DataFrame(
        {
            "CLOSE": ["10", "bad", "12", "13"],
            "VOL": ["1", "bad", "3", "4"],
            "TRADE_DATE": ["2026-04-01", "bad-date", "2026-04-06", "2026-04-07"],
        }
    )
    normalized = scanner._normalize_bars(dated, _TARGET)
    assert normalized is not None
    assert normalized["close"].tolist() == [10.0, 12.0]
    assert "volume" in normalized

    object_index = _bars([1.0, 2.0, 3.0], index=["bad", "2026-04-05", "2026-04-06"])
    normalized = scanner._normalize_bars(object_index, _TARGET)
    assert normalized is not None and normalized["close"].tolist() == [2.0, 3.0]

    future_index = pd.date_range("2026-04-07", periods=2)
    assert scanner._normalize_bars(_bars([1.0, 2.0], index=future_index), _TARGET) is None
    assert scanner._normalize_bars(_bars(["bad", 2.0]), _TARGET) is None


def test_compute_features_rejects_bad_prices_and_handles_volume_edges() -> None:
    rule = _rule({"market": "us", "return_2d": (">", 0)})
    assert scanner._compute_features(_bars([1.0, 0.0]), rule) is None
    assert scanner._compute_features(_bars([0.0, 1.0]), rule) is None

    no_baseline = scanner._compute_features(_bars([1.0, 2.0], [0.0, 3.0]), rule)
    assert no_baseline is not None and "volume_ratio" not in no_baseline
    no_last_volume = scanner._compute_features(_bars([1.0, 2.0], [1.0, 0.0]), rule)
    assert no_last_volume is not None and "volume_ratio" not in no_last_volume
    one_volume = scanner._compute_features(_bars([1.0, 2.0], [None, 2.0]), rule)
    assert one_volume is not None and "volume_ratio" not in one_volume


def test_entry_matching_checked_default_and_missing_feature_edges() -> None:
    rising = _bars([1.0, 2.0], [1.0, 2.0])
    falling = _bars([2.0, 1.0], [2.0, 1.0])
    assert not scanner._entry_condition_matches(pd.DataFrame(), _rule(), _TARGET)
    assert not scanner._entry_condition_matches(
        rising, _rule({"market": "us", "volume_gt": 3.0}), _TARGET
    )
    assert not scanner._entry_condition_matches(
        falling, _rule({"market": "us", "return_1d": (">", 0)}), _TARGET
    )
    assert not scanner._entry_condition_matches(falling, _rule(), _TARGET)
    assert scanner._entry_condition_matches(rising, _rule(), _TARGET)

    no_volume = _bars([1.0, 2.0])
    assert not scanner._entry_condition_matches(
        no_volume, _rule({"market": "us", "volume_ratio": (">", 1)}), _TARGET
    )


def test_window_feature_mapping_and_comparison_matrix() -> None:
    assert scanner._window_from_rule(_rule({"return_7d": 1}), 5) == 7
    assert scanner._window_from_rule(_rule({"momentum": (">", "3")}), 5) == 3
    assert scanner._window_from_rule(_rule({"market": "us"}), 5) == 5

    for key, expected in (
        ("market", None),
        ("MA_WINDOW", None),
        ("close_gt_ma20", "price_above_ma"),
        ("turnover_growth", "volume_ratio"),
        ("roc_5d", "momentum"),
        ("unknown", None),
    ):
        assert scanner._feature_for_condition_key(key) == expected

    assert scanner._compare(True, ("==", "false")) is False
    assert scanner._compare(False, "no") is True
    assert scanner._compare(True, 1) is True
    numeric_cases = [
        (2.0, (">", 1), True),
        (2.0, ("gt", 3), False),
        (2.0, (">=", 2), True),
        (2.0, ("ge", 3), False),
        (2.0, ("<", 3), True),
        (2.0, ("lt", 1), False),
        (2.0, ("<=", 2), True),
        (2.0, ("le", 1), False),
        (2.0, ("==", 2), True),
        (2.0, ("eq", 3), False),
        (2.0, ("!=", 3), True),
        (2.0, ("ne", 2), False),
        (2.0, ("unknown", 99), True),
        (2.0, (">", "bad"), True),
        (2.0, 1, True),
    ]
    for value, condition, expected in numeric_cases:
        assert scanner._compare(value, condition) is expected

    assert scanner._int_condition_value((">", "4"), 2) == 4
    assert scanner._int_condition_value(0, 2) == 1
    assert scanner._int_condition_value("bad", 2) == 2
    assert scanner._to_float("1.5") == 1.5
    assert scanner._to_float(None) is None
