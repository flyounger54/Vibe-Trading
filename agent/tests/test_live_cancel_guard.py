"""Node 12B risk-reducing remote cancellation boundary."""

from __future__ import annotations

import json

import pytest

from src.live import order_guard, paths
from src.live.halt import trip_halt
from src.live.order_guard import LiveCancelGuardTool
from src.tools.mcp import MCPRemoteToolSpec

pytestmark = pytest.mark.unit


class _Adapter:
    server_name = "robinhood"

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def call_tool(self, remote_name, arguments, *, local_name=None):
        self.calls.append((remote_name, arguments))
        return {"status": "ok", "order_id": arguments.get("order_id")}


def _guard(adapter: _Adapter) -> LiveCancelGuardTool:
    return LiveCancelGuardTool(
        adapter,
        MCPRemoteToolSpec(
            server_name="robinhood",
            remote_name="cancel_order",
            local_name="mcp_robinhood_cancel_order",
            description="Cancel an order",
            parameters={
                "type": "object",
                "properties": {"order_id": {"type": "string"}},
                "required": ["order_id"],
            },
        ),
        broker="robinhood",
        session_id="session-1",
    )


def test_cancel_remains_available_without_mandate_and_while_halted(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(paths, "get_runtime_root", lambda: tmp_path)
    monkeypatch.setattr(
        order_guard, "_record_live_action", lambda event: {"kind": event.kind}
    )
    trip_halt("test", "risk reduction must remain available", "robinhood")
    adapter = _Adapter()

    out = json.loads(_guard(adapter).execute(order_id="order-1"))

    assert out["status"] == "ok"
    assert adapter.calls == [("cancel_order", {"order_id": "order-1"})]
    assert out["live_action"]["kind"] == "order_cancelled"


def test_cancel_audit_failure_never_blocks_risk_reduction(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(paths, "get_runtime_root", lambda: tmp_path)

    def fail_audit(event):
        raise OSError("ledger unavailable")

    monkeypatch.setattr(order_guard, "_record_live_action", fail_audit)
    adapter = _Adapter()

    out = json.loads(_guard(adapter).execute(order_id="order-2"))

    assert out["status"] == "ok"
    assert adapter.calls == [("cancel_order", {"order_id": "order-2"})]
