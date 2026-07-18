"""Tamper-evident order ledger and durable client-order idempotency.

Every paper or live order reserves its caller-supplied ``client_order_id`` in
this append-only hash chain before a broker write.  A repeated identical order
replays its terminal result; a pending or conflicting duplicate is denied and
is never re-sent.  The whole ledger fails closed on malformed or altered data.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Literal

from src.live.paths import broker_dir
from src.tools.redaction import redact_payload

_SCHEMA_VERSION = 1
_MAX_LEDGER_BYTES = 8 * 1024 * 1024
_CLIENT_ORDER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,63}$")
_TERMINAL_EVENTS = frozenset({"accepted", "error", "blocked", "ambiguous"})
_COMPLETION_EVENTS = _TERMINAL_EVENTS

OrderOutcome = Literal["accepted", "error", "blocked", "ambiguous"]


class OrderLedgerError(RuntimeError):
    """The order ledger cannot safely accept or resolve an idempotency key."""


@dataclass(frozen=True)
class OrderClaim:
    """Result of reserving a client-order id."""

    action: Literal["claimed", "replay", "pending", "conflict"]
    outcome: str | None = None
    result: dict[str, Any] | None = None


def order_ledger_path(broker: str) -> Path:
    """Return the fixed per-broker append-only order ledger path."""
    return broker_dir(broker) / "order-ledger.jsonl"


def _lock_path(broker: str) -> Path:
    return broker_dir(broker) / ".order-ledger.lock"


def _validate_client_order_id(client_order_id: str) -> str:
    value = str(client_order_id or "").strip()
    if not _CLIENT_ORDER_ID_RE.fullmatch(value):
        raise OrderLedgerError(
            "client_order_id must be 8-64 safe ASCII characters beginning with an alphanumeric"
        )
    return value


def _validate_channel(channel: str) -> str:
    value = str(channel or "").strip()
    if not value or len(value) > 160 or any(ch in value for ch in ("\n", "\r", "\x00")):
        raise OrderLedgerError("invalid order-ledger channel")
    return value


def _canonical(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _record_hash(record: dict[str, Any]) -> str:
    unsigned = {key: value for key, value in record.items() if key != "record_hash"}
    return hashlib.sha256(_canonical(unsigned)).hexdigest()


def _assert_private_regular(path: Path) -> None:
    if path.is_symlink():
        raise OrderLedgerError("order ledger must not be a symlink")
    try:
        stat = path.stat()
    except OSError as exc:
        raise OrderLedgerError(f"order ledger cannot be inspected: {exc}") from exc
    if hasattr(os, "getuid") and stat.st_uid != os.getuid():
        raise OrderLedgerError("order ledger is not owned by the current user")
    if stat.st_mode & 0o077:
        raise OrderLedgerError("order ledger permissions are not private")
    if stat.st_size > _MAX_LEDGER_BYTES:
        raise OrderLedgerError("order ledger exceeds the safety size limit")


@contextmanager
def _locked(broker: str) -> Iterator[None]:
    path = _lock_path(broker)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            yield
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        raise


def _read_records(broker: str) -> list[dict[str, Any]]:
    path = order_ledger_path(broker)
    if path.is_symlink():
        raise OrderLedgerError("order ledger must not be a symlink")
    if not path.exists():
        return []
    _assert_private_regular(path)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise OrderLedgerError(f"order ledger is unreadable: {exc}") from exc

    records: list[dict[str, Any]] = []
    previous = "0" * 64
    for index, line in enumerate(lines, start=1):
        try:
            record = json.loads(line)
        except (TypeError, ValueError) as exc:
            raise OrderLedgerError(f"order ledger integrity failure at line {index}") from exc
        if not isinstance(record, dict):
            raise OrderLedgerError(f"order ledger integrity failure at line {index}")
        if record.get("schema_version") != _SCHEMA_VERSION or record.get("sequence") != index:
            raise OrderLedgerError(f"order ledger integrity failure at line {index}")
        if record.get("previous_hash") != previous or record.get("record_hash") != _record_hash(record):
            raise OrderLedgerError(f"order ledger integrity failure at line {index}")
        previous = str(record["record_hash"])
        records.append(record)
    return records


def _append_record(broker: str, records: list[dict[str, Any]], payload: dict[str, Any]) -> dict[str, Any]:
    path = order_ledger_path(broker)
    previous = str(records[-1]["record_hash"]) if records else "0" * 64
    record = {
        "schema_version": _SCHEMA_VERSION,
        "sequence": len(records) + 1,
        "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "previous_hash": previous,
        **payload,
    }
    record["record_hash"] = _record_hash(record)
    line = json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n"
    encoded = line.encode("utf-8")
    current_size = path.stat().st_size if path.exists() else 0
    if current_size + len(encoded) > _MAX_LEDGER_BYTES:
        raise OrderLedgerError("order ledger exceeds the safety size limit")
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, encoded)
        os.fsync(fd)
    finally:
        os.close(fd)
    records.append(record)
    return record


def _latest(records: list[dict[str, Any]], channel: str, client_order_id: str) -> dict[str, Any] | None:
    return next(
        (
            record
            for record in reversed(records)
            if record.get("channel") == channel and record.get("client_order_id") == client_order_id
        ),
        None,
    )


def claim_order(
    broker: str,
    channel: str,
    client_order_id: str,
    fingerprint: str,
) -> OrderClaim:
    """Atomically reserve an idempotency key or resolve its prior outcome."""
    key = _validate_client_order_id(client_order_id)
    channel_key = _validate_channel(channel)
    fingerprint_value = str(fingerprint or "").strip()
    if not fingerprint_value:
        raise OrderLedgerError("order fingerprint is required")

    with _locked(broker):
        records = _read_records(broker)
        prior = _latest(records, channel_key, key)
        if prior is not None:
            if prior.get("fingerprint") != fingerprint_value:
                return OrderClaim(action="conflict")
            event = str(prior.get("event") or "")
            if event in _TERMINAL_EVENTS:
                result = prior.get("result")
                return OrderClaim(
                    action="replay",
                    outcome=event,
                    result=dict(result) if isinstance(result, dict) else None,
                )
            return OrderClaim(action="pending")

        _append_record(
            broker,
            records,
            {
                "event": "reserved",
                "channel": channel_key,
                "client_order_id": key,
                "fingerprint": fingerprint_value,
                "result": None,
            },
        )
        return OrderClaim(action="claimed")


def accepted_client_order_ids(broker: str, channel: str) -> frozenset[str]:
    """Return terminally accepted idempotency keys for reconciliation.

    Reading uses the same lock and full hash-chain validation as claiming an
    order.  A corrupt ledger therefore cannot be used to bless an otherwise
    unrecognized broker-side open order.
    """
    channel_key = _validate_channel(channel)
    with _locked(broker):
        records = _read_records(broker)
        return frozenset(
            str(record["client_order_id"])
            for record in records
            if record.get("channel") == channel_key
            and record.get("event") == "accepted"
            and record.get("client_order_id")
        )


def complete_order(
    broker: str,
    channel: str,
    client_order_id: str,
    fingerprint: str,
    *,
    outcome: OrderOutcome,
    result: dict[str, Any],
) -> None:
    """Append the terminal result for a previously reserved order."""
    if outcome not in _COMPLETION_EVENTS:
        raise OrderLedgerError(f"invalid order outcome: {outcome}")
    key = _validate_client_order_id(client_order_id)
    channel_key = _validate_channel(channel)
    with _locked(broker):
        records = _read_records(broker)
        prior = _latest(records, channel_key, key)
        if prior is None or prior.get("fingerprint") != fingerprint:
            raise OrderLedgerError("order completion does not match a reservation")
        if prior.get("event") != "reserved":
            raise OrderLedgerError("order reservation is already terminal")
        _append_record(
            broker,
            records,
            {
                "event": outcome,
                "channel": channel_key,
                "client_order_id": key,
                "fingerprint": fingerprint,
                "result": redact_payload(dict(result)),
            },
        )
