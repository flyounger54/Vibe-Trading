"""Pre-trade enforcement gate (SPEC.md Mandate Enforcement §3).

:class:`LiveOrderGuardTool` is the dedicated wrapper that owns the live-order
gate. It subclasses :class:`~src.tools.mcp.MCPRemoteTool` and is instantiated
only for a broker's order-placing (WRITE/UNKNOWN) remote tools; every read tool
keeps the untouched plain ``MCPRemoteTool.execute()`` path with no gate.

On every ``execute()`` it runs, in order and **all fail-closed** before any
broker call:

1. ``load_mandate`` — no valid mandate / unknown schema version → DENY.
2. expiry — past ``consent.expires_at`` → DENY (routes to re-auth).
3. ``halt_flag_set`` — kill switch tripped → DENY, NO remote call.
4. ``extract_order_intent`` — unparseable order → DENY.
5. read positions + balance via the broker's READ MCP tools (plain path).
6. ``check_mandate`` — ALLOW (forward via ``super().execute``) / DENY
   (structural: universe|instrument) / PAUSE_FOR_REAUTH (quantitative).
7. exact live qualification — broker + account + build + policy must be in an
   active, unexpired pilot state before the remote order tool is invoked.

The daily ``trade_counter.json`` is incremented only on a confirmed ALLOW whose
forwarded broker result is **non-error** (``MCPServerAdapter.call_tool`` returns
an error envelope, it does not raise — a failed forward never placed an order and
never consumes a count), with UTC-date rollover. Every decision writes one
live-action audit event via :func:`src.live.audit.write_live_action`, and the
returned tool_result carries that redacted record under the frozen
``"live_action"`` key so the api_server SSE relay can emit a ``live.action``
event without touching the agent loop.

When the order is sized by ``quantity``, the gate derives a live quote (broker
``get_quotes`` READ tool first, then the data loaders) and enforces the LARGER of
the explicit notional and ``quantity`` × price — fail-closed DENY when no quote
is obtainable — so the notional/exposure/leverage caps stay enforceable.

``repeatable = False`` mirrors the no-retry stance in
``MCPServerAdapter._call_tool`` — a live order must never be silently re-issued.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Callable

from src.live.audit import LiveActionEvent, LiveActionKind, LiveActionOutcome, write_live_action
from src.live.enforcement import (
    BREACH_KIND_INSTRUMENT,
    BREACH_KIND_UNIVERSE,
    BreachEvent,
    OrderIntent,
    check_mandate,
    instrument_asset_class,
    last_price_usd,
)
from src.live.extractors import get_extractor
from src.live.execution_risk import (
    check_execution_risk,
    normalize_account_equity_usd,
    normalize_order_notional,
    normalize_quote,
    observe_daily_loss,
    open_order_reservations_usd,
)
from src.live.halt import halt_flag_set, trip_halt
from src.live.mandate.model import MANDATE_SCHEMA_VERSION, Mandate
from src.live.mandate.store import load_mandate
from src.live.daily_count import increment_daily_count, read_daily_count
from src.live.qualification import QualificationDecision, evaluate_live_qualification
from src.live.order_ledger import OrderLedgerError, OrderOutcome, claim_order, complete_order
from src.live.runtime.reconcile import reconcile
from src.tools.mcp import MCPRemoteTool, MCPRemoteToolSpec, MCPServerAdapter

logger = logging.getLogger(__name__)

#: Frozen marker key the api_server SSE relay reads off the returned tool_result
#: to emit a ``live.action`` event without touching the agent loop.
LIVE_ACTION_RESULT_KEY = "live_action"

#: Fallback READ tools the gate uses to snapshot positions/balance and live
#: quotes. Connector mappings in ``src.trading`` override these when available.
_POSITIONS_TOOLS = ("get_positions",)
_BALANCE_TOOLS = ("get_account",)
_QUOTE_TOOLS = ("get_quotes",)
_ORDERS_TOOLS = ("list_orders",)

_DECISION_ALLOW = "allow"
_DECISION_DENY = "deny"
_DECISION_PAUSE = "pause_for_reauth"


class LiveOrderGuardTool(MCPRemoteTool):
    """Mandate-enforcing wrapper for a broker's order-placing remote tool."""

    repeatable = False
    is_readonly = False

    def __init__(
        self,
        adapter: MCPServerAdapter,
        spec: MCPRemoteToolSpec,
        *,
        broker: str | None = None,
        session_id: str = "",
        qualification_check: Callable[[str, str], QualificationDecision] | None = None,
    ) -> None:
        """Initialize the gate wrapper.

        Args:
            adapter: Adapter used to invoke the remote server (read + write).
            spec: Resolved local metadata for the order-placing remote tool.
            broker: Broker key for mandate/counter/halt lookups. Defaults to the
                spec's ``server_name`` (the channel is keyed by broker, e.g.
                ``"robinhood"``).
            session_id: Originating session id, stamped onto audit events.
            qualification_check: Injectable Node 12A qualification evaluator.
                Production uses the protected registry; tests may supply a
                deterministic decision without touching operator state.
        """
        super().__init__(adapter, spec)
        self.broker = (broker or spec.server_name or "").strip().lower()
        self.session_id = session_id
        self._qualification_check = qualification_check or evaluate_live_qualification

    @property
    def remote_name(self) -> str:
        """The broker's un-prefixed remote tool name (e.g. ``place_order``)."""
        return self._spec.remote_name

    def execute(self, **kwargs: Any) -> str:
        """Run the pre-trade gate, then ALLOW / DENY / PAUSE the order.

        Args:
            **kwargs: Order-tool arguments from the agent loop.

        Returns:
            JSON string: on ALLOW, the forwarded broker result; otherwise a
            structured refusal envelope (``status: "blocked"``) carrying the
            decision and, for quantitative breaches, the :class:`BreachEvent`.
        """
        mandate = load_mandate(self.broker)
        if mandate is None or mandate.schema_version != MANDATE_SCHEMA_VERSION:
            return self._deny(
                reason="no valid mandate on file",
                checked=["mandate"],
                mandate=mandate,
            )
        if mandate.execution_controls is None:
            return self._deny(
                reason="mandate has no Node 12B execution controls",
                checked=["mandate", "execution_controls"],
                mandate=mandate,
            )

        if self._is_expired(mandate):
            return self._deny(
                reason="mandate expired — re-authorize",
                checked=["mandate", "expiry"],
                mandate=mandate,
                reauth=True,
            )

        if halt_flag_set(self.broker):
            return self._deny(
                reason="live trading halted",
                checked=["mandate", "expiry", "halt_flag"],
                mandate=mandate,
            )

        extractor = get_extractor(self.broker)
        intent = extractor(self.remote_name, kwargs) if extractor is not None else None
        if intent is None:
            return self._deny(
                reason="order intent could not be parsed",
                checked=["mandate", "expiry", "halt_flag", "intent"],
                mandate=mandate,
            )

        channel = f"live:{mandate.consent.account_ref}"
        fingerprint = _order_fingerprint(intent, kwargs)
        try:
            claim = claim_order(
                self.broker,
                channel,
                intent.client_order_id or "",
                fingerprint,
            )
        except OrderLedgerError as exc:
            return self._deny(
                reason=f"order ledger unavailable: {exc}",
                checked=["mandate", "expiry", "halt_flag", "client_order_id"],
                mandate=mandate,
            )
        if claim.action == "replay":
            replay = dict(claim.result or {})
            replay["idempotency_replayed"] = True
            replay["client_order_id"] = intent.client_order_id
            return json.dumps(replay, ensure_ascii=False)
        if claim.action in {"pending", "conflict"}:
            reason = (
                "client_order_id is already pending; broker call will not be retried"
                if claim.action == "pending"
                else "client_order_id was already used for a different order"
            )
            return self._deny(
                reason=reason,
                checked=["mandate", "expiry", "halt_flag", "client_order_id"],
                mandate=mandate,
            )

        # Reconcile any quantity into a single authoritative notional BEFORE the
        # mandate checks so a {notional_usd, quantity} pair can't bypass the
        # notional cap (H3) and a quantity-only order stays cap-enforceable (H4).
        quote = normalize_quote(
            self._broker_quote_payload(intent.symbol),
            symbol=intent.symbol,
        )
        intent = normalize_order_notional(intent, quote) if quote is not None else None
        if quote is None or intent is None:
            refusal = self._deny(
                reason="quantity order notional could not be priced (fail-closed)",
                checked=["mandate", "expiry", "halt_flag", "intent", "quote_time", "fx"],
                mandate=mandate,
            )
            return self._complete_blocked(channel, fingerprint, kwargs, refusal)

        positions = self._read_first(self._read_tools("positions", _POSITIONS_TOOLS))
        balance = self._read_first(self._read_tools("account", _BALANCE_TOOLS))
        open_orders = self._read_first(self._read_tools("orders", _ORDERS_TOOLS))
        position_rows = _payload_rows(positions, "positions")
        order_rows = _payload_rows(open_orders, "open_orders", fallback_key="orders")
        if position_rows is None or order_rows is None or not isinstance(balance, dict):
            refusal = self._deny(
                reason="positions, balance, or open orders unavailable (fail-closed)",
                checked=["broker_snapshot"],
                mandate=mandate,
            )
            return self._complete_blocked(channel, fingerprint, kwargs, refusal)
        try:
            report = reconcile(
                self.broker,
                lambda: position_rows,
                lambda: balance,
                lambda: order_rows,
            )
        except Exception as exc:  # noqa: BLE001
            refusal = self._deny(
                reason=f"reconciliation failed: {exc}",
                checked=["reconciliation"],
                mandate=mandate,
            )
            return self._complete_blocked(channel, fingerprint, kwargs, refusal)
        if not report.is_safe:
            refusal = self._deny(
                reason="reconciliation is unsafe; order was not sent",
                checked=["reconciliation"],
                mandate=mandate,
            )
            return self._complete_blocked(channel, fingerprint, kwargs, refusal)

        reservations = open_order_reservations_usd(
            open_orders,
            max_fx_age_seconds=mandate.execution_controls.max_quote_age_seconds,
            max_clock_drift_seconds=mandate.execution_controls.max_clock_drift_seconds,
        )
        equity = normalize_account_equity_usd(balance, default_currency="USD")
        if reservations is None or equity is None:
            refusal = self._deny(
                reason="open-order reservations or account equity could not be normalized",
                checked=["open_order_reservations", "account_equity", "fx"],
                mandate=mandate,
            )
            return self._complete_blocked(channel, fingerprint, kwargs, refusal)
        try:
            daily_loss = observe_daily_loss(self.broker, channel, equity)
        except Exception as exc:  # noqa: BLE001
            refusal = self._deny(
                reason=f"daily-loss state unavailable: {exc}",
                checked=["max_daily_loss_usd"],
                mandate=mandate,
            )
            return self._complete_blocked(channel, fingerprint, kwargs, refusal)
        risk_breach = check_execution_risk(
            mandate.execution_controls,
            intent,
            quote,
            daily_loss_usd=daily_loss,
        )
        if risk_breach is not None:
            refusal = self._deny(
                reason=f"{risk_breach.code}: {risk_breach.detail}",
                checked=[risk_breach.code],
                mandate=mandate,
            )
            return self._complete_blocked(channel, fingerprint, kwargs, refusal)
        daily_count = self._read_daily_count()

        breach = check_mandate(
            mandate,
            intent,
            positions,
            balance,
            broker=self.broker,
            remote_tool=self.remote_name,
            daily_count=daily_count,
            reserved_notional_usd=reservations,
        )

        if breach is None:
            qualification = self._qualification_check(
                self.broker, mandate.consent.account_ref
            )
            if not qualification.allowed:
                return self._deny_qualification(
                    channel=channel,
                    fingerprint=fingerprint,
                    kwargs=kwargs,
                    mandate=mandate,
                    intent=intent,
                    qualification=qualification,
                )
            return self._allow(
                mandate=mandate,
                intent=intent,
                kwargs=kwargs,
                qualification=qualification,
                channel=channel,
                fingerprint=fingerprint,
            )

        if breach.kind in (BREACH_KIND_UNIVERSE, BREACH_KIND_INSTRUMENT):
            refusal = self._deny_breach(breach, mandate=mandate, intent=intent, reauth=False)
        else:
            refusal = self._deny_breach(breach, mandate=mandate, intent=intent, reauth=True)
        return self._complete_blocked(channel, fingerprint, kwargs, refusal)

    # -- intent normalization (quantity → notional) -------------------------

    def _normalize_intent_notional(self, intent: OrderIntent) -> OrderIntent | None:
        """Stamp a single authoritative ``notional_usd`` onto the intent.

        Closes two bypasses (SPEC §4):

        * **H3** — an order carrying BOTH ``notional_usd`` and ``quantity`` is
          enforced on the LARGER of (explicit notional, ``quantity`` × live
          price), so a small notional can't smuggle a huge quantity past the
          notional / exposure / leverage math.
        * **H4** — a quantity-only order derives its notional from a live quote
          so the notional cap stays enforceable.

        Fail-closed: when ``quantity`` is present but NO quote can be obtained
        from the broker quote tool or any data loader, the order is DENIED
        (returns ``None``) rather than waved through. When no ``quantity`` is
        present the intent passes through unchanged (its explicit notional, if
        any, is validated downstream).

        Args:
            intent: The extractor's normalized intent.

        Returns:
            A new :class:`OrderIntent` with ``notional_usd`` set to the enforced
            value, or ``None`` when a quantity order cannot be priced.
        """
        if intent.quantity is None:
            return intent

        price = self._quote_price(intent)
        if price is None:
            return None
        implied = intent.quantity * price
        if implied != implied or implied <= 0:  # NaN / non-positive → fail-closed
            return None

        explicit = intent.notional_usd if intent.notional_usd is not None else 0.0
        enforced = max(float(explicit), implied)
        return OrderIntent(
            symbol=intent.symbol,
            side=intent.side,
            notional_usd=enforced,
            quantity=intent.quantity,
            instrument_type=intent.instrument_type,
            asset_class=intent.asset_class,
            client_order_id=intent.client_order_id,
            order_type=intent.order_type,
            limit_price=intent.limit_price,
        )

    def _quote_price(self, intent: OrderIntent) -> float | None:
        """Return a live USD price for the intent's symbol, fail-closed.

        Prefers the broker's READ quote tool (``get_quotes``) so the price is
        the broker's own; falls back to Vibe-Trading's data loaders
        (:func:`src.live.enforcement.last_price_usd`, standard auto-fallback)
        when the broker quote is unavailable. Returns ``None`` when no source
        yields a usable price.

        Args:
            intent: The order intent whose symbol is priced.

        Returns:
            A positive USD price, or ``None`` (→ fail-closed DENY upstream).
        """
        broker_price = self._broker_quote_price(intent.symbol)
        if broker_price is not None:
            return broker_price
        asset_class = instrument_asset_class(intent.instrument_type)
        if asset_class is None:
            return None
        try:
            return last_price_usd(intent.symbol, asset_class)
        except Exception as exc:  # loader chain failure → fail-closed
            logger.warning("loader quote failed for %s: %s", intent.symbol, exc)
            return None

    def _broker_quote_price(self, symbol: str) -> float | None:
        """Read a USD price for ``symbol`` from the broker's quote tool.

        Calls the ungated read path (never the guard) for ``get_quotes`` with the
        symbol argument and parses a price from the common envelope shapes.
        Returns ``None`` on any error envelope, missing field, or unparseable
        value — the caller then falls back to the data loaders.

        Args:
            symbol: Normalized upper-case symbol.

        Returns:
            A positive USD price, or ``None``.
        """
        result = self._broker_quote_payload(symbol)
        return _parse_quote_price(result, symbol)

    def _broker_quote_payload(self, symbol: str) -> object:
        """Return the first successful raw quote payload with timestamp data."""
        for remote in self._read_tools("quote", _QUOTE_TOOLS):
            try:
                result = self._adapter.call_tool(
                    remote, {"symbol": symbol}, local_name=remote
                )
            except Exception as exc:
                logger.warning("broker quote tool %s failed: %s", remote, exc)
                continue
            if isinstance(result, dict) and result.get("status") == "error":
                continue
            return result
        return None

    # -- decision helpers ---------------------------------------------------

    def _allow(
        self,
        *,
        mandate: Mandate,
        intent: OrderIntent,
        kwargs: dict,
        qualification: QualificationDecision,
        channel: str,
        fingerprint: str,
    ) -> str:
        """Forward the order unchanged; consume a count + audit only on success.

        ``MCPServerAdapter.call_tool`` does NOT raise on broker/network failure —
        it returns a ``{"status": "error", ...}`` envelope. So the gate inspects
        the forwarded payload (H2):

        * **non-error** → increment the daily counter and audit
          ``kind="order_placed"`` / ``outcome="accepted"``.
        * **error envelope** → audit ``kind="order_rejected"`` /
          ``outcome="error"`` and do NOT consume a daily count (a failed forward
          never placed an order).

        Either way the returned tool_result carries the redacted audit record
        under :data:`LIVE_ACTION_RESULT_KEY` so the api_server SSE relay can emit
        a ``live.action`` event without touching the agent loop (H5).
        """
        checked = [
            "mandate", "expiry", "halt_flag", "client_order_id", "reconciliation",
            "quote_freshness", "clock_drift", "fx_normalization", "price_deviation",
            "max_daily_loss_usd", "open_order_reservations", "intent",
            "exclude_symbols", "allowed_instruments", "asset_classes",
            "max_order_notional_usd", "max_total_exposure_usd", "max_leverage",
            "max_trades_per_day", "account_funding_usd", "universe_floors",
            "live_qualification",
        ]
        pre_record = self._audit(
            kind="order_submitted",
            outcome="accepted",
            mandate=mandate,
            intent=intent,
            broker_request=dict(kwargs),
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
            refusal = self._refusal(
                decision=_DECISION_DENY,
                reason="live audit unavailable before broker write",
                reauth=False,
            )
            return self._complete_blocked(channel, fingerprint, kwargs, refusal)

        raised = False
        try:
            forwarded = super().execute(**kwargs)
        except Exception as exc:  # noqa: BLE001
            raised = True
            forwarded = json.dumps({"status": "error", "error": str(exc)})
        broker_response = self._safe_json(forwarded)
        is_error = self._is_error_envelope(broker_response)
        ledger_result = broker_response if isinstance(broker_response, dict) else {
            "status": "error", "error": "unparseable broker result"
        }
        outcome: OrderOutcome = "ambiguous" if raised else "error" if is_error else "accepted"
        ledger_error: str | None = None
        try:
            complete_order(
                self.broker,
                channel,
                intent.client_order_id or "",
                fingerprint,
                outcome=outcome,
                result=ledger_result,
            )
        except OrderLedgerError as exc:
            trip_halt("order_ledger", f"post-write ledger failure: {exc}", self.broker)
            ledger_error = str(exc)
        if is_error:
            record = self._audit(
                kind="order_rejected",
                outcome="error",
                mandate=mandate,
                intent=intent,
                broker_request=dict(kwargs),
                broker_response=broker_response,
                gate_decision={
                    "allowed": True,
                    "decision": _DECISION_ALLOW,
                    "checked_limits": checked,
                    "qualification": qualification.to_dict(),
                },
                error=self._error_message(broker_response),
            )
        else:
            # Only a confirmed ALLOW + non-error forward consumes a daily count.
            self._increment_daily_count()
            record = self._audit(
                kind="order_placed",
                outcome="accepted",
                mandate=mandate,
                intent=intent,
                broker_request=dict(kwargs),
                broker_response=broker_response,
                gate_decision={
                    "allowed": True,
                    "decision": _DECISION_ALLOW,
                    "checked_limits": checked,
                    "qualification": qualification.to_dict(),
                },
            )
        if record is None:
            trip_halt("live_audit", "post-write live audit failure", self.broker)
            payload = dict(ledger_result)
            payload.update(
                {
                    "safety_halt": True,
                    "audit_error": "post-write live audit failed",
                    LIVE_ACTION_RESULT_KEY: pre_record,
                    "client_order_id": intent.client_order_id,
                }
            )
            if ledger_error is not None:
                payload["ledger_error"] = ledger_error
            return json.dumps(payload, ensure_ascii=False)
        embedded = self._embed_live_action(forwarded, record)
        try:
            payload = json.loads(embedded)
        except (TypeError, ValueError):
            return embedded
        if isinstance(payload, dict):
            payload["client_order_id"] = intent.client_order_id
            if ledger_error is not None:
                payload["safety_halt"] = True
                payload["ledger_error"] = ledger_error
            return json.dumps(payload, ensure_ascii=False)
        return embedded

    def _deny_qualification(
        self,
        *,
        channel: str,
        fingerprint: str,
        kwargs: dict,
        mandate: Mandate,
        intent: OrderIntent,
        qualification: QualificationDecision,
    ) -> str:
        """Audit and deny immediately before the remote order write boundary."""
        snapshot = qualification.to_dict()
        record = self._audit(
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
        refusal = self._refusal(
            decision="qualification_required",
            reason=qualification.reason,
            reauth=False,
            record=record,
            qualification=snapshot,
        )
        return self._complete_blocked(channel, fingerprint, kwargs, refusal)

    def _deny(
        self,
        *,
        reason: str,
        checked: list[str],
        mandate: Mandate | None,
        reauth: bool = False,
    ) -> str:
        """Audit + return a refusal envelope for a pre-intent / structural DENY."""
        record = self._audit(
            kind="order_rejected",
            outcome="blocked",
            mandate=mandate,
            intent=None,
            broker_request=None,
            broker_response=None,
            gate_decision={"allowed": False, "decision": _DECISION_DENY, "checked_limits": checked},
            error=reason,
        )
        return self._refusal(
            decision=_DECISION_DENY, reason=reason, reauth=reauth, record=record
        )

    def _deny_breach(
        self,
        breach: BreachEvent,
        *,
        mandate: Mandate,
        intent: OrderIntent,
        reauth: bool,
    ) -> str:
        """Audit + return a refusal for a ``check_mandate`` breach.

        Structural breaches (``reauth=False``) DENY outright; quantitative
        breaches (``reauth=True``) PAUSE for re-authorization and surface the
        full :class:`BreachEvent` so the consent layer can render a widen-prompt.
        """
        decision = _DECISION_PAUSE if reauth else _DECISION_DENY
        record = self._audit(
            kind="breach",
            outcome="blocked",
            mandate=mandate,
            intent=intent,
            broker_request=None,
            broker_response=None,
            gate_decision={
                "allowed": False,
                "decision": decision,
                "limit": breach.limit,
                "kind": breach.kind,
                "limit_value": breach.limit_value,
                "attempted_value": breach.attempted_value,
            },
            error=breach.detail or f"order breaches {breach.limit}",
        )
        return self._refusal(
            decision=decision,
            reason=breach.detail or f"order breaches {breach.limit}",
            reauth=reauth,
            breach=breach,
            record=record,
        )

    def _refusal(
        self,
        *,
        decision: str,
        reason: str,
        reauth: bool,
        breach: BreachEvent | None = None,
        record: dict | None = None,
        qualification: dict[str, object] | None = None,
    ) -> str:
        """Build the structured refusal envelope returned to the agent loop."""
        payload: dict[str, Any] = {
            "status": "blocked",
            "decision": decision,
            "reason": reason,
            "broker": self.broker,
            "remote_tool": self.remote_name,
            "requires_reauthorization": reauth,
        }
        if record is not None:
            payload[LIVE_ACTION_RESULT_KEY] = record
        if qualification is not None:
            payload["qualification"] = qualification
        if breach is not None:
            payload["breach"] = {
                "broker": breach.broker,
                "limit": breach.limit,
                "limit_value": breach.limit_value,
                "attempted_value": breach.attempted_value,
                "overage": breach.overage,
                "remote_tool": breach.remote_tool,
                "created_at": breach.created_at,
                "kind": breach.kind,
                "detail": breach.detail,
                "proposed_action": {
                    "symbol": breach.proposed_action.symbol,
                    "side": breach.proposed_action.side,
                    "notional_usd": breach.proposed_action.notional_usd,
                    "quantity": breach.proposed_action.quantity,
                    "instrument_type": breach.proposed_action.instrument_type.value,
                },
            }
        return json.dumps(payload, ensure_ascii=False)

    def _complete_blocked(
        self,
        channel: str,
        fingerprint: str,
        kwargs: dict[str, Any],
        refusal: str,
    ) -> str:
        """Finalize a reserved order as blocked without a broker write."""
        response = self._safe_json(refusal) or {
            "status": "blocked", "reason": "order blocked"
        }
        try:
            complete_order(
                self.broker,
                channel,
                str(kwargs.get("client_order_id") or ""),
                fingerprint,
                outcome="blocked",
                result=response,
            )
        except OrderLedgerError as exc:
            response["ledger_error"] = str(exc)
            return json.dumps(response, ensure_ascii=False)
        return refusal

    # -- read snapshot ------------------------------------------------------

    def _read_first(self, candidates: tuple[str, ...]) -> object:
        """Read the first responsive broker read tool, fail-closed.

        Routes through the plain ``MCPServerAdapter.call_tool`` path (NOT the
        guard) so reads are never gated. Returns ``None`` on any error envelope
        or exception so the downstream check fail-closes.

        Args:
            candidates: Ordered remote read-tool names to try.

        Returns:
            The first successful tool result payload, or ``None``.
        """
        for remote in candidates:
            try:
                result = self._adapter.call_tool(remote, {}, local_name=remote)
            except Exception as exc:
                logger.warning("live read tool %s failed: %s", remote, exc)
                continue
            if isinstance(result, dict) and result.get("status") == "error":
                continue
            return result
        return None

    def _read_tools(self, operation: str, fallback: tuple[str, ...]) -> tuple[str, ...]:
        """Return connector-specific read tools, falling back to legacy names."""
        try:
            from src.trading.service import runner_tool_name

            remote = runner_tool_name(self.broker, operation)
        except Exception:  # pragma: no cover - guard must fail closed later
            remote = None
        return (remote,) if remote else fallback

    # -- daily counter ------------------------------------------------------

    def _read_daily_count(self) -> int:
        """Return today's order count via the shared per-broker counter."""
        return read_daily_count(self.broker)

    def _increment_daily_count(self) -> None:
        """Increment today's order count via the shared per-broker counter."""
        increment_daily_count(self.broker)

    # -- audit + misc -------------------------------------------------------

    def _audit(
        self,
        *,
        kind: LiveActionKind,
        outcome: LiveActionOutcome,
        mandate: Mandate | None,
        intent: OrderIntent | None,
        broker_request: dict | None,
        broker_response: dict | None,
        gate_decision: dict,
        error: str | None = None,
    ) -> dict | None:
        """Write one live-action audit event and return the redacted record.

        The returned record (identical to what was written to the ledger) is
        embedded under :data:`LIVE_ACTION_RESULT_KEY` in the tool_result so the
        SSE relay can emit a ``live.action`` event. A write failure is returned
        as ``None``: callers deny before an order write, or halt after a write.

        Returns:
            The redacted audit record, or ``None`` when the write failed.
        """
        consent = mandate.consent if mandate is not None else None
        try:
            event = LiveActionEvent(
                kind=kind,
                session_id=self.session_id,
                outcome=outcome,
                server=self.broker,
                remote_tool=self.remote_name,
                intent_normalized=_describe_intent(intent),
                mandate_snapshot_ref=consent.consent_token_sha256 if consent else None,
                consent_record_ref=consent.account_ref if consent else None,
                broker_request=broker_request,
                broker_response=broker_response,
                gate_decision=gate_decision,
                error=error,
            )
            return _record_live_action(event)
        except Exception as exc:  # caller applies the phase-specific safety rule
            logger.warning("live-action audit write failed (%s): %s", kind, exc)
            return None

    def _is_expired(self, mandate: Mandate) -> bool:
        """Return whether the mandate is past its ``expires_at`` (fail-closed).

        An unparseable ``expires_at`` is treated as expired (fail-closed): a
        live mandate with a malformed expiry must not keep trading.
        """
        raw = mandate.consent.expires_at
        try:
            expires = datetime.fromisoformat(raw)
        except (TypeError, ValueError):
            return True
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) >= expires

    @staticmethod
    def _safe_json(text: str) -> dict | None:
        """Best-effort parse of the forwarded broker result for the audit record."""
        try:
            parsed = json.loads(text)
        except (TypeError, ValueError):
            return {"raw": text}
        return parsed if isinstance(parsed, dict) else {"raw": parsed}

    @staticmethod
    def _is_error_envelope(broker_response: dict | None) -> bool:
        """Whether the forwarded result is an error envelope (H2).

        ``MCPServerAdapter.call_tool`` returns ``{"status": "error", ...}`` on
        broker/network failure without raising. Treats a missing/unparseable
        response as an error too (fail-closed: no order was confirmed placed).
        """
        if not isinstance(broker_response, dict):
            return True
        return str(broker_response.get("status", "")).lower() == "error"

    @staticmethod
    def _error_message(broker_response: dict | None) -> str:
        """Extract a human-readable error from an error envelope."""
        if isinstance(broker_response, dict):
            for key in ("error", "message", "detail"):
                value = broker_response.get(key)
                if isinstance(value, str) and value:
                    return value
        return "broker forward returned an error"

    @staticmethod
    def _embed_live_action(forwarded: str, record: dict | None) -> str:
        """Embed the redacted audit record under the frozen live-action key (H5).

        The forwarded broker result is a JSON object string; the record is added
        as a top-level ``live_action`` key so the api_server SSE relay can emit a
        ``live.action`` event without touching ``loop.py``. If the result isn't a
        JSON object or there is no record, the forwarded string is returned
        unchanged.
        """
        if record is None:
            return forwarded
        try:
            payload = json.loads(forwarded)
        except (TypeError, ValueError):
            return forwarded
        if not isinstance(payload, dict):
            return forwarded
        payload[LIVE_ACTION_RESULT_KEY] = record
        return json.dumps(payload, ensure_ascii=False)


class LiveCancelGuardTool(MCPRemoteTool):
    """Always-available risk-reducing cancel wrapper with best-effort audit.

    Cancellation remains callable when mandate, qualification, or kill switch
    state is unavailable.  Audit failures are surfaced but never prevent a
    risk-reducing broker write.
    """

    repeatable = False
    is_readonly = False

    def __init__(
        self,
        adapter: MCPServerAdapter,
        spec: MCPRemoteToolSpec,
        *,
        broker: str | None = None,
        session_id: str = "",
    ) -> None:
        super().__init__(adapter, spec)
        self.broker = (broker or spec.server_name or "").strip().lower()
        self.session_id = session_id

    def execute(self, **kwargs: Any) -> str:
        mandate = load_mandate(self.broker)
        consent = mandate.consent if mandate is not None else None

        def audit(
            kind: LiveActionKind,
            outcome: LiveActionOutcome,
            response: dict | None,
            error: str | None = None,
        ):
            try:
                return _record_live_action(
                    LiveActionEvent(
                        kind=kind,
                        session_id=self.session_id,
                        outcome=outcome,
                        server=self.broker,
                        remote_tool=self._spec.remote_name,
                        intent_normalized=f"cancel {kwargs.get('order_id') or ''}".strip(),
                        mandate_snapshot_ref=(
                            consent.consent_token_sha256 if consent else None
                        ),
                        consent_record_ref=consent.account_ref if consent else None,
                        broker_request=dict(kwargs),
                        broker_response=response,
                        gate_decision={
                            "allowed": True,
                            "decision": "risk_reducing_cancel",
                            "qualification_required": False,
                            "halt_exempt": True,
                        },
                        error=error,
                    )
                )
            except Exception as exc:  # noqa: BLE001 - never block cancellation
                logger.warning("cancel audit failed for %s: %s", self.broker, exc)
                return None

        audit("cancel_requested", "accepted", None)
        try:
            forwarded = super().execute(**kwargs)
        except Exception as exc:  # noqa: BLE001
            forwarded = json.dumps({"status": "error", "error": str(exc)})
        response = LiveOrderGuardTool._safe_json(forwarded)
        is_error = LiveOrderGuardTool._is_error_envelope(response)
        record = audit(
            "order_cancelled",
            "error" if is_error else "accepted",
            response,
            LiveOrderGuardTool._error_message(response) if is_error else None,
        )
        return LiveOrderGuardTool._embed_live_action(forwarded, record)


def _record_live_action(event: LiveActionEvent) -> dict | None:
    """Call ``write_live_action`` with the keyword contract, fall back positional.

    The frozen contract is
    ``write_live_action(event, *, event_callback=None, trace_writer=None)`` (G2
    is updating the signature). If only the positional form exists today, the
    keyword call raises ``TypeError`` and we retry positionally so this parcel
    works against either signature.
    """
    try:
        return write_live_action(event, event_callback=None, trace_writer=None)
    except TypeError:
        return write_live_action(event)


def _order_fingerprint(intent: OrderIntent, kwargs: dict[str, Any]) -> str:
    """Hash the canonical economic intent for durable idempotency."""
    payload = {
        "symbol": intent.symbol,
        "side": intent.side,
        "notional_usd": intent.notional_usd,
        "quantity": intent.quantity,
        "instrument_type": intent.instrument_type.value,
        "asset_class": intent.asset_class.value if intent.asset_class else None,
        "order_type": intent.order_type,
        "limit_price": intent.limit_price,
        "time_in_force": kwargs.get("time_in_force"),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _payload_rows(
    payload: object,
    key: str,
    *,
    fallback_key: str | None = None,
) -> list[dict[str, Any]] | None:
    """Extract normalized rows from remote read envelopes."""
    if isinstance(payload, list):
        rows: object = payload
    elif isinstance(payload, dict):
        rows = payload.get(key)
        if rows is None and fallback_key is not None:
            rows = payload.get(fallback_key)
        if rows is None and key == "positions" and payload:
            # Robinhood may return a mapping of symbol -> position object.
            candidates = list(payload.values())
            rows = candidates if all(isinstance(row, dict) for row in candidates) else None
    else:
        return None
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        return None
    return rows


def _parse_quote_price(result: object, symbol: str) -> float | None:
    """Extract a positive USD price from a broker quote-tool payload, fail-closed.

    Accepts the common envelope shapes a ``get_quotes`` read tool may return:

    * a flat dict with a price field (``price`` / ``last_price`` / ``last`` /
      ``mark_price`` / ``ask`` / ``bid``);
    * a dict keyed by symbol → quote dict;
    * a dict with a ``quotes`` / ``data`` list of quote dicts (matched on
      ``symbol`` / ``ticker`` when present, else the sole entry).

    Unknown extra keys are ignored, never guessed. Returns ``None`` on anything
    unparseable so the caller falls back to the data loaders.

    Args:
        result: The broker quote tool's normalized payload.
        symbol: The normalized upper-case symbol requested.

    Returns:
        A positive USD price, or ``None``.
    """
    if not isinstance(result, dict):
        return None

    direct = _price_from_quote_dict(result)
    if direct is not None:
        return direct

    keyed = result.get(symbol)
    if isinstance(keyed, dict):
        price = _price_from_quote_dict(keyed)
        if price is not None:
            return price

    for container_key in ("quotes", "data", "results"):
        rows = result.get(container_key)
        if not isinstance(rows, list):
            continue
        match = _match_quote_row(rows, symbol)
        if match is not None:
            price = _price_from_quote_dict(match)
            if price is not None:
                return price
    return None


def _match_quote_row(rows: list, symbol: str) -> dict | None:
    """Pick the quote row for ``symbol`` from a list, or the sole entry."""
    dict_rows = [row for row in rows if isinstance(row, dict)]
    for row in dict_rows:
        for key in ("symbol", "ticker", "instrument"):
            value = row.get(key)
            if isinstance(value, str) and value.strip().upper() == symbol:
                return row
    return dict_rows[0] if len(dict_rows) == 1 else None


def _price_from_quote_dict(quote: dict) -> float | None:
    """Return the first parseable positive price from a quote dict, else None."""
    for key in ("price", "last_price", "last", "mark_price", "close", "ask", "bid"):
        if key in quote:
            try:
                value = float(quote[key])
            except (TypeError, ValueError):
                continue
            if value == value and value > 0:  # finite + positive
                return value
    return None


def _describe_intent(intent: OrderIntent | None) -> str | None:
    """Render a human-readable normalized intent for the audit record."""
    if intent is None:
        return None
    size = (
        f"${intent.notional_usd:g}"
        if intent.notional_usd is not None
        else f"{intent.quantity:g} units"
        if intent.quantity is not None
        else "?"
    )
    return f"{intent.side} {size} {intent.symbol} ({intent.instrument_type.value})"
