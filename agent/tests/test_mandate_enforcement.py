"""Mandate enforcement gate tests (SPEC.md Mandate Enforcement §3–§6).

Parametrized per limit: each case submits an order breaching exactly one limit
and asserts a refusal carrying the breach; one in-mandate case asserts the order
forwards through to the underlying mock MCP tool. Universe market-cap/liquidity
floors are exercised by monkeypatching the loader-backed helpers (no network).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

import src.live.enforcement as enforcement
import src.live.order_guard as order_guard
import src.live.paths as paths
from src.live.enforcement import (
    BREACH_KIND_INSTRUMENT,
    BREACH_KIND_QUANTITATIVE,
    BREACH_KIND_UNIVERSE,
    OrderIntent,
    check_mandate,
)
from src.live.mandate.model import (
    MANDATE_SCHEMA_VERSION,
    AssetClass,
    ConsentMeta,
    ExecutionControls,
    HardCaps,
    InstrumentType,
    Mandate,
    UniverseConstraint,
)
from src.live.order_ledger import OrderLedgerError
from src.live.qualification import QualificationDecision, QualificationState
from src.tools.mcp import MCPRemoteToolSpec


# --------------------------------------------------------------------------- #
# Fixtures + mock MCP adapter                                                  #
# --------------------------------------------------------------------------- #


@pytest.fixture
def live_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the live root at a tmp dir so tests never touch the real store."""
    monkeypatch.setattr(paths, "get_runtime_root", lambda: tmp_path)
    return tmp_path


class _MockAdapter:
    """Minimal MCPServerAdapter stand-in with recording call_tool."""

    def __init__(self, *, positions: Any, balance: Any) -> None:
        self.server_name = "robinhood"
        self._positions = positions
        self._balance = balance
        self.call_records: list[dict[str, Any]] = []
        self.order_calls: list[dict[str, Any]] = []

    def call_tool(self, remote_name: str, arguments: dict, *, local_name: str | None = None) -> dict:
        self.call_records.append({"remote": remote_name, "arguments": arguments})
        if remote_name in ("get_positions", "list_positions"):
            return {"positions": self._positions, "status": "ok"}
        if remote_name in ("get_account", "get_balance", "get_buying_power"):
            return {
                "account": {"equity": self._balance, "currency": "USD"},
                "status": "ok",
            }
        if remote_name in ("list_orders", "get_orders"):
            return {"open_orders": [], "status": "ok"}
        if remote_name in ("get_quotes", "get_quote"):
            return {
                "status": "ok",
                "quotes": [{
                    "symbol": arguments.get("symbol") or "AAPL",
                    "bid": 100.0,
                    "ask": 100.0,
                    "last": 100.0,
                    "time": datetime.now(timezone.utc).isoformat(),
                    "currency": "USD",
                }],
            }
        # The order placement itself (super().execute forwards here).
        self.order_calls.append({"remote": remote_name, "arguments": arguments})
        return {"status": "ok", "order_id": "rh_test_1", "state": "accepted"}


def _spec() -> MCPRemoteToolSpec:
    return MCPRemoteToolSpec(
        server_name="robinhood",
        remote_name="place_order",
        local_name="mcp_robinhood_place_order",
        description="Place an order.",
        parameters={
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "side": {"type": "string"},
                "instrument_type": {"type": "string"},
                "notional_usd": {"type": "number"},
                "quantity": {"type": "number"},
            },
            "additionalProperties": True,
        },
    )


def _mandate(expires_in_days: int = 30, **caps_overrides: Any) -> Mandate:
    created = datetime.now(timezone.utc)
    caps = {
        "account_funding_usd": 5000.0,
        "max_order_notional_usd": 750.0,
        "max_total_exposure_usd": 5000.0,
        "max_leverage": 1.0,
        "allowed_instruments": (InstrumentType.EQUITY, InstrumentType.ETF),
        "max_trades_per_day": 5,
    }
    caps.update(caps_overrides)
    return Mandate(
        schema_version=MANDATE_SCHEMA_VERSION,
        hard_caps=HardCaps(**caps),
        universe=UniverseConstraint(
            asset_classes=(AssetClass.US_EQUITY, AssetClass.US_ETF),
            min_market_cap_usd=None,
            min_avg_daily_volume_usd=None,
            exclude_symbols=("GME",),
        ),
        consent=ConsentMeta(
            created_at=created.isoformat(),
            consent_token_sha256="deadbeef",
            broker="robinhood",
            account_ref="acct_ref_xyz",
            expires_at=(created + timedelta(days=expires_in_days)).isoformat(),
        ),
        execution_controls=ExecutionControls(
            max_daily_loss_usd=500.0,
            max_price_deviation_bps=100.0,
            max_quote_age_seconds=30.0,
            max_clock_drift_seconds=5.0,
        ),
    )


