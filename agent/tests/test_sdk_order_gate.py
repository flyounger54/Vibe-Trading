"""Tests for the direct-SDK live order gate + service order routing (Layer B/C).

The gate is the red-line code: live orders must pass mandate + kill-switch +
fail-closed pre-trade checks before any broker call. These tests use a fake
connector module + a stubbed mandate/halt so they need no broker SDK.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.live import paths as live_paths
from src.live import sdk_order_gate as gate
from src.live.enforcement import OrderIntent
from src.live.mandate.model import (
    AssetClass,
    ConsentMeta,
    ExecutionControls,
    HardCaps,
    InstrumentType,
    Mandate,
    MANDATE_SCHEMA_VERSION,
    UniverseConstraint,
)
from src.live.qualification import QualificationDecision, QualificationState
from src.trading import service

pytestmark = pytest.mark.unit


class _FakeConnector:
    """Minimal connector module stand-in capturing place_order calls."""

    def __init__(self, *, positions=None, balance=None, quote_last=100.0):
        self.placed: list[dict] = []
        self._positions = positions if positions is not None else {"status": "ok", "positions": []}
        self._balance = balance if balance is not None else {
            "status": "ok", "account": {"equity": 1_000_000, "currency": "USD"}
        }
        self._quote_last = quote_last

    def place_order(self, config, **kwargs):
        self.placed.append(kwargs)
        return {"status": "ok", "order_id": "OID-1", **kwargs}

    def get_positions(self, config):
        return self._positions

    def get_account_snapshot(self, config):
        return self._balance

    def get_quote(self, symbol, *, config=None):
        return {
            "status": "ok",
            "symbol": symbol,
            "quote": {
                "bid": self._quote_last,
                "ask": self._quote_last,
                "last": self._quote_last,
                "time": datetime.now(timezone.utc).isoformat(),
                "currency": "USD",
            },
        }

    def get_open_orders(self, config):
        return {"status": "ok", "open_orders": []}


def _mandate(*, max_order=1_000_000.0, assets=(AssetClass.US_EQUITY,), instruments=(InstrumentType.EQUITY,)):
    return Mandate(
        schema_version=MANDATE_SCHEMA_VERSION,
        hard_caps=HardCaps(
            account_funding_usd=1_000_000.0,
            max_order_notional_usd=max_order,
            max_total_exposure_usd=1_000_000.0,
            max_leverage=2.0,
            allowed_instruments=tuple(instruments),
            max_trades_per_day=100,
        ),
        universe=UniverseConstraint(
            asset_classes=tuple(assets),
            min_market_cap_usd=None,
            min_avg_daily_volume_usd=None,
            exclude_symbols=(),
        ),
        consent=ConsentMeta(
            created_at="2026-01-01T00:00:00+00:00",
            consent_token_sha256="deadbeef",
            broker="alpaca",
            account_ref="acct-1",
            expires_at="2999-01-01T00:00:00+00:00",
        ),
        execution_controls=ExecutionControls(
            max_daily_loss_usd=10_000.0,
            max_price_deviation_bps=100.0,
            max_quote_age_seconds=30.0,
            max_clock_drift_seconds=5.0,
        ),
    )


def _qualification(*, allowed: bool = True) -> QualificationDecision:
    return QualificationDecision(
        allowed=allowed,
        code="qualified" if allowed else "live_broker_not_enabled",
        reason="test qualification" if allowed else "live execution is disabled",
        broker="alpaca",
        account_ref="acct-1",
        build_revision="e9f54e0ef19054a690690bdb3c12fa2154b20ebb",
        policy_version="node12a-live-qualification-v1",
        state=QualificationState.PILOT_ACTIVE if allowed else QualificationState.DISABLED,
        observed_trading_days=30 if allowed else 0,
    )


def _patch_gate(monkeypatch, *, mandate, halted=False, qualified=True):
    monkeypatch.setattr(gate, "load_mandate", lambda broker: mandate)
    monkeypatch.setattr(gate, "halt_flag_set", lambda broker: halted)
    monkeypatch.setattr(gate, "write_live_action", lambda *a, **k: {"audited": True})
    monkeypatch.setattr(gate, "read_daily_count", lambda broker: 0)
    monkeypatch.setattr(gate, "increment_daily_count", lambda broker: 1)
    monkeypatch.setattr(
        gate,
        "claim_order",
        lambda *args, **kwargs: SimpleNamespace(action="claimed", result=None),
    )
    monkeypatch.setattr(gate, "complete_order", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        gate,
        "reconcile",
        lambda *args, **kwargs: SimpleNamespace(is_safe=True),
    )
    monkeypatch.setattr(gate, "observe_daily_loss", lambda *args, **kwargs: 0.0)
    monkeypatch.setattr(
        gate,
        "evaluate_live_qualification",
        lambda broker, account_ref: _qualification(allowed=qualified),
    )


def _intent(notional=500.0, qty=None, asset=AssetClass.US_EQUITY):
    return OrderIntent(
        symbol="AAPL", side="buy", notional_usd=notional, quantity=qty,
        instrument_type=InstrumentType.EQUITY, asset_class=asset,
        client_order_id="vt_test_order_0001", order_type="market",
    )


# --------------------------------------------------------------------------- #
# Gate decisions
# --------------------------------------------------------------------------- #


def test_gate_denies_without_mandate(monkeypatch) -> None:
    _patch_gate(monkeypatch, mandate=None)
    conn = _FakeConnector()
    out = gate.execute_live_order(
        broker="alpaca", connector_module=conn, config=object(),
        intent=_intent(), place_kwargs={"symbol": "AAPL", "side": "buy", "notional": 500.0},
    )
    assert out["status"] == "blocked" and out["decision"] == "deny"
    assert "mandate" in out["reason"]
    assert conn.placed == []  # never reached the broker


def test_gate_denies_on_halt(monkeypatch) -> None:
    _patch_gate(monkeypatch, mandate=_mandate(), halted=True)
    conn = _FakeConnector()
    out = gate.execute_live_order(
        broker="alpaca", connector_module=conn, config=object(),
        intent=_intent(), place_kwargs={"symbol": "AAPL", "side": "buy", "notional": 500.0},
    )
    assert out["status"] == "blocked"
    assert conn.placed == []
    assert "halt" in out["reason"].lower()


def test_gate_allows_in_bounds_and_places(monkeypatch) -> None:
    _patch_gate(monkeypatch, mandate=_mandate())
    conn = _FakeConnector()
    out = gate.execute_live_order(
        broker="alpaca", connector_module=conn, config=object(),
        intent=_intent(notional=500.0), place_kwargs={"symbol": "AAPL", "side": "buy", "notional": 500.0},
    )
    assert out["status"] == "ok" and out["order_id"] == "OID-1"
    assert len(conn.placed) == 1  # forwarded to broker
    assert "live_action" in out


def test_gate_blocks_at_write_boundary_without_live_qualification(monkeypatch) -> None:
    _patch_gate(monkeypatch, mandate=_mandate(), qualified=False)
    conn = _FakeConnector()

    out = gate.execute_live_order(
        broker="alpaca",
        connector_module=conn,
        config=object(),
        intent=_intent(notional=500.0),
        place_kwargs={"symbol": "AAPL", "side": "buy", "notional": 500.0},
    )

    assert out["status"] == "blocked"
    assert out["decision"] == "qualification_required"
    assert out["qualification"]["allowed"] is False
    assert conn.placed == []


def test_gate_blocks_oversized_order(monkeypatch) -> None:
    _patch_gate(monkeypatch, mandate=_mandate(max_order=100.0))
    conn = _FakeConnector()
    out = gate.execute_live_order(
        broker="alpaca", connector_module=conn, config=object(),
        intent=_intent(notional=5000.0), place_kwargs={"symbol": "AAPL", "side": "buy", "notional": 5000.0},
    )
    assert out["status"] == "blocked"
    assert conn.placed == []
    assert out["decision"] in ("pause_for_reauth", "deny")


def test_gate_blocks_disallowed_asset_class(monkeypatch) -> None:
    # Mandate allows only US equity; an HK-equity order must be denied structurally.
    _patch_gate(monkeypatch, mandate=_mandate(assets=(AssetClass.US_EQUITY,)))
    conn = _FakeConnector()
    out = gate.execute_live_order(
        broker="tiger", connector_module=conn, config=object(),
        intent=_intent(asset=AssetClass.HK_EQUITY),
        place_kwargs={"symbol": "700.HK", "side": "buy", "notional": 500.0},
    )
    assert out["status"] == "blocked" and out["decision"] == "deny"
    assert conn.placed == []


def test_gate_quantity_order_priced_and_enforced(monkeypatch) -> None:
    # quantity-only order: gate prices via connector quote (last=100) → 10*100=1000 notional.
    _patch_gate(monkeypatch, mandate=_mandate(max_order=500.0))
    conn = _FakeConnector(quote_last=100.0)
    out = gate.execute_live_order(
        broker="alpaca", connector_module=conn, config=object(),
        intent=_intent(notional=None, qty=10.0),
        place_kwargs={"symbol": "AAPL", "side": "buy", "quantity": 10.0},
    )
    # 1000 > max_order 500 → blocked
    assert out["status"] == "blocked"
    assert conn.placed == []


# --------------------------------------------------------------------------- #
# Service routing
# --------------------------------------------------------------------------- #


def test_service_place_order_paper_uses_shared_safety_path(monkeypatch) -> None:
    """Paper profile constructs the same OrderIntent and enters the paper gate."""
    conn = _FakeConnector()
    captured: dict = {}
    monkeypatch.setattr(service, "_sdk_module", lambda c: conn)
    monkeypatch.setattr(conn, "build_config", lambda *a, **k: object(), raising=False)
    # build_config is called on the module; give the fake one.
    conn.build_config = lambda profile_config, overrides: object()
    def fake_paper(**kwargs):
        captured.update(kwargs)
        return {"status": "ok", "order_id": "paper-1"}

    monkeypatch.setattr("src.live.sdk_order_gate.execute_paper_order", fake_paper)
    out = service.place_order(
        "AAPL",
        "alpaca-paper-trade",
        side="buy",
        quantity=1,
        client_order_id="vt_paper_order_0001",
    )
    assert out["status"] == "ok"
    assert conn.placed == []
    assert captured["intent"].client_order_id == "vt_paper_order_0001"
    assert out["environment"] == "paper"


def test_service_place_order_live_routes_through_gate(monkeypatch) -> None:
    """Live profile routes through the gate; no mandate → blocked, not placed."""
    conn = _FakeConnector()
    conn.build_config = lambda profile_config, overrides: object()
    monkeypatch.setattr(service, "_sdk_module", lambda c: conn)
    monkeypatch.setattr("src.live.sdk_order_gate.load_mandate", lambda broker: None)
    monkeypatch.setattr("src.live.sdk_order_gate.write_live_action", lambda *a, **k: {"audited": True})
    out = service.place_order(
        "AAPL", "alpaca-live-trade", side="buy", notional=500.0,
        client_order_id="vt_live_order_0001",
    )
    assert out["status"] == "blocked"
    assert conn.placed == []
    assert out["environment"] == "live"


def test_service_live_cancel_audits_before_and_after_without_gating(
    monkeypatch,
) -> None:
    profile = SimpleNamespace(
        id="alpaca-live-trade",
        connector="alpaca",
        transport="broker_sdk",
        environment="live",
        config={},
    )

    class _CancelConnector:
        @staticmethod
        def build_config(config, overrides):
            return object()

        @staticmethod
        def cancel_order(config, order_id, *, symbol=None):
            return {"status": "ok", "order_id": order_id, "symbol": symbol}

    audit_kinds: list[str] = []
    monkeypatch.setattr(service, "profile_by_id", lambda profile_id: profile)
    monkeypatch.setattr(service, "_sdk_module", lambda connector: _CancelConnector)
    monkeypatch.setattr(
        service,
        "_audit_live_cancel",
        lambda *args, kind, **kwargs: audit_kinds.append(kind),
    )

    out = service.cancel_order("OID-1", "alpaca-live-trade", symbol="AAPL")

    assert out["status"] == "ok"
    assert audit_kinds == ["cancel_requested", "order_cancelled"]


def test_paper_gate_persists_idempotency_and_replays_without_resend(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(live_paths, "get_runtime_root", lambda: tmp_path)
    monkeypatch.setattr(gate, "load_mandate", lambda broker: _mandate())
    monkeypatch.setattr(gate, "halt_flag_set", lambda broker: False)
    monkeypatch.setattr(gate, "read_daily_count", lambda broker: 0)
    monkeypatch.setattr(gate, "increment_daily_count", lambda broker: 1)
    conn = _FakeConnector()
    intent = _intent(notional=None, qty=1)
    kwargs = {
        "symbol": "AAPL",
        "side": "buy",
        "quantity": 1,
        "notional": None,
        "order_type": "market",
        "limit_price": None,
        "time_in_force": "day",
    }

    first = gate.execute_paper_order(
        broker="alpaca",
        profile_id="alpaca-paper-trade",
        connector_module=conn,
        config=object(),
        intent=intent,
        place_kwargs=kwargs,
    )
    replay = gate.execute_paper_order(
        broker="alpaca",
        profile_id="alpaca-paper-trade",
        connector_module=conn,
        config=object(),
        intent=intent,
        place_kwargs=kwargs,
    )

    assert first["status"] == "ok"
    assert replay["status"] == "ok" and replay["idempotency_replayed"] is True
    assert len(conn.placed) == 1


def test_live_gate_blocks_before_connector_when_prewrite_audit_fails(monkeypatch) -> None:
    _patch_gate(monkeypatch, mandate=_mandate())
    monkeypatch.setattr(gate, "write_live_action", lambda *args, **kwargs: None)
    conn = _FakeConnector()

    out = gate.execute_live_order(
        broker="alpaca",
        connector_module=conn,
        config=object(),
        intent=_intent(),
        place_kwargs={"symbol": "AAPL", "side": "buy", "notional": 500.0},
    )

    assert out["status"] == "blocked"
    assert "audit unavailable" in out["reason"]
    assert conn.placed == []


def test_no_longbridge_live_trade_profile() -> None:
    from src.trading import profiles

    ids = {p.id for p in profiles.list_profiles()}
    assert "longbridge-paper-trade" in ids
    assert "longbridge-live-trade" not in ids  # capped: no live order placement


def test_trade_profiles_have_place_capability() -> None:
    from src.trading import profiles

    for pid in ("alpaca-live-trade", "okx-live-trade", "binance-live-trade", "futu-live-trade", "tiger-live-trade"):
        prof = profiles.profile_by_id(pid)
        assert prof.readonly is False
        assert any("requires_mandate" in c for c in prof.capabilities)


# --------------------------------------------------------------------------- #
# Gate edges: expiry, count-only-on-success, connector raise, unpriceable qty
# --------------------------------------------------------------------------- #


def _expired_mandate():
    m = _mandate()
    return Mandate(
        schema_version=MANDATE_SCHEMA_VERSION, hard_caps=m.hard_caps, universe=m.universe,
        consent=ConsentMeta(
            created_at="2020-01-01T00:00:00+00:00", consent_token_sha256="x",
            broker="alpaca", account_ref="a", expires_at="2020-02-01T00:00:00+00:00",
        ),
        execution_controls=m.execution_controls,
    )


def test_gate_denies_expired_mandate(monkeypatch) -> None:
    _patch_gate(monkeypatch, mandate=_expired_mandate())
    conn = _FakeConnector()
    out = gate.execute_live_order(
        broker="alpaca", connector_module=conn, config=object(),
        intent=_intent(), place_kwargs={"symbol": "AAPL", "side": "buy", "notional": 500.0},
    )
    assert out["status"] == "blocked" and out["requires_reauthorization"] is True
    assert conn.placed == []


def test_gate_count_consumed_only_on_success(monkeypatch) -> None:
    increments: list[str] = []
    _patch_gate(monkeypatch, mandate=_mandate())
    monkeypatch.setattr(gate, "increment_daily_count", lambda b: increments.append(b))

    # Connector returns an error envelope → no count consumed.
    class _ErrConn(_FakeConnector):
        def place_order(self, config, **kwargs):
            return {"status": "error", "error": "broker rejected"}

    out = gate.execute_live_order(
        broker="alpaca", connector_module=_ErrConn(), config=object(),
        intent=_intent(), place_kwargs={"symbol": "AAPL", "side": "buy", "notional": 500.0},
    )
    assert out["status"] == "error"
    assert increments == []  # failed placement must not consume a daily count


def test_gate_connector_raise_is_caught(monkeypatch) -> None:
    _patch_gate(monkeypatch, mandate=_mandate())

    class _RaiseConn(_FakeConnector):
        def place_order(self, config, **kwargs):
            raise RuntimeError("sdk boom")

    out = gate.execute_live_order(
        broker="alpaca", connector_module=_RaiseConn(), config=object(),
        intent=_intent(), place_kwargs={"symbol": "AAPL", "side": "buy", "notional": 500.0},
    )
    assert out["status"] == "error"  # raise converted to error envelope, not propagated


def test_gate_quantity_unpriceable_denies(monkeypatch) -> None:
    _patch_gate(monkeypatch, mandate=_mandate())

    class _NoQuoteConn(_FakeConnector):
        def get_quote(self, symbol, *, config=None):
            return {"status": "error", "error": "no quote"}

    # Force the loader fallback to also fail so pricing is impossible.
    monkeypatch.setattr("src.live.sdk_order_gate.last_price_usd", lambda *a, **k: None)
    out = gate.execute_live_order(
        broker="alpaca", connector_module=_NoQuoteConn(), config=object(),
        intent=_intent(notional=None, qty=5.0),
        place_kwargs={"symbol": "AAPL", "side": "buy", "quantity": 5.0},
    )
    assert out["status"] == "blocked" and "priced" in out["reason"]


def _execute_live(connector: object) -> dict[str, object]:
    return gate.execute_live_order(
        broker="alpaca",
        connector_module=connector,
        config=object(),
        intent=_intent(),
        place_kwargs={"symbol": "AAPL", "side": "buy", "notional": 500.0},
    )


@pytest.mark.parametrize("action", ["pending", "conflict"])
def test_live_gate_rejects_nonterminal_ledger_claims(monkeypatch, action: str) -> None:
    _patch_gate(monkeypatch, mandate=_mandate())
    monkeypatch.setattr(
        gate,
        "claim_order",
        lambda *args, **kwargs: SimpleNamespace(action=action, result=None),
    )
    conn = _FakeConnector()

    out = _execute_live(conn)

    assert out["status"] == "blocked"
    assert "client_order_id" in str(out["reason"])
    assert conn.placed == []


def test_live_gate_replays_terminal_claim_without_broker_write(monkeypatch) -> None:
    _patch_gate(monkeypatch, mandate=_mandate())
    monkeypatch.setattr(
        gate,
        "claim_order",
        lambda *args, **kwargs: SimpleNamespace(
            action="replay", result={"status": "ok", "order_id": "old-order"}
        ),
    )
    conn = _FakeConnector()

    out = _execute_live(conn)

    assert out["idempotency_replayed"] is True
    assert out["order_id"] == "old-order"
    assert conn.placed == []


def test_live_gate_rejects_unavailable_ledger(monkeypatch) -> None:
    _patch_gate(monkeypatch, mandate=_mandate())

    def fail_claim(*args, **kwargs):
        raise gate.OrderLedgerError("tampered")

    monkeypatch.setattr(gate, "claim_order", fail_claim)
    out = _execute_live(_FakeConnector())
    assert out["status"] == "blocked"
    assert "ledger unavailable" in str(out["reason"])


def test_live_gate_requires_node12b_execution_controls(monkeypatch) -> None:
    mandate = SimpleNamespace(
        schema_version=MANDATE_SCHEMA_VERSION,
        execution_controls=None,
        consent=SimpleNamespace(account_ref="acct-1", consent_token_sha256="hash"),
    )
    _patch_gate(monkeypatch, mandate=mandate)

    out = _execute_live(_FakeConnector())

    assert out["status"] == "blocked"
    assert "execution controls" in str(out["reason"])


@pytest.mark.parametrize("mode", ["missing_snapshot", "reconcile_error", "unsafe"])
def test_live_gate_snapshot_and_reconciliation_fail_closed(
    monkeypatch, mode: str
) -> None:
    _patch_gate(monkeypatch, mandate=_mandate())
    conn = _FakeConnector()
    if mode == "missing_snapshot":
        conn._positions = {"status": "error", "error": "offline"}
    elif mode == "reconcile_error":
        monkeypatch.setattr(
            gate,
            "reconcile",
            lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("corrupt")),
        )
    else:
        monkeypatch.setattr(
            gate, "reconcile", lambda *args, **kwargs: SimpleNamespace(is_safe=False)
        )

    out = _execute_live(conn)

    assert out["status"] == "blocked"
    assert conn.placed == []


@pytest.mark.parametrize("mode", ["reservations", "equity", "daily_loss", "risk"])
def test_live_gate_normalization_and_risk_fail_closed(monkeypatch, mode: str) -> None:
    _patch_gate(monkeypatch, mandate=_mandate())
    if mode == "reservations":
        monkeypatch.setattr(gate, "open_order_reservations_usd", lambda *a, **k: None)
    elif mode == "equity":
        monkeypatch.setattr(gate, "normalize_account_equity_usd", lambda *a, **k: None)
    elif mode == "daily_loss":
        monkeypatch.setattr(
            gate,
            "observe_daily_loss",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("state corrupt")),
        )
    else:
        monkeypatch.setattr(
            gate,
            "check_execution_risk",
            lambda *a, **k: SimpleNamespace(code="stale_quote", detail="stale"),
        )

    conn = _FakeConnector()
    out = _execute_live(conn)

    assert out["status"] == "blocked"
    assert conn.placed == []


def test_live_gate_marks_postwrite_ledger_and_audit_failures(monkeypatch) -> None:
    _patch_gate(monkeypatch, mandate=_mandate())
    audits = iter(({"phase": "pre"}, None))
    monkeypatch.setattr(gate, "write_live_action", lambda *a, **k: next(audits))

    def fail_complete(*args, **kwargs):
        raise gate.OrderLedgerError("disk failed")

    halts: list[str] = []
    monkeypatch.setattr(gate, "complete_order", fail_complete)
    monkeypatch.setattr(
        gate, "trip_halt", lambda reason, detail, broker: halts.append(reason)
    )

    out = _execute_live(_FakeConnector())

    assert out["status"] == "ok"
    assert out["safety_halt"] is True
    assert out["ledger_error"] == "disk failed"
    assert out["audit_error"] == "post-write live audit failed"
    assert halts == ["order_ledger", "live_audit"]


def test_live_gate_handles_non_object_broker_result(monkeypatch) -> None:
    _patch_gate(monkeypatch, mandate=_mandate())

    class _NonObjectConnector(_FakeConnector):
        def place_order(self, config, **kwargs):
            return "unexpected"

    out = _execute_live(_NonObjectConnector())

    assert out["status"] == "error"
    assert out["error"] == "non-dict broker result"


@pytest.mark.parametrize("action", ["replay", "pending", "conflict"])
def test_paper_gate_honors_ledger_claim_state(monkeypatch, action: str) -> None:
    _patch_gate(monkeypatch, mandate=_mandate())
    result = {"status": "ok", "order_id": "existing"} if action == "replay" else None
    monkeypatch.setattr(
        gate,
        "claim_order",
        lambda *args, **kwargs: SimpleNamespace(action=action, result=result),
    )
    conn = _FakeConnector()

    out = gate.execute_paper_order(
        broker="alpaca",
        profile_id="alpaca-paper-trade",
        connector_module=conn,
        config=object(),
        intent=_intent(),
        place_kwargs={"symbol": "AAPL", "side": "buy", "notional": 500.0},
    )

    if action == "replay":
        assert out["idempotency_replayed"] is True
    else:
        assert out["status"] == "blocked"
    assert conn.placed == []


def test_paper_gate_rejects_ledger_and_halt_failures(monkeypatch) -> None:
    _patch_gate(monkeypatch, mandate=_mandate(), halted=True)
    halted = gate.execute_paper_order(
        broker="alpaca",
        profile_id="alpaca-paper-trade",
        connector_module=_FakeConnector(),
        config=object(),
        intent=_intent(),
        place_kwargs={},
    )
    assert halted["status"] == "blocked"

    _patch_gate(monkeypatch, mandate=_mandate())

    def fail_claim(*args, **kwargs):
        raise gate.OrderLedgerError("ledger offline")

    monkeypatch.setattr(gate, "claim_order", fail_claim)
    unavailable = gate.execute_paper_order(
        broker="alpaca",
        profile_id="alpaca-paper-trade",
        connector_module=_FakeConnector(),
        config=object(),
        intent=_intent(),
        place_kwargs={},
    )
    assert "ledger unavailable" in str(unavailable["reason"])


def test_paper_gate_requires_valid_mandate_and_fresh_quote(monkeypatch) -> None:
    _patch_gate(monkeypatch, mandate=None)
    invalid = gate.execute_paper_order(
        broker="alpaca",
        profile_id="alpaca-paper-trade",
        connector_module=_FakeConnector(),
        config=object(),
        intent=_intent(),
        place_kwargs={},
    )
    assert invalid["status"] == "blocked"

    _patch_gate(monkeypatch, mandate=_mandate())
    no_quote = _FakeConnector()
    no_quote.get_quote = lambda symbol, config=None: {"status": "error"}
    missing = gate.execute_paper_order(
        broker="alpaca",
        profile_id="alpaca-paper-trade",
        connector_module=no_quote,
        config=object(),
        intent=_intent(),
        place_kwargs={},
    )
    assert missing["status"] == "blocked"
    assert "quote unavailable" in str(missing["reason"])


def test_paper_gate_requires_normalized_reservations(monkeypatch) -> None:
    _patch_gate(monkeypatch, mandate=_mandate())
    monkeypatch.setattr(gate, "open_order_reservations_usd", lambda *a, **k: None)

    out = gate.execute_paper_order(
        broker="alpaca",
        profile_id="alpaca-paper-trade",
        connector_module=_FakeConnector(),
        config=object(),
        intent=_intent(),
        place_kwargs={},
    )

    assert out["status"] == "blocked"
    assert "USD-normalized" in str(out["reason"])


@pytest.mark.parametrize("mode", ["snapshot", "reconcile_error", "unsafe", "daily_loss", "risk", "mandate"])
def test_paper_gate_rejects_untrusted_runtime_state(monkeypatch, mode: str) -> None:
    _patch_gate(monkeypatch, mandate=_mandate())
    conn = _FakeConnector()
    if mode == "snapshot":
        conn._positions = {"status": "error"}
    elif mode == "reconcile_error":
        monkeypatch.setattr(
            gate,
            "reconcile",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("corrupt")),
        )
    elif mode == "unsafe":
        monkeypatch.setattr(gate, "reconcile", lambda *a, **k: SimpleNamespace(is_safe=False))
    elif mode == "daily_loss":
        monkeypatch.setattr(
            gate,
            "observe_daily_loss",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("corrupt")),
        )
    elif mode == "risk":
        monkeypatch.setattr(
            gate,
            "check_execution_risk",
            lambda *a, **k: SimpleNamespace(code="daily_loss", detail="limit"),
        )
    else:
        monkeypatch.setattr(
            gate,
            "check_mandate",
            lambda *a, **k: SimpleNamespace(detail="mandate breach", limit="max_order"),
        )

    out = gate.execute_paper_order(
        broker="alpaca",
        profile_id="alpaca-paper-trade",
        connector_module=conn,
        config=object(),
        intent=_intent(),
        place_kwargs={"symbol": "AAPL", "side": "buy", "notional": 500.0},
    )

    assert out["status"] == "blocked"
    assert conn.placed == []


@pytest.mark.parametrize("mode", ["raise", "non_object", "ledger"])
def test_paper_gate_persists_terminal_broker_failures(monkeypatch, mode: str) -> None:
    _patch_gate(monkeypatch, mandate=_mandate())

    class _FailureConnector(_FakeConnector):
        def place_order(self, config, **kwargs):
            if mode == "raise":
                raise RuntimeError("broker unavailable")
            if mode == "non_object":
                return "unexpected"
            return super().place_order(config, **kwargs)

    if mode == "ledger":
        monkeypatch.setattr(
            gate,
            "complete_order",
            lambda *a, **k: (_ for _ in ()).throw(gate.OrderLedgerError("disk")),
        )
    out = gate.execute_paper_order(
        broker="alpaca",
        profile_id="alpaca-paper-trade",
        connector_module=_FailureConnector(),
        config=object(),
        intent=_intent(),
        place_kwargs={"symbol": "AAPL", "side": "buy", "notional": 500.0},
    )

    assert out["status"] == "error"
    if mode == "ledger":
        assert out["safety_error"] == "paper order ledger completion failed: disk"


def test_sdk_gate_low_level_fail_closed_helpers(monkeypatch) -> None:
    real_quote_price = gate._quote_price
    assert gate._connector_quote(SimpleNamespace(), object(), "AAPL") is None
    raising_quote = SimpleNamespace(
        get_quote=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("offline"))
    )
    assert gate._connector_quote(raising_quote, object(), "AAPL") is None

    assert gate._payload_rows([{"symbol": "AAPL"}], "positions") == [
        {"symbol": "AAPL"}
    ]
    assert gate._payload_rows({"positions": ["bad"]}, "positions") is None

    plain = _intent(notional=100.0, qty=None)
    assert gate._normalize_notional(plain, object(), object()) is plain
    quantity = _intent(notional=None, qty=2.0)
    monkeypatch.setattr(gate, "_quote_price", lambda *a: None)
    assert gate._normalize_notional(quantity, object(), object()) is None
    monkeypatch.setattr(gate, "_quote_price", lambda *a: 25.0)
    normalized = gate._normalize_notional(quantity, object(), object())
    assert normalized is not None and normalized.notional_usd == 50.0
    monkeypatch.setattr(gate, "_connector_quote_price", lambda *a: 123.0)
    assert real_quote_price(quantity, object(), object()) == 123.0


def test_sdk_gate_blocked_completion_surfaces_ledger_error(monkeypatch) -> None:
    monkeypatch.setattr(
        gate,
        "complete_order",
        lambda *a, **k: (_ for _ in ()).throw(gate.OrderLedgerError("disk")),
    )
    refusal = {"status": "blocked"}

    out = gate._complete_blocked(
        "alpaca", "paper:test", _intent(), "f" * 64, refusal
    )

    assert out["ledger_error"] == "disk"


# --------------------------------------------------------------------------- #
# Connector order-method validation (fail-closed, no SDK needed)
# --------------------------------------------------------------------------- #


def test_longbridge_place_order_paper_only_guard() -> None:
    from src.trading.connectors.longbridge import sdk as lb

    cfg = lb.LongbridgeConfig(app_key="k", app_secret="s", access_token="t", profile="live-readonly")
    out = lb.place_order(cfg, symbol="700.HK", side="buy", quantity=100)
    assert out["status"] == "error" and "paper" in out["error"].lower()
    out2 = lb.cancel_order(cfg, "OID", symbol="700.HK")
    assert out2["status"] == "error" and "paper" in out2["error"].lower()


@pytest.mark.parametrize("connector", ["tiger", "alpaca", "okx", "binance", "futu", "longbridge"])
def test_connector_place_order_rejects_bad_side(connector) -> None:
    import importlib

    mod = importlib.import_module(f"src.trading.connectors.{connector}.sdk")
    cfg = mod.build_config({"profile": "paper"}, None)
    out = mod.place_order(cfg, symbol="AAPL", side="hold", quantity=1)
    assert out["status"] == "error"


@pytest.mark.parametrize("connector", ["tiger", "alpaca", "okx", "binance", "futu", "longbridge"])
def test_connector_place_order_rejects_both_qty_and_notional(connector) -> None:
    import importlib

    mod = importlib.import_module(f"src.trading.connectors.{connector}.sdk")
    cfg = mod.build_config({"profile": "paper"}, None)
    out = mod.place_order(cfg, symbol="AAPL", side="buy", quantity=1, notional=100)
    assert out["status"] == "error"


def test_okx_order_result_rejects_failed_scode() -> None:
    from src.trading.connectors.okx import sdk as ox

    cfg = ox.OKXConfig(api_key="k", api_secret="s", passphrase="p")
    # A 200 envelope (code 0) whose per-order sCode != 0 is a FAILED order.
    failed = ox._order_result(cfg, {"code": "0", "data": [{"sCode": "51008", "sMsg": "insufficient"}]}, symbol="BTC-USDT", side="buy", order_type="market", time_in_force="day")
    assert failed["status"] == "error"
    ok = ox._order_result(cfg, {"code": "0", "data": [{"ordId": "O1", "sCode": "0"}]}, symbol="BTC-USDT", side="buy", order_type="market", time_in_force="day")
    assert ok["status"] == "ok" and ok["order_id"] == "O1"
