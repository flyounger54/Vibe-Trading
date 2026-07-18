"""Pre-trade mandate gate for DIRECT-SDK connectors (SPEC Mandate Enforcement §3).

The MCP :class:`~src.live.order_guard.LiveOrderGuardTool` gates Robinhood by
wrapping a remote MCP tool. Direct-SDK connectors (tiger / alpaca / okx /
binance / futu) place orders through a normal Python call, not an MCP tool, so
they need a function-based gate with the SAME ceremony, all fail-closed before
any order reaches the broker:

1. ``load_mandate`` — no valid mandate / unknown schema version → DENY.
2. expiry — past ``consent.expires_at`` → DENY (routes to re-auth).
3. ``halt_flag_set`` — kill switch tripped → DENY, no broker call.
4. notional normalization — a ``quantity`` order is priced (connector quote →
   data loaders) and enforced on the LARGER of explicit notional and
   ``quantity × price``; fail-closed DENY when unpriceable.
5. read positions + balance via the connector's own READ functions.
6. ``check_mandate`` — ALLOW → ``connector.place_order`` / DENY (structural) /
   PAUSE_FOR_REAUTH (quantitative).
7. exact live qualification — broker + account + build + policy must be in an
   active, unexpired pilot state before ``connector.place_order`` is invoked.

A daily count is consumed only on a confirmed ALLOW whose ``place_order``
returned a non-error envelope. Every decision writes one audit event and the
returned payload carries the redacted record under ``live_action``.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any

from src.live.audit import LiveActionEvent, write_live_action
from src.live.daily_count import increment_daily_count, read_daily_count
from src.live.enforcement import (
    BREACH_KIND_INSTRUMENT,
    BREACH_KIND_UNIVERSE,
    OrderIntent,
    check_mandate,
    instrument_asset_class,
    last_price_usd,
)
from src.live.halt import halt_flag_set
from src.live.halt import trip_halt
from src.live.execution_risk import (
    check_execution_risk,
    normalize_account_equity_usd,
    normalize_order_notional,
    normalize_quote,
    observe_daily_loss,
    open_order_reservations_usd,
)
from src.live.mandate.model import MANDATE_SCHEMA_VERSION, Mandate
from src.live.mandate.store import load_mandate
from src.live.qualification import QualificationDecision, evaluate_live_qualification
from src.live.order_ledger import (
    OrderLedgerError,
    OrderOutcome,
    accepted_client_order_ids,
    claim_order,
    complete_order,
)
from src.live.runtime.reconcile import reconcile

logger = logging.getLogger(__name__)

LIVE_ACTION_RESULT_KEY = "live_action"
_REMOTE_TOOL = "place_order"

_DECISION_ALLOW = "allow"
_DECISION_DENY = "deny"
_DECISION_PAUSE = "pause_for_reauth"


def execute_live_order(
    *,
    broker: str,
    connector_module: Any,
    config: Any,
    intent: OrderIntent,
    place_kwargs: dict[str, Any],
    session_id: str = "",
) -> dict[str, Any]:
    """Run the live mandate gate around a direct-SDK ``place_order``.

    Args:
        broker: Broker key (mandate/halt/counter/audit are keyed by this).
        connector_module: The connector's ``sdk`` module (provides
            ``place_order``/``get_positions``/``get_account_snapshot``/``get_quote``).
        config: The connector config object for a LIVE profile.
        intent: Normalized :class:`OrderIntent` built from the tool args.
        place_kwargs: Keyword args forwarded verbatim to ``connector_module.place_order``
            on ALLOW (``symbol``/``side``/``quantity``/``notional``/``order_type``/
            ``limit_price``/``time_in_force``).
        session_id: Originating session id, stamped onto audit events.

    Returns:
        On ALLOW: the connector's ``place_order`` result dict (with a
        ``live_action`` record attached). Otherwise a refusal envelope
        ``{"status":"blocked","decision",...}``.
    """
    broker = (broker or "").strip().lower()

    mandate = load_mandate(broker)
    if mandate is None or mandate.schema_version != MANDATE_SCHEMA_VERSION:
        return _deny(broker, session_id, "no valid mandate on file", ["mandate"], mandate, intent=None)

    if mandate.execution_controls is None:
        return _deny(
            broker,
            session_id,
            "mandate has no Node 12B execution controls",
            ["mandate", "execution_controls"],
            mandate,
            intent=intent,
        )

    if _is_expired(mandate):
        return _deny(broker, session_id, "mandate expired — re-authorize", ["mandate", "expiry"], mandate, intent=None, reauth=True)

    if halt_flag_set(broker):
        return _deny(broker, session_id, "live trading halted", ["mandate", "expiry", "halt_flag"], mandate, intent=None)

    channel = f"live:{mandate.consent.account_ref}"
    fingerprint = _order_fingerprint(intent, place_kwargs)
    try:
        claim = claim_order(broker, channel, intent.client_order_id or "", fingerprint)
    except OrderLedgerError as exc:
        return _deny(
            broker,
            session_id,
            f"order ledger unavailable: {exc}",
            ["mandate", "expiry", "halt_flag", "client_order_id"],
            mandate,
            intent=intent,
        )
    if claim.action == "replay":
        replay = dict(claim.result or {})
        replay["idempotency_replayed"] = True
        replay["client_order_id"] = intent.client_order_id
        return replay
    if claim.action in {"pending", "conflict"}:
        reason = (
            "client_order_id is already pending; broker call will not be retried"
            if claim.action == "pending"
            else "client_order_id was already used for a different order"
        )
        return _deny(
            broker,
            session_id,
            reason,
            ["mandate", "expiry", "halt_flag", "client_order_id"],
            mandate,
            intent=intent,
        )

    quote_payload = _connector_quote(connector_module, config, intent.symbol)
    quote = normalize_quote(quote_payload, symbol=intent.symbol)
    normalized = normalize_order_notional(intent, quote) if quote is not None else None
    if quote is None or normalized is None:
        refusal = _deny(
            broker, session_id, "quantity order notional could not be priced (fail-closed)",
            ["mandate", "expiry", "halt_flag", "quote_time", "fx"], mandate, intent=intent,
        )
        return _complete_blocked(broker, channel, intent, fingerprint, refusal)
    intent = normalized

    positions = _safe_read(connector_module, "get_positions", config)
    balance = _safe_read(connector_module, "get_account_snapshot", config)
    open_orders = _safe_read(connector_module, "get_open_orders", config)
    position_rows = _payload_rows(positions, "positions")
    order_rows = _payload_rows(open_orders, "open_orders")
    if position_rows is None or order_rows is None or not isinstance(balance, dict):
        refusal = _deny(
            broker,
            session_id,
            "positions, balance, or open orders unavailable (fail-closed)",
            ["broker_snapshot"],
            mandate,
            intent=intent,
        )
        return _complete_blocked(broker, channel, intent, fingerprint, refusal)

    try:
        report = reconcile(
            broker,
            lambda: position_rows,
            lambda: balance,
            lambda: order_rows,
        )
    except Exception as exc:  # noqa: BLE001 - corrupt/failed reconcile denies
        refusal = _deny(
            broker,
            session_id,
            f"reconciliation failed: {exc}",
            ["reconciliation"],
            mandate,
            intent=intent,
        )
        return _complete_blocked(broker, channel, intent, fingerprint, refusal)
    if not report.is_safe:
        refusal = _deny(
            broker,
            session_id,
            "reconciliation is unsafe; order was not sent",
            ["reconciliation"],
            mandate,
            intent=intent,
        )
        return _complete_blocked(broker, channel, intent, fingerprint, refusal)

    reservations = open_order_reservations_usd(
        open_orders,
        max_fx_age_seconds=mandate.execution_controls.max_quote_age_seconds,
        max_clock_drift_seconds=mandate.execution_controls.max_clock_drift_seconds,
    )
    equity = normalize_account_equity_usd(
        balance, default_currency=_default_account_currency(broker)
    )
    if reservations is None or equity is None:
        refusal = _deny(
            broker,
            session_id,
            "open-order reservations or account equity could not be normalized",
            ["open_order_reservations", "account_equity", "fx"],
            mandate,
            intent=intent,
        )
        return _complete_blocked(broker, channel, intent, fingerprint, refusal)
    try:
        daily_loss = observe_daily_loss(broker, channel, equity)
    except Exception as exc:  # noqa: BLE001 - corrupt state denies
        refusal = _deny(
            broker,
            session_id,
            f"daily-loss state unavailable: {exc}",
            ["max_daily_loss_usd"],
            mandate,
            intent=intent,
        )
        return _complete_blocked(broker, channel, intent, fingerprint, refusal)

    risk_breach = check_execution_risk(
        mandate.execution_controls,
        intent,
        quote,
        daily_loss_usd=daily_loss,
    )
    if risk_breach is not None:
        refusal = _deny(
            broker,
            session_id,
            f"{risk_breach.code}: {risk_breach.detail}",
            [risk_breach.code],
            mandate,
            intent=intent,
        )
        return _complete_blocked(broker, channel, intent, fingerprint, refusal)

    daily_count = read_daily_count(broker)

    breach = check_mandate(
        mandate, intent, positions, balance,
        broker=broker, remote_tool=_REMOTE_TOOL, daily_count=daily_count,
        reserved_notional_usd=reservations,
    )

    if breach is None:
        qualification = evaluate_live_qualification(broker, mandate.consent.account_ref)
        if not qualification.allowed:
            refusal = _deny_qualification(
                broker, session_id, mandate, intent, qualification
            )
            return _complete_blocked(
                broker, channel, intent, fingerprint, refusal
            )
        return _allow(
            broker,
            session_id,
            connector_module,
            config,
            intent,
            place_kwargs,
            mandate,
            qualification,
            channel,
            fingerprint,
        )

    reauth = breach.kind not in (BREACH_KIND_UNIVERSE, BREACH_KIND_INSTRUMENT)
    refusal = _deny_breach(broker, session_id, breach, mandate, intent, reauth)
    return _complete_blocked(broker, channel, intent, fingerprint, refusal)


def execute_paper_order(
    *,
    broker: str,
    profile_id: str,
    connector_module: Any,
    config: Any,
    intent: OrderIntent,
    place_kwargs: dict[str, Any],
) -> dict[str, Any]:
    """Execute a paper order through the same intent, risk, and ledger path.

    Paper trading uses the prospective broker mandate as the exact risk policy
    that a later live pilot would enforce.  It omits only live qualification and
    the real-money audit sink; its durable order ledger remains the paper-soak
    evidence source.
    """
    broker = str(broker or "").strip().lower()
    mandate = load_mandate(broker)
    if (
        mandate is None
        or mandate.schema_version != MANDATE_SCHEMA_VERSION
        or mandate.execution_controls is None
        or _is_expired(mandate)
    ):
        return _paper_refusal(broker, "paper qualification requires a valid mandate v2")
    if halt_flag_set(broker):
        return _paper_refusal(broker, "trading halted")

    channel = f"paper:{profile_id}"
    fingerprint = _order_fingerprint(intent, place_kwargs)
    try:
        claim = claim_order(broker, channel, intent.client_order_id or "", fingerprint)
    except OrderLedgerError as exc:
        return _paper_refusal(broker, f"order ledger unavailable: {exc}")
    if claim.action == "replay":
        replay = dict(claim.result or {})
        replay["idempotency_replayed"] = True
        replay["client_order_id"] = intent.client_order_id
        return replay
    if claim.action == "pending":
        return _paper_refusal(broker, "client_order_id is pending; order was not re-sent")
    if claim.action == "conflict":
        return _paper_refusal(broker, "client_order_id conflicts with another order")

    def block(reason: str) -> dict[str, Any]:
        refusal = _paper_refusal(broker, reason)
        return _complete_blocked(broker, channel, intent, fingerprint, refusal)

    quote = normalize_quote(
        _connector_quote(connector_module, config, intent.symbol),
        symbol=intent.symbol,
    )
    normalized = normalize_order_notional(intent, quote) if quote is not None else None
    if quote is None or normalized is None:
        return block("fresh USD-normalized quote unavailable")
    intent = normalized

    positions = _safe_read(connector_module, "get_positions", config)
    balance = _safe_read(connector_module, "get_account_snapshot", config)
    open_orders = _safe_read(connector_module, "get_open_orders", config)
    position_rows = _payload_rows(positions, "positions")
    order_rows = _payload_rows(open_orders, "open_orders")
    if position_rows is None or order_rows is None or not isinstance(balance, dict):
        return block("paper broker snapshot unavailable")
    try:
        report = reconcile(
            f"{broker}-paper",
            lambda: position_rows,
            lambda: balance,
            lambda: order_rows,
            authorized_client_order_ids=tuple(
                accepted_client_order_ids(broker, channel)
            ),
        )
    except Exception as exc:  # noqa: BLE001
        return block(f"paper reconciliation failed: {exc}")
    if not report.is_safe:
        return block("paper reconciliation is unsafe")

    reservations = open_order_reservations_usd(
        open_orders,
        max_fx_age_seconds=mandate.execution_controls.max_quote_age_seconds,
        max_clock_drift_seconds=mandate.execution_controls.max_clock_drift_seconds,
    )
    equity = normalize_account_equity_usd(
        balance, default_currency=_default_account_currency(broker)
    )
    if reservations is None or equity is None:
        return block("paper reservations or account equity are not USD-normalized")
    try:
        daily_loss = observe_daily_loss(broker, channel, equity)
    except Exception as exc:  # noqa: BLE001
        return block(f"paper daily-loss state unavailable: {exc}")
    risk_breach = check_execution_risk(
        mandate.execution_controls,
        intent,
        quote,
        daily_loss_usd=daily_loss,
    )
    if risk_breach is not None:
        return block(f"{risk_breach.code}: {risk_breach.detail}")

    counter_key = f"{broker}-paper"
    breach = check_mandate(
        mandate,
        intent,
        positions,
        balance,
        broker=broker,
        remote_tool=_REMOTE_TOOL,
        daily_count=read_daily_count(counter_key),
        reserved_notional_usd=reservations,
    )
    if breach is not None:
        return block(breach.detail or f"paper order breaches {breach.limit}")

    request = {**place_kwargs, "client_order_id": intent.client_order_id}
    raised = False
    try:
        result = connector_module.place_order(config, **request)
    except Exception as exc:  # noqa: BLE001
        raised = True
        result = {"status": "error", "error": str(exc)}
    result_dict = result if isinstance(result, dict) else {
        "status": "error", "error": "non-dict broker result"
    }
    is_error = str(result_dict.get("status", "")).lower() != "ok"
    outcome: OrderOutcome = "ambiguous" if raised else "error" if is_error else "accepted"
    try:
        complete_order(
            broker,
            channel,
            intent.client_order_id or "",
            fingerprint,
            outcome=outcome,
            result=result_dict,
        )
    except OrderLedgerError as exc:
        return {
            **result_dict,
            "status": "error",
            "safety_error": f"paper order ledger completion failed: {exc}",
        }
    if not is_error:
        increment_daily_count(counter_key)
    return {**result_dict, "client_order_id": intent.client_order_id}


# --------------------------------------------------------------------------- #
# Decision helpers
# --------------------------------------------------------------------------- #


def _paper_refusal(broker: str, reason: str) -> dict[str, Any]:
    """Return the stable paper safety-gate refusal envelope."""
    return {
        "status": "blocked",
        "decision": _DECISION_DENY,
        "reason": reason,
        "broker": broker,
        "requires_reauthorization": False,
    }


def _allow(
    broker,
    session_id,
    connector_module,
    config,
    intent,
    place_kwargs,
    mandate,
    qualification: QualificationDecision,
    channel: str,
    fingerprint: str,
) -> dict[str, Any]:
    """Durably audit intent, execute once, finalize ledger, and audit outcome."""
    checked = [
        "mandate", "expiry", "halt_flag", "client_order_id", "reconciliation",
        "quote_freshness", "clock_drift", "fx_normalization", "price_deviation",
        "max_daily_loss_usd", "open_order_reservations", "exclude_symbols",
        "allowed_instruments", "asset_classes", "max_order_notional_usd",
        "max_total_exposure_usd", "max_leverage", "max_trades_per_day",
        "account_funding_usd", "universe_floors", "live_qualification",
    ]
    request = {**place_kwargs, "client_order_id": intent.client_order_id}
    pre_record = _audit(
        broker,
        session_id,
        kind="order_submitted",
        outcome="accepted",
        mandate=mandate,
        intent=intent,
        broker_request=request,
        broker_response=None,
        gate_decision={
            "allowed": True,
            "decision": _DECISION_ALLOW,
            "phase": "pre_write",
            "checked_limits": checked,
            "qualification": qualification.to_dict(),
        },
    )
    if pre_record is None:
        refusal = _refusal(
            broker,
            decision=_DECISION_DENY,
            reason="live audit unavailable before broker write",
            reauth=False,
        )
        return _complete_blocked(broker, channel, intent, fingerprint, refusal)

    raised = False
    try:
        result = connector_module.place_order(config, **request)
    except Exception as exc:  # noqa: BLE001 - a connector raise must not escape the gate
        logger.warning("live place_order raised for %s: %s", broker, exc)
        raised = True
        result = {"status": "error", "error": str(exc)}

    is_error = not isinstance(result, dict) or str(result.get("status", "")).lower() != "ok"
    result_dict = result if isinstance(result, dict) else {"status": "error", "error": "non-dict broker result"}
    ledger_outcome: OrderOutcome = "ambiguous" if raised else "error" if is_error else "accepted"
    try:
        complete_order(
            broker,
            channel,
            intent.client_order_id or "",
            fingerprint,
            outcome=ledger_outcome,
            result=result_dict,
        )
    except OrderLedgerError as exc:
        trip_halt("order_ledger", f"post-write ledger failure: {exc}", broker)
        result_dict = {
            **result_dict,
            "safety_halt": True,
            "ledger_error": str(exc),
        }
    if is_error:
        record = _audit(
            broker, session_id, kind="order_rejected", outcome="error", mandate=mandate, intent=intent,
            broker_request=request, broker_response=result_dict,
            gate_decision={
                "allowed": True,
                "decision": _DECISION_ALLOW,
                "checked_limits": checked,
                "qualification": qualification.to_dict(),
            },
            error=_error_message(result),
        )
    else:
        increment_daily_count(broker)
        record = _audit(
            broker, session_id, kind="order_placed", outcome="accepted", mandate=mandate, intent=intent,
            broker_request=request, broker_response=result_dict,
            gate_decision={
                "allowed": True,
                "decision": _DECISION_ALLOW,
                "checked_limits": checked,
                "qualification": qualification.to_dict(),
            },
        )
    if record is None:
        trip_halt("live_audit", "post-write live audit failure", broker)
        result_dict = {
            **result_dict,
            "safety_halt": True,
            "audit_error": "post-write live audit failed",
            LIVE_ACTION_RESULT_KEY: pre_record,
        }
    else:
        result_dict = {**result_dict, LIVE_ACTION_RESULT_KEY: record}
    result_dict["client_order_id"] = intent.client_order_id
    return result_dict


def _deny(broker, session_id, reason, checked, mandate, *, intent, reauth=False) -> dict[str, Any]:
    """Audit + return a refusal for a pre-check / structural DENY."""
    record = _audit(
        broker, session_id, kind="order_rejected", outcome="blocked", mandate=mandate, intent=intent,
        broker_request=None, broker_response=None,
        gate_decision={"allowed": False, "decision": _DECISION_DENY, "checked_limits": checked},
        error=reason,
    )
    return _refusal(broker, decision=_DECISION_DENY, reason=reason, reauth=reauth, record=record)


def _deny_qualification(
    broker: str,
    session_id: str,
    mandate: Mandate,
    intent: OrderIntent,
    qualification: QualificationDecision,
) -> dict[str, Any]:
    """Audit and deny immediately before the connector write boundary."""
    snapshot = qualification.to_dict()
    record = _audit(
        broker,
        session_id,
        kind="order_rejected",
        outcome="blocked",
        mandate=mandate,
        intent=intent,
        broker_request=None,
        broker_response=None,
        gate_decision={
            "allowed": False,
            "decision": "qualification_required",
            "checked_limits": ["mandate", "risk", "live_qualification"],
            "qualification": snapshot,
        },
        error=qualification.reason,
    )
    return _refusal(
        broker,
        decision="qualification_required",
        reason=qualification.reason,
        reauth=False,
        record=record,
        qualification=snapshot,
    )


def _deny_breach(broker, session_id, breach, mandate, intent, reauth) -> dict[str, Any]:
    """Audit + return a refusal for a ``check_mandate`` breach."""
    decision = _DECISION_PAUSE if reauth else _DECISION_DENY
    record = _audit(
        broker, session_id, kind="breach", outcome="blocked", mandate=mandate, intent=intent,
        broker_request=None, broker_response=None,
        gate_decision={
            "allowed": False, "decision": decision, "limit": breach.limit, "kind": breach.kind,
            "limit_value": breach.limit_value, "attempted_value": breach.attempted_value,
        },
        error=breach.detail or f"order breaches {breach.limit}",
    )
    return _refusal(
        broker, decision=decision, reason=breach.detail or f"order breaches {breach.limit}",
        reauth=reauth, breach=breach, record=record,
    )


def _refusal(
    broker,
    *,
    decision,
    reason,
    reauth,
    breach=None,
    record=None,
    qualification=None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": "blocked",
        "decision": decision,
        "reason": reason,
        "broker": broker,
        "requires_reauthorization": reauth,
    }
    if record is not None:
        payload[LIVE_ACTION_RESULT_KEY] = record
    if qualification is not None:
        payload["qualification"] = qualification
    if breach is not None:
        payload["breach"] = {
            "broker": breach.broker, "limit": breach.limit, "limit_value": breach.limit_value,
            "attempted_value": breach.attempted_value, "overage": breach.overage,
            "kind": breach.kind, "detail": breach.detail,
            "proposed_action": {
                "symbol": breach.proposed_action.symbol, "side": breach.proposed_action.side,
                "notional_usd": breach.proposed_action.notional_usd, "quantity": breach.proposed_action.quantity,
                "instrument_type": breach.proposed_action.instrument_type.value,
            },
        }
    return payload


# --------------------------------------------------------------------------- #
# Notional normalization + reads
# --------------------------------------------------------------------------- #


def _order_fingerprint(intent: OrderIntent, place_kwargs: dict[str, Any]) -> str:
    """Hash the canonical economic intent without persisting raw arguments."""
    payload = {
        "symbol": intent.symbol,
        "side": intent.side,
        "notional_usd": intent.notional_usd,
        "quantity": intent.quantity,
        "instrument_type": intent.instrument_type.value,
        "asset_class": intent.asset_class.value if intent.asset_class else None,
        "order_type": intent.order_type,
        "limit_price": intent.limit_price,
        "time_in_force": place_kwargs.get("time_in_force"),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _complete_blocked(
    broker: str,
    channel: str,
    intent: OrderIntent,
    fingerprint: str,
    refusal: dict[str, Any],
) -> dict[str, Any]:
    """Make a claimed order terminal without ever reaching the broker."""
    try:
        complete_order(
            broker,
            channel,
            intent.client_order_id or "",
            fingerprint,
            outcome="blocked",
            result=refusal,
        )
    except OrderLedgerError as exc:
        return {**refusal, "ledger_error": str(exc)}
    return refusal


def _connector_quote(connector_module: Any, config: Any, symbol: str) -> object:
    """Return the broker quote envelope; failures remain fail-closed."""
    getter = getattr(connector_module, "get_quote", None)
    if getter is None:
        return None
    try:
        return getter(symbol, config=config)
    except Exception as exc:  # noqa: BLE001
        logger.warning("connector quote failed for %s: %s", symbol, exc)
        return None


def _payload_rows(payload: object, key: str) -> list[dict[str, Any]] | None:
    """Extract a normalized list from a successful connector envelope."""
    if isinstance(payload, list):
        rows: object = payload
    elif isinstance(payload, dict):
        rows = payload.get(key)
    else:
        return None
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        return None
    return rows


def _default_account_currency(broker: str) -> str | None:
    """Return a structurally-known account reporting currency, if any."""
    return {
        "alpaca": "USD",
        "tiger": "USD",
        "robinhood": "USD",
        "okx": "USD",
    }.get(broker)


def _normalize_notional(intent: OrderIntent, connector_module: Any, config: Any) -> OrderIntent | None:
    """Stamp a single authoritative ``notional_usd`` (quantity → priced).

    Currency note: the connector quote is the broker's native currency (HKD for
    HK, CNH for A-share). The mandate caps are USD; treating a local-currency
    figure as USD OVER-states USD exposure for HKD/CNH (≈7-8x), so the caps bind
    CONSERVATIVELY (over-deny, never under-deny). FX normalization is a follow-up
    before HK/CN are promoted past the structural asset-class gate.
    """
    if intent.quantity is None:
        return intent
    price = _quote_price(intent, connector_module, config)
    if price is None:
        return None
    implied = intent.quantity * price
    if implied != implied or implied <= 0:
        return None
    explicit = intent.notional_usd if intent.notional_usd is not None else 0.0
    enforced = max(float(explicit), implied)
    return OrderIntent(
        symbol=intent.symbol, side=intent.side, notional_usd=enforced,
        quantity=intent.quantity, instrument_type=intent.instrument_type, asset_class=intent.asset_class,
        client_order_id=intent.client_order_id, order_type=intent.order_type,
        limit_price=intent.limit_price,
    )


def _quote_price(intent: OrderIntent, connector_module: Any, config: Any) -> float | None:
    """Live USD price for the intent symbol: connector quote first, loaders next."""
    broker_price = _connector_quote_price(connector_module, config, intent.symbol)
    if broker_price is not None:
        return broker_price
    asset_class = intent.asset_class or instrument_asset_class(intent.instrument_type)
    if asset_class is None:
        return None
    try:
        return last_price_usd(intent.symbol, asset_class)
    except Exception as exc:  # noqa: BLE001 - loader failure → fail-closed
        logger.warning("loader quote failed for %s: %s", intent.symbol, exc)
        return None


def _connector_quote_price(connector_module: Any, config: Any, symbol: str) -> float | None:
    """Parse a positive price from the connector's ``get_quote`` envelope."""
    getter = getattr(connector_module, "get_quote", None)
    if getter is None:
        return None
    try:
        result = getter(symbol, config=config)
    except Exception as exc:  # noqa: BLE001
        logger.warning("connector quote failed for %s: %s", symbol, exc)
        return None
    if not isinstance(result, dict) or str(result.get("status", "")).lower() == "error":
        return None
    quote = result.get("quote")
    if not isinstance(quote, dict):
        return None
    for key in ("last", "ask", "bid", "close"):
        if key in quote:
            try:
                value = float(quote[key])
            except (TypeError, ValueError):
                continue
            if value == value and value > 0:
                return value
    return None