def _write_mandate(live_runtime: Path, mandate: Mandate) -> None:
    broker = live_runtime / "live" / "robinhood"
    broker.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": mandate.schema_version,
        "hard_caps": {
            "account_funding_usd": mandate.hard_caps.account_funding_usd,
            "max_order_notional_usd": mandate.hard_caps.max_order_notional_usd,
            "max_total_exposure_usd": mandate.hard_caps.max_total_exposure_usd,
            "max_leverage": mandate.hard_caps.max_leverage,
            "allowed_instruments": [i.value for i in mandate.hard_caps.allowed_instruments],
            "max_trades_per_day": mandate.hard_caps.max_trades_per_day,
        },
        "universe": {
            "asset_classes": [a.value for a in mandate.universe.asset_classes],
            "min_market_cap_usd": mandate.universe.min_market_cap_usd,
            "min_avg_daily_volume_usd": mandate.universe.min_avg_daily_volume_usd,
            "exclude_symbols": list(mandate.universe.exclude_symbols),
        },
        "execution_controls": {
            "max_daily_loss_usd": mandate.execution_controls.max_daily_loss_usd,
            "max_price_deviation_bps": mandate.execution_controls.max_price_deviation_bps,
            "max_quote_age_seconds": mandate.execution_controls.max_quote_age_seconds,
            "max_clock_drift_seconds": mandate.execution_controls.max_clock_drift_seconds,
        },
        "consent": {
            "created_at": mandate.consent.created_at,
            "consent_token_sha256": mandate.consent.consent_token_sha256,
            "broker": mandate.consent.broker,
            "account_ref": mandate.consent.account_ref,
            "expires_at": mandate.consent.expires_at,
        },
    }
    (broker / "mandate.json").write_text(json.dumps(payload), encoding="utf-8")


# --------------------------------------------------------------------------- #
# check_mandate — pure decision function, parametrized per limit               #
# --------------------------------------------------------------------------- #


def _intent(**overrides: Any) -> OrderIntent:
    base = {
        "symbol": "AAPL",
        "side": "buy",
        "notional_usd": 100.0,
        "quantity": None,
        "instrument_type": InstrumentType.EQUITY,
    }
    base.update(overrides)
    return OrderIntent(**base)


def _check(intent: OrderIntent, mandate: Mandate, *, positions=None, daily_count=0):
    return check_mandate(
        mandate,
        intent,
        positions if positions is not None else [],
        {"equity": 5000.0},
        broker="robinhood",
        remote_tool="place_order",
        daily_count=daily_count,
    )


def test_in_mandate_order_passes() -> None:
    assert _check(_intent(notional_usd=100.0), _mandate()) is None


def test_open_order_reservations_count_toward_total_exposure() -> None:
    breach = check_mandate(
        _mandate(),
        _intent(notional_usd=200.0),
        [{"market_value": 4500.0}],
        {"equity": 5000.0},
        broker="robinhood",
        remote_tool="place_order",
        daily_count=0,
        reserved_notional_usd=400.0,
    )
    assert breach is not None
    assert breach.limit == "max_total_exposure_usd"


