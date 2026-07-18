"""Node 12B durable client-order idempotency ledger."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.live import paths as live_paths
from src.live.order_ledger import (
    OrderLedgerError,
    accepted_client_order_ids,
    claim_order,
    complete_order,
    order_ledger_path,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def ledger_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(live_paths, "get_runtime_root", lambda: tmp_path)
    return tmp_path


def test_claim_complete_and_replay_survive_restart(ledger_root: Path) -> None:
    first = claim_order("alpaca", "paper:alpaca-paper-trade", "vt_order_0001", "fp-a")
    assert first.action == "claimed"

    complete_order(
        "alpaca",
        "paper:alpaca-paper-trade",
        "vt_order_0001",
        "fp-a",
        outcome="accepted",
        result={"status": "ok", "order_id": "broker-1", "api_secret": "must-hide"},
    )
    replay = claim_order("alpaca", "paper:alpaca-paper-trade", "vt_order_0001", "fp-a")

    assert replay.action == "replay"
    assert replay.outcome == "accepted"
    assert replay.result == {
        "status": "ok",
        "order_id": "broker-1",
        "api_secret": "[redacted]",
    }
    assert accepted_client_order_ids(
        "alpaca", "paper:alpaca-paper-trade"
    ) == frozenset({"vt_order_0001"})


def test_pending_duplicate_and_fingerprint_conflict_are_blocked(ledger_root: Path) -> None:
    claim_order("alpaca", "live:acct-1", "vt_order_0002", "fp-a")

    pending = claim_order("alpaca", "live:acct-1", "vt_order_0002", "fp-a")
    conflict = claim_order("alpaca", "live:acct-1", "vt_order_0002", "fp-b")

    assert pending.action == "pending"
    assert conflict.action == "conflict"


def test_invalid_client_order_id_is_rejected_before_a_write(ledger_root: Path) -> None:
    with pytest.raises(OrderLedgerError, match="client_order_id"):
        claim_order("alpaca", "live:acct-1", "../escape", "fp-a")
    assert not order_ledger_path("alpaca").exists()


def test_tampered_hash_chain_fails_closed(ledger_root: Path) -> None:
    claim_order("alpaca", "live:acct-1", "vt_order_0003", "fp-a")
    path = order_ledger_path("alpaca")
    row = json.loads(path.read_text(encoding="utf-8"))
    row["fingerprint"] = "tampered"
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")

    with pytest.raises(OrderLedgerError, match="integrity"):
        claim_order("alpaca", "live:acct-1", "vt_order_0004", "fp-b")


def test_ledger_is_private_and_append_only_jsonl(ledger_root: Path) -> None:
    claim_order("alpaca", "paper:profile", "vt_order_0005", "fp-a")
    complete_order(
        "alpaca",
        "paper:profile",
        "vt_order_0005",
        "fp-a",
        outcome="blocked",
        result={"status": "blocked", "reason": "stale quote"},
    )

    path = order_ledger_path("alpaca")
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [row["event"] for row in rows] == ["reserved", "blocked"]
    assert path.stat().st_mode & 0o077 == 0