def _safe_read(connector_module: Any, fn_name: str, config: Any) -> object:
    """Call a connector read fn, returning ``None`` on any error (fail-closed)."""
    fn = getattr(connector_module, fn_name, None)
    if fn is None:
        return None
    try:
        result = fn(config)
    except Exception as exc:  # noqa: BLE001
        logger.warning("connector read %s failed: %s", fn_name, exc)
        return None
    if isinstance(result, dict) and str(result.get("status", "")).lower() == "error":
        return None
    return result


# --------------------------------------------------------------------------- #
# Audit + misc
# --------------------------------------------------------------------------- #


def _audit(broker, session_id, *, kind, outcome, mandate, intent, broker_request, broker_response, gate_decision, error=None) -> dict | None:
    consent = mandate.consent if mandate is not None else None
    try:
        event = LiveActionEvent(
            kind=kind,
            session_id=session_id,
            outcome=outcome,
            server=broker,
            remote_tool=_REMOTE_TOOL,
            intent_normalized=_describe_intent(intent),
            mandate_snapshot_ref=consent.consent_token_sha256 if consent else None,
            consent_record_ref=consent.account_ref if consent else None,
            broker_request=broker_request,
            broker_response=broker_response,
            gate_decision=gate_decision,
            error=error,
        )
        try:
            return write_live_action(event, event_callback=None, trace_writer=None)
        except TypeError:
            return write_live_action(event)
    except Exception as exc:  # auditing must never block a decision
        logger.warning("live-action audit write failed (%s): %s", kind, exc)
        return None


def _is_expired(mandate: Mandate) -> bool:
    raw = mandate.consent.expires_at
    try:
        expires = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return True
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) >= expires


def _error_message(result: object) -> str:
    if isinstance(result, dict):
        for key in ("error", "message", "detail"):
            value = result.get(key)
            if isinstance(value, str) and value:
                return value
    return "broker order returned an error"


def _describe_intent(intent: OrderIntent | None) -> str | None:
    if intent is None:
        return None
    size = (
        f"${intent.notional_usd:g}" if intent.notional_usd is not None
        else f"{intent.quantity:g} units" if intent.quantity is not None
        else "?"
    )
    return f"{intent.side} {size} {intent.symbol} ({intent.instrument_type.value})"