@pytest.mark.parametrize(
    "intent_kwargs, mandate_kwargs, positions, daily_count, expect_limit, expect_kind",
    [
        # Exclude-list (universe, structural).
        (dict(symbol="GME"), {}, [], 0, "exclude_symbols", BREACH_KIND_UNIVERSE),
        # Disallowed instrument (instrument, structural).
        (dict(instrument_type=InstrumentType.CRYPTO), {}, [], 0, "allowed_instruments", BREACH_KIND_INSTRUMENT),
        # Per-order notional (quantitative).
        (dict(notional_usd=1000.0), {}, [], 0, "max_order_notional_usd", BREACH_KIND_QUANTITATIVE),
        # Total exposure (quantitative): existing $4900 + $200 buy > $5000 cap.
        (dict(notional_usd=200.0), {}, [{"market_value": 4900.0}], 0, "max_total_exposure_usd", BREACH_KIND_QUANTITATIVE),
        # Leverage (quantitative): tiny funding makes 1x leverage breach.
        (dict(notional_usd=600.0), dict(account_funding_usd=500.0, max_total_exposure_usd=1e9), [], 0, "max_leverage", BREACH_KIND_QUANTITATIVE),
        # Daily count (quantitative): already at the cap.
        (dict(notional_usd=100.0), {}, [], 5, "max_trades_per_day", BREACH_KIND_QUANTITATIVE),
    ],
)
def test_each_limit_breach(
    intent_kwargs, mandate_kwargs, positions, daily_count, expect_limit, expect_kind
) -> None:
    breach = _check(
        _intent(**intent_kwargs),
        _mandate(**mandate_kwargs),
        positions=positions,
        daily_count=daily_count,
    )
    assert breach is not None
    assert breach.limit == expect_limit
    assert breach.kind == expect_kind


def test_unreadable_positions_fail_closed() -> None:
    # A position with no parseable value → total-exposure check denies.
    breach = _check(_intent(notional_usd=100.0), _mandate(), positions=[{"junk": 1}])
    assert breach is not None
    assert breach.limit == "max_total_exposure_usd"


def test_universe_market_cap_floor_denies_when_below(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(enforcement, "market_cap_usd", lambda s, ac: 1.0e8)
    mandate = _mandate()
    mandate = Mandate(
        schema_version=mandate.schema_version,
        hard_caps=mandate.hard_caps,
        universe=UniverseConstraint(
            asset_classes=mandate.universe.asset_classes,
            min_market_cap_usd=1.0e9,  # floor above the stubbed cap
            min_avg_daily_volume_usd=None,
            exclude_symbols=mandate.universe.exclude_symbols,
        ),
        consent=mandate.consent,
    )
    breach = _check(_intent(notional_usd=100.0), mandate)
    assert breach is not None
    assert breach.limit == "min_market_cap_usd"
    assert breach.kind == BREACH_KIND_UNIVERSE


def test_universe_market_cap_missing_data_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(enforcement, "market_cap_usd", lambda s, ac: None)
    mandate = _mandate()
    mandate = Mandate(
        schema_version=mandate.schema_version,
        hard_caps=mandate.hard_caps,
        universe=UniverseConstraint(
            asset_classes=mandate.universe.asset_classes,
            min_market_cap_usd=1.0e9,
            min_avg_daily_volume_usd=None,
            exclude_symbols=mandate.universe.exclude_symbols,
        ),
        consent=mandate.consent,
    )
    breach = _check(_intent(notional_usd=100.0), mandate)
    assert breach is not None
    assert breach.limit == "min_market_cap_usd"  # no data → DENY


# --------------------------------------------------------------------------- #
# LiveOrderGuardTool — end to end through the gate                             #
# --------------------------------------------------------------------------- #


def _allowed_qualification(_broker: str, account_ref: str) -> QualificationDecision:
    return QualificationDecision(
        allowed=True,
        code="qualified",
        reason="test qualification",
        broker="robinhood",
        account_ref=account_ref,
        build_revision="e9f54e0ef19054a690690bdb3c12fa2154b20ebb",
        policy_version="node12a-live-qualification-v1",
        state=QualificationState.PILOT_ACTIVE,
        observed_trading_days=30,
    )


def _guard(adapter, **kwargs):
    kwargs.setdefault("qualification_check", _allowed_qualification)
    return order_guard.LiveOrderGuardTool(adapter, _spec(), broker="robinhood", session_id="s1", **kwargs)


def _execute(guard, **kwargs):
    kwargs.setdefault("client_order_id", f"vt_test_{uuid.uuid4().hex}")
    kwargs.setdefault("order_type", "market")
    return guard.execute(**kwargs)


def test_guard_forwards_in_mandate_order(live_runtime: Path) -> None:
    _write_mandate(live_runtime, _mandate())
    adapter = _MockAdapter(positions=[], balance=5000.0)
    guard = _guard(adapter)
    out = json.loads(_execute(guard, symbol="AAPL", side="buy", instrument_type="equity", notional_usd=100.0))
    assert out.get("status") == "ok"
    assert out.get("order_id") == "rh_test_1"
    assert len(adapter.order_calls) == 1
    # Daily counter incremented exactly once on confirmed forward.
    counter = json.loads((live_runtime / "live" / "robinhood" / "trade_counter.json").read_text())
    assert counter["count"] == 1


def test_guard_replays_same_client_order_id_without_second_broker_write(
    live_runtime: Path,
) -> None:
    _write_mandate(live_runtime, _mandate())
    adapter = _MockAdapter(positions=[], balance=5000.0)
    guard = _guard(adapter)
    args = {
        "symbol": "AAPL",
        "side": "buy",
        "instrument_type": "equity",
        "notional_usd": 100.0,
        "client_order_id": "vt_remote_replay_0001",
        "order_type": "market",
    }

    first = json.loads(guard.execute(**args))
    replay = json.loads(guard.execute(**args))

    assert first["status"] == "ok"
    assert replay["idempotency_replayed"] is True
    assert len(adapter.order_calls) == 1


def test_guard_blocks_before_broker_when_prewrite_audit_fails(
    live_runtime: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_mandate(live_runtime, _mandate())
    adapter = _MockAdapter(positions=[], balance=5000.0)
    guard = _guard(adapter)
    monkeypatch.setattr(order_guard, "_record_live_action", lambda event: None)

    out = json.loads(
        guard.execute(
            symbol="AAPL",
            side="buy",
            instrument_type="equity",
            notional_usd=100.0,
            client_order_id="vt_audit_fail_0001",
            order_type="market",
        )
    )

    assert out["status"] == "blocked"
    assert "audit unavailable" in out["reason"]
    assert adapter.order_calls == []


def test_guard_defaults_closed_without_qualification_selection(
    live_runtime: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_mandate(live_runtime, _mandate())
    monkeypatch.delenv("VIBE_TRADING_LIVE_BROKER", raising=False)
    adapter = _MockAdapter(positions=[], balance=5000.0)
    guard = order_guard.LiveOrderGuardTool(
        adapter, _spec(), broker="robinhood", session_id="s1"
    )

    out = json.loads(
        _execute(guard,
            symbol="AAPL",
            side="buy",
            instrument_type="equity",
            notional_usd=100.0,
        )
    )

    assert out["status"] == "blocked"
    assert out["decision"] == "qualification_required"
    assert out["qualification"]["code"] == "live_broker_not_enabled"
    assert adapter.order_calls == []


def test_guard_blocks_over_notional_with_breach(live_runtime: Path) -> None:
    _write_mandate(live_runtime, _mandate())
    adapter = _MockAdapter(positions=[], balance=5000.0)
    guard = _guard(adapter)
    out = json.loads(_execute(guard, symbol="AAPL", side="buy", instrument_type="equity", notional_usd=5000.0))
    assert out["status"] == "blocked"
    assert out["decision"] == "pause_for_reauth"
    assert out["requires_reauthorization"] is True
    assert out["breach"]["limit"] == "max_order_notional_usd"
    assert adapter.order_calls == []  # never forwarded


def test_guard_structural_breach_denies_no_reauth(live_runtime: Path) -> None:
    _write_mandate(live_runtime, _mandate())
    adapter = _MockAdapter(positions=[], balance=5000.0)
    guard = _guard(adapter)
    out = json.loads(_execute(guard, symbol="GME", side="buy", instrument_type="equity", notional_usd=100.0))
    assert out["status"] == "blocked"
    assert out["decision"] == "deny"
    assert out["requires_reauthorization"] is False
    assert out["breach"]["kind"] == BREACH_KIND_UNIVERSE
    assert adapter.order_calls == []


def test_guard_no_mandate_denies(live_runtime: Path) -> None:
    adapter = _MockAdapter(positions=[], balance=5000.0)
    guard = _guard(adapter)
    out = json.loads(_execute(guard, symbol="AAPL", side="buy", instrument_type="equity", notional_usd=100.0))
    assert out["status"] == "blocked"
    assert out["decision"] == "deny"
    assert adapter.order_calls == []


def test_guard_expired_mandate_denies_with_reauth(live_runtime: Path) -> None:
    _write_mandate(live_runtime, _mandate(expires_in_days=-1))
    adapter = _MockAdapter(positions=[], balance=5000.0)
    guard = _guard(adapter)
    out = json.loads(_execute(guard, symbol="AAPL", side="buy", instrument_type="equity", notional_usd=100.0))
    assert out["status"] == "blocked"
    assert out["requires_reauthorization"] is True
    assert adapter.order_calls == []


def test_guard_unparseable_intent_denies(live_runtime: Path) -> None:
    _write_mandate(live_runtime, _mandate())
    adapter = _MockAdapter(positions=[], balance=5000.0)
    guard = _guard(adapter)
    # Missing side → extractor returns None → DENY.
    out = json.loads(_execute(guard, symbol="AAPL", instrument_type="equity", notional_usd=100.0))
    assert out["status"] == "blocked"
    assert adapter.order_calls == []


def test_guard_repeatable_is_false() -> None:
    assert order_guard.LiveOrderGuardTool.repeatable is False
    assert order_guard.LiveOrderGuardTool.is_readonly is False


# --------------------------------------------------------------------------- #
# H2 — failed broker forward must not consume a count or audit "accepted"      #
# --------------------------------------------------------------------------- #


class _FailingForwardAdapter:
    """Adapter whose order placement returns an ERROR envelope (no raise).

    Mirrors ``MCPServerAdapter.call_tool``: broker/network failure is reported as
    a ``{"status": "error", ...}`` envelope, not an exception.
    """

    def __init__(self) -> None:
        self.server_name = "robinhood"
        self.order_calls: list[dict[str, Any]] = []

    def call_tool(self, remote_name: str, arguments: dict, *, local_name: str | None = None) -> dict:
        if remote_name == "get_positions":
            return {"positions": [], "status": "ok"}
        if remote_name == "get_account":
            return {"account": {"equity": 5000.0, "currency": "USD"}, "status": "ok"}
        if remote_name == "list_orders":
            return {"open_orders": [], "status": "ok"}
        if remote_name == "get_quotes":
            return {
                "status": "ok",
                "quotes": [{
                    "symbol": "AAPL", "bid": 100.0, "ask": 100.0,
                    "last": 100.0, "currency": "USD",
                    "time": datetime.now(timezone.utc).isoformat(),
                }],
            }
        # The order placement fails at the broker.
        self.order_calls.append({"remote": remote_name, "arguments": arguments})
        return {"status": "error", "error": "broker rejected", "error_type": "BrokerError"}


def _read_audit_records(live_runtime: Path) -> list[dict[str, Any]]:
    ledger = live_runtime / "live" / "audit.jsonl"
    if not ledger.is_file():
        return []
    return [json.loads(line) for line in ledger.read_text().splitlines() if line.strip()]


def test_failed_forward_does_not_consume_count_or_audit_accepted(live_runtime: Path) -> None:
    """H2: an in-mandate order whose forward errors must NOT consume a daily
    count and must NOT write an ``accepted`` record."""
    _write_mandate(live_runtime, _mandate())
    adapter = _FailingForwardAdapter()
    guard = _guard(adapter)

    out = json.loads(
        _execute(guard, symbol="AAPL", side="buy", instrument_type="equity", notional_usd=100.0)
    )

    # The order WAS forwarded (it passed the gate) but the broker errored.
    assert len(adapter.order_calls) == 1
    assert out.get("status") == "error"

    # No daily count consumed.
    counter_path = live_runtime / "live" / "robinhood" / "trade_counter.json"
    assert not counter_path.is_file()

    # The pre-write intent is accepted into the audit ledger, but no accepted
    # order_placed record exists; the broker outcome is exactly one error.
    records = _read_audit_records(live_runtime)
    submitted = [r for r in records if r["kind"] == "order_submitted"]
    assert len(submitted) == 1 and submitted[0]["outcome"] == "accepted"
    accepted = [r for r in records if r["kind"] == "order_placed"]
    assert accepted == []
    errored = [r for r in records if r["outcome"] == "error"]
    assert len(errored) == 1
    assert errored[0]["kind"] == "order_rejected"


def test_successful_forward_consumes_count_and_audits_accepted(live_runtime: Path) -> None:
    """Counterpart to H2: a non-error forward consumes exactly one count and
    writes one ``order_placed``/``accepted`` record carrying ``live_action``."""
    _write_mandate(live_runtime, _mandate())
    adapter = _MockAdapter(positions=[], balance=5000.0)
    guard = _guard(adapter)

    out = json.loads(
        _execute(guard, symbol="AAPL", side="buy", instrument_type="equity", notional_usd=100.0)
    )
    assert out.get("status") == "ok"
    counter = json.loads((live_runtime / "live" / "robinhood" / "trade_counter.json").read_text())
    assert counter["count"] == 1
    accepted = [r for r in _read_audit_records(live_runtime) if r["kind"] == "order_placed"]
    assert len(accepted) == 1
    # H5: the redacted audit record is embedded under the frozen marker key.
    assert out[order_guard.LIVE_ACTION_RESULT_KEY]["kind"] == "order_placed"


def test_post_write_ledger_failure_halts_and_is_returned(
    live_runtime: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A broker write followed by ledger failure is surfaced and freezes trading."""
    from src.live.halt import halt_flag_set

    _write_mandate(live_runtime, _mandate())
    adapter = _MockAdapter(positions=[], balance=5000.0)
    guard = _guard(adapter)

    def fail_completion(*args, **kwargs):
        raise OrderLedgerError("disk unavailable")

    monkeypatch.setattr(order_guard, "complete_order", fail_completion)
    out = json.loads(
        _execute(
            guard,
            symbol="AAPL",
            side="buy",
            instrument_type="equity",
            notional_usd=100.0,
        )
    )

    assert len(adapter.order_calls) == 1
    assert out["safety_halt"] is True
    assert "disk unavailable" in out["ledger_error"]
    assert halt_flag_set("robinhood") is True


# --------------------------------------------------------------------------- #
# H3 — notional+quantity ambiguity bypass is closed                           #
# --------------------------------------------------------------------------- #


class _QuoteAdapter:
    """Adapter with a working ``get_quotes`` read tool returning a fixed price."""

    def __init__(self, *, price: float, positions: Any = (), balance: Any = 5000.0) -> None:
        self.server_name = "robinhood"
        self._price = price
        self._positions = list(positions)
        self._balance = balance
        self.order_calls: list[dict[str, Any]] = []
        self.quote_calls: list[dict[str, Any]] = []

    def call_tool(self, remote_name: str, arguments: dict, *, local_name: str | None = None) -> dict:
        if remote_name == "get_positions":
            return {"positions": self._positions, "status": "ok"}
        if remote_name == "get_account":
            return {"account": {"equity": self._balance, "currency": "USD"}, "status": "ok"}
        if remote_name == "list_orders":
            return {"open_orders": [], "status": "ok"}
        if remote_name == "get_quotes":
            self.quote_calls.append({"arguments": arguments})
            return {
                "status": "ok",
                "symbol": arguments.get("symbol"),
                "bid": self._price,
                "ask": self._price,
                "last": self._price,
                "time": datetime.now(timezone.utc).isoformat(),
                "currency": "USD",
            }
        self.order_calls.append({"remote": remote_name, "arguments": arguments})
        return {"status": "ok", "order_id": "rh_test_1", "state": "accepted"}


def test_notional_quantity_bypass_is_closed(live_runtime: Path) -> None:
    """H3: an order carrying a tiny notional but a huge quantity is enforced on
    the quantity-implied notional, so it is NOT waved through."""
    _write_mandate(live_runtime, _mandate())  # max_order_notional_usd = 750
    # 100000 shares * $10 = $1,000,000 implied notional, well over the cap; the
    # explicit $10 notional must not be the value enforced.
    adapter = _QuoteAdapter(price=10.0)
    guard = _guard(adapter)

    out = json.loads(
        _execute(guard,
            symbol="AAPL", side="buy", instrument_type="equity",
            notional_usd=10.0, quantity=100000.0,
        )
    )
    assert out["status"] == "blocked"
    assert out["breach"]["limit"] == "max_order_notional_usd"
    assert out["breach"]["attempted_value"] == 1_000_000.0
    assert adapter.order_calls == []  # never forwarded
    assert adapter.quote_calls  # broker quote tool was consulted
