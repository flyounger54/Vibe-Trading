"""Node 12C signed paper-soak qualification evidence.

The live qualification registry is intentionally operator-owned.  This module
adds the operator-side path that can move one exact
``(broker, account_ref, build_revision, policy_version)`` key from
``disabled`` to ``paper_soak`` and, after a verified campaign, to
``pilot_eligible``.  It never activates live trading.

Each campaign is bound to a signed market-session manifest.  One evidence
record is appended for each scheduled session, in order, after the close.  The
records form an Ed25519 signature chain; the exportable evidence contains only
the public key, while the private key lives in a separate protected keystore.  Raw
broker payloads are validated in memory and represented only by SHA-256
digests in the evidence ledger.  Rejected days remain in the chain and reset
the consecutive accepted-day streak.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import re
import secrets
import stat
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence, TypedDict

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from src.live.execution_risk import (
    normalize_account_equity_usd,
    normalize_quote,
    open_order_reservations_usd,
)
from src.live.paths import live_root
from src.live.qualification import (
    MIN_PAPER_SOAK_TRADING_DAYS,
    QUALIFICATION_POLICY_VERSION,
    QUALIFICATION_SCHEMA_VERSION,
    QualificationState,
    transition_allowed,
)
from src.trading.profiles import profile_by_id

SOAK_EVIDENCE_SCHEMA_VERSION = 1
SOAK_EVIDENCE_POLICY_VERSION = "node12c-signed-paper-soak-v1"

REQUIRED_DRILLS: tuple[str, ...] = (
    "restart_recovery",
    "disconnect_recovery",
    "duplicate_order_replay",
    "ledger_tamper_fail_closed",
    "unauthorized_order_halt",
    "kill_switch_block",
    "cancel_while_halted",
)

_EVIDENCE_DIRNAME = "qualification-evidence"
_SIGNING_KEY_DIRNAME = "qualification-signing-keys"
_REGISTRY_FILENAME = "qualification-registry.json"
_MANIFEST_FILENAME = "manifest.json"
_LEDGER_FILENAME = "daily-evidence.jsonl"
_LOCK_FILENAME = ".campaign.lock"
_REGISTRY_LOCK_FILENAME = ".qualification-registry.lock"

_MAX_MANIFEST_BYTES = 2 * 1024 * 1024
_MAX_LEDGER_BYTES = 16 * 1024 * 1024
_MAX_REGISTRY_BYTES = 2 * 1024 * 1024
_MAX_HEARTBEAT_GAP_SECONDS = 300.0
_MAX_QUOTE_AGE_SECONDS = 120.0
_MAX_CLOCK_DRIFT_SECONDS = 5.0
_MAX_RECORD_DELAY_SECONDS = 12 * 60 * 60

_BUILD_RE = re.compile(r"^[0-9a-f]{40}$")
_CAMPAIGN_RE = re.compile(r"^[0-9a-f]{24}$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_SIGNATURE_RE = re.compile(r"^[0-9a-f]{128}$")
_ACTOR_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}$")
_EVIDENCE_REF_RE = re.compile(
    r"^node12-paper-soak-v1://([0-9a-f]{24})/([0-9a-f]{128})$"
)


class PaperSoakError(RuntimeError):
    """The paper-soak campaign cannot be trusted or advanced."""


class _ProbeOrderArgs(TypedDict):
    side: str
    quantity: float | None
    notional: float | None
    order_type: str
    limit_price: float | None
    time_in_force: str
    client_order_id: str


@dataclass(frozen=True)
class PaperSoakStatus:
    """Credential-free progress for one signed campaign."""

    campaign_id: str
    state: QualificationState
    broker: str
    profile_id: str
    build_revision: str
    policy_version: str
    accepted_days: int
    observed_trading_days: tuple[str, ...]
    next_trading_day: str | None
    completed_drills: tuple[str, ...]
    missing_drills: tuple[str, ...]
    eligible: bool
    last_day_accepted: bool | None = None
    failure_codes: tuple[str, ...] = ()
    evidence_ref: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "campaign_id": self.campaign_id,
            "state": self.state.value,
            "broker": self.broker,
            "profile_id": self.profile_id,
            "build_revision": self.build_revision,
            "policy_version": self.policy_version,
            "accepted_days": self.accepted_days,
            "required_trading_days": MIN_PAPER_SOAK_TRADING_DAYS,
            "observed_trading_days": list(self.observed_trading_days),
            "next_trading_day": self.next_trading_day,
            "completed_drills": list(self.completed_drills),
            "missing_drills": list(self.missing_drills),
            "eligible": self.eligible,
            "last_day_accepted": self.last_day_accepted,
            "failure_codes": list(self.failure_codes),
            "evidence_ref": self.evidence_ref,
        }


@dataclass(frozen=True)
class VerifiedPaperSoak:
    """Evidence result consumed by the qualification-registry parser."""

    observed_trading_days: tuple[date, ...]
    completed_drills: tuple[str, ...]
    eligible: bool


def evidence_ledger_path(campaign_id: str) -> Path:
    """Return the fixed append-only evidence path for a campaign."""
    return _campaign_dir(campaign_id) / _LEDGER_FILENAME


def start_paper_soak(
    profile_id: str,
    account_ref: str,
    build_revision: str,
    calendar: Mapping[str, object],
    *,
    actor: str,
    now: datetime | None = None,
) -> PaperSoakStatus:
    """Create a signed campaign and enter ``paper_soak`` for its exact key."""
    current = _utc_now(now)
    profile = profile_by_id(str(profile_id or "").strip().lower())
    if profile.environment != "paper":
        raise PaperSoakError("qualification requires a paper connector profile")
    if profile.transport != "broker_sdk":
        raise PaperSoakError("paper qualification requires a broker_sdk profile")
    if profile.readonly or "orders.place" not in profile.capabilities:
        raise PaperSoakError("paper qualification profile must expose orders.place")

    account = _bounded_text(account_ref, "account_ref", 128)
    build = str(build_revision or "").strip().lower()
    if not _BUILD_RE.fullmatch(build):
        raise PaperSoakError("build_revision must be 40 lowercase hex characters")
    actor_value = _actor(actor)
    calendar_id, sessions = _parse_calendar(calendar)
    first_open = _parse_datetime(sessions[0]["opens_at"], "opens_at")
    if current > first_open:
        raise PaperSoakError("paper soak cannot start after its first scheduled session")

    identity = {
        "broker": profile.connector,
        "account_ref_sha256": _sha256_text(account),
        "build_revision": build,
        "policy_version": QUALIFICATION_POLICY_VERSION,
        "profile_id": profile.id,
        "started_at": current.isoformat(),
    }
    campaign_id = hashlib.sha256(_canonical(identity)).hexdigest()[:24]
    campaign_dir = _campaign_dir(campaign_id)
    root = campaign_dir.parent
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    _tighten_directory(root)
    try:
        campaign_dir.mkdir(mode=0o700)
    except FileExistsError as exc:
        raise PaperSoakError("an identical paper-soak campaign already exists") from exc

    private_key = Ed25519PrivateKey.generate()
    private_key_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_key_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    manifest: dict[str, Any] = {
        "schema_version": SOAK_EVIDENCE_SCHEMA_VERSION,
        "evidence_policy_version": SOAK_EVIDENCE_POLICY_VERSION,
        "campaign_id": campaign_id,
        "broker": profile.connector,
        "profile_id": profile.id,
        "account_ref_sha256": _sha256_text(account),
        "build_revision": build,
        "qualification_policy_version": QUALIFICATION_POLICY_VERSION,
        "calendar_id": calendar_id,
        "calendar_sha256": hashlib.sha256(_canonical(sessions)).hexdigest(),
        "required_trading_days": MIN_PAPER_SOAK_TRADING_DAYS,
        "required_drills": list(REQUIRED_DRILLS),
        "public_key_ed25519": public_key_bytes.hex(),
        "started_at": current.isoformat(),
        "started_by": actor_value,
        "sessions": sessions,
    }
    manifest["manifest_signature"] = _signature(private_key, manifest)
    key_root = live_root() / _SIGNING_KEY_DIRNAME
    key_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    _tighten_directory(key_root)
    _write_new_bytes(_private_key_path(campaign_id), private_key_bytes)
    _atomic_write_json(campaign_dir / _MANIFEST_FILENAME, manifest, replace=False)

    try:
        _enter_registry_paper_soak(
            broker=profile.connector,
            account_ref=account,
            build_revision=build,
            actor=actor_value,
            current=current,
            last_session_close=_parse_datetime(sessions[-1]["closes_at"], "closes_at"),
        )
    except Exception:
        # The private campaign remains as an inert forensic artifact.  It has no
        # registry reference and therefore cannot grant or advance qualification.
        raise
    return load_paper_soak_status(campaign_id)


def record_paper_soak_day(
    campaign_id: str,
    observation: Mapping[str, object],
    *,
    now: datetime | None = None,
) -> PaperSoakStatus:
    """Validate and append the next scheduled session's evidence.

    A record is appended even when validation fails.  That makes a bad day
    visible and resets the accepted streak instead of silently dropping it.
    """
    current = _utc_now(now)
    with _campaign_lock(campaign_id):
        manifest, private_key, sessions, records = _load_campaign(
            campaign_id, require_private=True
        )
        if private_key is None:  # pragma: no cover - enforced by require_private
            raise PaperSoakError("paper-soak private signing key is unavailable")
        if len(records) >= len(sessions):
            raise PaperSoakError("the signed calendar has no remaining session")
        session = sessions[len(records)]
        closes_at = _parse_datetime(session["closes_at"], "closes_at")
        if current < closes_at:
            raise PaperSoakError("paper-soak evidence cannot be recorded before session close")
        if (current - closes_at).total_seconds() > _MAX_RECORD_DELAY_SECONDS:
            raise PaperSoakError("paper-soak evidence is too late; backfilling is forbidden")

        failures, artifacts, passed_drills = _validate_observation(
            manifest,
            session,
            observation,
        )
        previous = str(records[-1]["signature"]) if records else str(
            manifest["manifest_signature"]
        )
        record: dict[str, Any] = {
            "schema_version": SOAK_EVIDENCE_SCHEMA_VERSION,
            "sequence": len(records) + 1,
            "campaign_id": campaign_id,
            "trading_day": session["trading_day"],
            "opens_at": session["opens_at"],
            "closes_at": session["closes_at"],
            "recorded_at": current.isoformat(),
            "previous_signature": previous,
            "accepted": not failures,
            "failure_codes": failures,
            "passed_drills": passed_drills,
            "artifacts": artifacts,
        }
        record["signature"] = _signature(private_key, record)
        _append_record(campaign_id, record)
        records.append(record)
        _sync_registry_progress(manifest, records)
    return load_paper_soak_status(campaign_id)


def collect_paper_soak_day(
    campaign_id: str,
    session_proof: Mapping[str, object],
    *,
    symbol: str,
    probe_order: Mapping[str, object],
    now: datetime | None = None,
) -> PaperSoakStatus:
    """Collect broker snapshots, run an explicit paper idempotency probe, and record.

    ``session_proof`` contributes only runner-owned coverage fields (heartbeats,
    opening-equity baseline, and drill artifact digests).  Account, open-order,
    quote, connection, and order-result payloads are always fetched through the
    configured connector service in this process, so a supplied JSON document
    cannot substitute those broker observations.
    """
    from src.trading import service

    manifest, _, _, _ = _load_campaign(campaign_id)
    profile_id = str(manifest["profile_id"])
    profile = profile_by_id(profile_id)
    if (
        profile.id != profile_id
        or profile.connector != manifest["broker"]
        or profile.environment != "paper"
        or profile.transport != "broker_sdk"
        or profile.readonly
        or "orders.place" not in profile.capabilities
    ):
        raise PaperSoakError("campaign paper connector profile no longer matches")
    clean_symbol = _bounded_text(symbol, "symbol", 128).upper()
    order_args = _parse_probe_order(probe_order)

    connection = _safe_connector_call(
        lambda: service.check_connection(profile_id), "connection"
    )
    account = _safe_connector_call(lambda: service.get_account(profile_id), "account")
    open_orders = _safe_connector_call(
        lambda: service.get_open_orders(profile_id, include_executions=True),
        "open_orders",
    )
    quote = _safe_connector_call(
        lambda: service.get_quote(clean_symbol, profile_id), "quote"
    )

    initial = _safe_connector_call(
        lambda: service.place_order(clean_symbol, profile_id, **order_args),
        "order_probe_initial",
    )
    if str(initial.get("status", "")).lower() == "ok":
        replay = _safe_connector_call(
            lambda: service.place_order(clean_symbol, profile_id, **order_args),
            "order_probe_replay",
        )
    else:
        replay = {
            "status": "error",
            "error": "initial paper probe was not accepted; replay was not attempted",
        }

    proof = _mapping(session_proof)
    observation: dict[str, object] = {
        "profile_id": profile_id,
        "symbol": clean_symbol,
        "heartbeat_times": proof.get("heartbeat_times"),
        "connection_history": proof.get("connection_history"),
        "baseline_observed_at": proof.get("baseline_observed_at"),
        "opening_equity_usd": proof.get("opening_equity_usd"),
        "connection": connection,
        "account": account,
        "open_orders": open_orders,
        "quote": quote,
        "order_probe": {
            "client_order_id": order_args["client_order_id"],
            "initial": initial,
            "replay": replay,
        },
        "drills": proof.get("drills", {}),
    }
    return record_paper_soak_day(campaign_id, observation, now=now)


def run_paper_soak_session(
    campaign_id: str,
    *,
    symbol: str,
    probe_order: Mapping[str, object],
    drills: Mapping[str, object] | None = None,
    poll_seconds: float = 60.0,
    now_fn: Callable[[], datetime] | None = None,
    sleep_fn: Callable[[float], None] | None = None,
) -> PaperSoakStatus:
    """Run one full scheduled paper session and append its evidence after close.

    The process must start no more than one hour before the signed session open.
    It polls connector readiness at a bounded cadence, establishes the opening
    USD-equity baseline before the open, and delegates the close snapshots plus
    paper idempotency probe to :func:`collect_paper_soak_day`.
    """
    from src.trading import service

    interval = float(poll_seconds)
    if not math.isfinite(interval) or interval < 1 or interval > _MAX_HEARTBEAT_GAP_SECONDS:
        raise PaperSoakError("poll_seconds must be between 1 and 300")
    clock = now_fn or (lambda: datetime.now(timezone.utc))
    sleeper = sleep_fn or time.sleep
    manifest, _, sessions, records = _load_campaign(campaign_id)
    if len(records) >= len(sessions):
        raise PaperSoakError("the signed calendar has no remaining session")
    session = sessions[len(records)]
    opens_at = _parse_datetime(session["opens_at"], "opens_at")
    closes_at = _parse_datetime(session["closes_at"], "closes_at")
    current = _utc_now(clock())
    if current > opens_at:
        raise PaperSoakError("paper session runner must start before the scheduled open")
    if (opens_at - current).total_seconds() > 3600:
        raise PaperSoakError("paper session runner may start at most one hour before open")

    profile_id = str(manifest["profile_id"])
    baseline_payload = _safe_connector_call(
        lambda: service.get_account(profile_id), "opening_account"
    )
    opening_equity = normalize_account_equity_usd(baseline_payload)
    heartbeat_times: list[str] = []
    connection_history: list[dict[str, str]] = []

    while current < closes_at:
        connection = _safe_connector_call(
            lambda: service.check_connection(profile_id), "connection_heartbeat"
        )
        heartbeat_times.append(current.isoformat())
        connection_history.append(
            {"at": current.isoformat(), "status": str(connection.get("status", "error"))}
        )
        remaining = (closes_at - current).total_seconds()
        sleeper(min(interval, remaining))
        next_current = _utc_now(clock())
        if next_current <= current:
            raise PaperSoakError("paper session runner clock did not advance")
        current = next_current

    final_connection = _safe_connector_call(
        lambda: service.check_connection(profile_id), "connection_heartbeat"
    )
    if not heartbeat_times or heartbeat_times[-1] != current.isoformat():
        heartbeat_times.append(current.isoformat())
        connection_history.append(
            {"at": current.isoformat(), "status": str(final_connection.get("status", "error"))}
        )

    drill_payload = _mapping(drills)
    statuses = [str(item["status"]).lower() for item in connection_history]
    if any(status != "ok" for status in statuses[:-1]) and statuses[-1] == "ok":
        drill_payload["disconnect_recovery"] = {
            "passed": True,
            "artifact_sha256": _payload_digest(connection_history),
        }
    session_proof: dict[str, object] = {
        "heartbeat_times": heartbeat_times,
        "connection_history": connection_history,
        "baseline_observed_at": heartbeat_times[0],
        "opening_equity_usd": opening_equity,
        "drills": drill_payload,
    }
    return collect_paper_soak_day(
        campaign_id,
        session_proof,
        symbol=symbol,
        probe_order=probe_order,
        now=current,
    )


def load_paper_soak_status(campaign_id: str) -> PaperSoakStatus:
    """Verify the complete chain and return trusted campaign progress."""
    manifest, _, sessions, records = _load_campaign(campaign_id)
    observed = _accepted_streak(records)
    completed = _completed_drills(records)
    missing = tuple(name for name in REQUIRED_DRILLS if name not in completed)
    eligible = len(observed) >= MIN_PAPER_SOAK_TRADING_DAYS and not missing
    state = _registry_state(manifest)
    next_day = (
        str(sessions[len(records)]["trading_day"])
        if len(records) < len(sessions)
        else None
    )
    last = records[-1] if records else None
    evidence_ref = _evidence_ref(campaign_id, records[-1]["signature"]) if records else None
    return PaperSoakStatus(
        campaign_id=campaign_id,
        state=state,
        broker=str(manifest["broker"]),
        profile_id=str(manifest["profile_id"]),
        build_revision=str(manifest["build_revision"]),
        policy_version=str(manifest["qualification_policy_version"]),
        accepted_days=len(observed),
        observed_trading_days=tuple(day.isoformat() for day in observed),
        next_trading_day=next_day,
        completed_drills=completed,
        missing_drills=missing,
        eligible=eligible,
        last_day_accepted=bool(last["accepted"]) if last is not None else None,
        failure_codes=tuple(str(code) for code in (last or {}).get("failure_codes", [])),
        evidence_ref=evidence_ref,
    )


def _parse_probe_order(value: Mapping[str, object]) -> _ProbeOrderArgs:
    if not isinstance(value, Mapping):
        raise PaperSoakError("paper probe order must be an object")
    allowed = {
        "side",
        "quantity",
        "notional",
        "order_type",
        "limit_price",
        "time_in_force",
        "client_order_id",
    }
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise PaperSoakError("paper probe order has unknown fields: " + ", ".join(unknown))
    side = str(value.get("side", "")).strip().lower()
    if side not in {"buy", "sell"}:
        raise PaperSoakError("paper probe side must be buy or sell")
    quantity = _positive_float(value.get("quantity"))
    notional = _positive_float(value.get("notional"))
    if (quantity is None) == (notional is None):
        raise PaperSoakError("paper probe requires exactly one positive quantity or notional")
    order_type = str(value.get("order_type", "market")).strip().lower()
    if order_type not in {"market", "limit"}:
        raise PaperSoakError("paper probe order_type must be market or limit")
    limit_price = _positive_float(value.get("limit_price"))
    if order_type == "limit" and limit_price is None:
        raise PaperSoakError("paper probe limit order requires a positive limit_price")
    client_order_id = _bounded_text(
        value.get("client_order_id"), "client_order_id", 64
    )
    if len(client_order_id) < 8:
        raise PaperSoakError("client_order_id must contain at least 8 characters")
    return {
        "side": side,
        "quantity": quantity,
        "notional": notional,
        "order_type": order_type,
        "limit_price": limit_price,
        "time_in_force": str(value.get("time_in_force", "day")).strip().lower(),
        "client_order_id": client_order_id,
    }


def _safe_connector_call(call, label: str) -> dict[str, Any]:  # noqa: ANN001
    try:
        payload = call()
    except Exception as exc:  # noqa: BLE001 - failures become rejected signed evidence
        return {"status": "error", "error": f"{label} failed: {exc}"}
    if not isinstance(payload, dict):
        return {"status": "error", "error": f"{label} returned a non-object payload"}
    return dict(payload)


def promote_paper_soak(
    campaign_id: str,
    *,
    actor: str,
    now: datetime | None = None,
) -> PaperSoakStatus:
    """Promote a fully verified campaign to ``pilot_eligible`` only."""
    current = _utc_now(now)
    actor_value = _actor(actor)
    with _campaign_lock(campaign_id):
        manifest, _, _, records = _load_campaign(campaign_id)
        observed = _accepted_streak(records)
        if len(observed) < MIN_PAPER_SOAK_TRADING_DAYS:
            raise PaperSoakError("promotion requires 30 consecutive accepted trading days")
        completed = _completed_drills(records)
        missing = [name for name in REQUIRED_DRILLS if name not in completed]
        if missing:
            raise PaperSoakError(
                "promotion requires every fault drill: " + ", ".join(missing)
            )
        last_record = records[-1]
        final_close = _parse_datetime(last_record["closes_at"], "closes_at")
        if current <= final_close:
            raise PaperSoakError("promotion must occur after the final accepted session")
        ref = _evidence_ref(campaign_id, last_record["signature"])
        verified = verify_paper_soak_evidence_ref(
            ref,
            broker=str(manifest["broker"]),
            account_ref_sha256=str(manifest["account_ref_sha256"]),
            build_revision=str(manifest["build_revision"]),
            policy_version=str(manifest["qualification_policy_version"]),
        )
        if not verified.eligible:
            raise PaperSoakError("signed evidence is not pilot eligible")
        _promote_registry(manifest, observed, ref, actor_value, current)
    return load_paper_soak_status(campaign_id)


def verify_paper_soak_evidence_ref(
    evidence_ref: str,
    *,
    broker: str,
    account_ref_sha256: str,
    build_revision: str,
    policy_version: str,
) -> VerifiedPaperSoak:
    """Verify an evidence URI and bind it to the expected qualification key."""
    match = _EVIDENCE_REF_RE.fullmatch(str(evidence_ref or "").strip())
    if match is None:
        raise PaperSoakError("paper-soak evidence reference is invalid")
    campaign_id, expected_signature = match.groups()
    manifest, _, _, records = _load_campaign(campaign_id)
    if not records or not secrets.compare_digest(
        str(records[-1]["signature"]), expected_signature
    ):
        raise PaperSoakError("paper-soak evidence reference does not name the chain tip")
    expected = {
        "broker": str(broker),
        "account_ref_sha256": str(account_ref_sha256),
        "build_revision": str(build_revision),
        "qualification_policy_version": str(policy_version),
    }
    for field, value in expected.items():
        if not secrets.compare_digest(str(manifest.get(field, "")), value):
            raise PaperSoakError(f"paper-soak evidence {field} does not match qualification")
    observed = _accepted_streak(records)
    completed = _completed_drills(records)
    eligible = (
        len(observed) >= MIN_PAPER_SOAK_TRADING_DAYS
        and all(name in completed for name in REQUIRED_DRILLS)
    )
    return VerifiedPaperSoak(
        observed_trading_days=observed,
        completed_drills=completed,
        eligible=eligible,
    )


def _validate_observation(
    manifest: Mapping[str, Any],
    session: Mapping[str, Any],
    observation: Mapping[str, object],
) -> tuple[list[str], dict[str, str], list[str]]:
    failures: list[str] = []
    profile_id = str(manifest["profile_id"])
    if str(observation.get("profile_id", "")).strip().lower() != profile_id:
        failures.append("profile_mismatch")

    opens_at = _parse_datetime(session["opens_at"], "opens_at")
    closes_at = _parse_datetime(session["closes_at"], "closes_at")
    heartbeats = _parse_heartbeats(observation.get("heartbeat_times"), failures)
    if heartbeats:
        if heartbeats[0] > opens_at or heartbeats[-1] < closes_at:
            failures.append("session_coverage_incomplete")
        gaps = [
            (right - left).total_seconds()
            for left, right in zip(heartbeats, heartbeats[1:])
        ]
        if gaps and max(gaps) > _MAX_HEARTBEAT_GAP_SECONDS:
            failures.append("heartbeat_gap")

    baseline_at = _try_datetime(observation.get("baseline_observed_at"))
    opening_equity = _positive_float(observation.get("opening_equity_usd"))
    if (
        baseline_at is None
        or baseline_at > opens_at
        or (opens_at - baseline_at).total_seconds() > 3600
        or opening_equity is None
    ):
        failures.append("opening_equity_baseline_invalid")

    connection = _mapping(observation.get("connection"))
    account = _mapping(observation.get("account"))
    open_orders = _mapping(observation.get("open_orders"))
    quote = _mapping(observation.get("quote"))
    artifacts = {
        "connection_sha256": _payload_digest(connection),
        "account_sha256": _payload_digest(account),
        "open_orders_sha256": _payload_digest(open_orders),
        "quote_sha256": _payload_digest(quote),
        "order_probe_sha256": _payload_digest(observation.get("order_probe")),
        "heartbeat_sha256": _payload_digest(observation.get("heartbeat_times")),
        "connection_history_sha256": _payload_digest(
            observation.get("connection_history")
        ),
    }

    if str(connection.get("status", "")).lower() != "ok":
        failures.append("connector_unavailable")
    if not _paper_identity_proven(connection, profile_id):
        failures.append("paper_environment_unproven")
    if not _paper_identity_proven(account, profile_id):
        failures.append("account_profile_mismatch")
    if _sha256_text(_extract_account_ref(account)) != str(manifest["account_ref_sha256"]):
        failures.append("account_ref_mismatch")
    if normalize_account_equity_usd(account) is None:
        failures.append("account_equity_unavailable")

    rows = open_orders.get("open_orders")
    if str(open_orders.get("status", "")).lower() != "ok" or not isinstance(rows, list):
        failures.append("open_orders_unavailable")
    elif not all(isinstance(row, dict) for row in rows):
        failures.append("open_orders_invalid")
    else:
        if any(not str(row.get("client_order_id", "")).strip() for row in rows):
            failures.append("open_order_client_id_missing")
        elif open_order_reservations_usd(open_orders, now=closes_at) is None:
            failures.append("open_order_notional_unverifiable")

    symbol = str(observation.get("symbol", "")).strip().upper()
    snapshot = normalize_quote(quote, symbol=symbol, observed_at=closes_at)
    if snapshot is None:
        failures.append("quote_invalid")
    else:
        age = (closes_at - snapshot.source_ts).total_seconds()
        if age > _MAX_QUOTE_AGE_SECONDS:
            failures.append("stale_quote")
        elif age < -_MAX_CLOCK_DRIFT_SECONDS:
            failures.append("quote_clock_drift")

    _validate_connection_history(
        observation.get("connection_history"), heartbeats, observation.get("drills"), failures
    )
    _validate_order_probe(observation.get("order_probe"), failures)
    passed_drills = _validate_drills(observation.get("drills"), failures)
    artifacts.update(
        {
            f"drill_{name}_sha256": str(
                _mapping(_mapping(observation.get("drills")).get(name)).get(
                    "artifact_sha256", ""
                )
            )
            for name in passed_drills
        }
    )
    return _dedupe(failures), artifacts, passed_drills


def _validate_connection_history(
    value: object,
    heartbeats: Sequence[datetime],
    drills: object,
    failures: list[str],
) -> None:
    if not isinstance(value, list) or len(value) != len(heartbeats) or len(value) > 5000:
        failures.append("connection_history_invalid")
        return
    statuses: list[str] = []
    for expected_at, item in zip(heartbeats, value):
        row = _mapping(item)
        observed_at = _try_datetime(row.get("at"))
        status = str(row.get("status", "")).strip().lower()
        if observed_at != expected_at or not status:
            failures.append("connection_history_invalid")
            return
        statuses.append(status)
    if statuses[-1] != "ok":
        failures.append("disconnect_unrecovered")
        return
    if any(status != "ok" for status in statuses[:-1]):
        recovery = _mapping(_mapping(drills).get("disconnect_recovery"))
        if recovery.get("passed") is not True:
            failures.append("disconnect_recovery_unproven")


def _validate_order_probe(value: object, failures: list[str]) -> None:
    probe = _mapping(value)
    client_order_id = str(probe.get("client_order_id", "")).strip()
    initial = _mapping(probe.get("initial"))
    replay = _mapping(probe.get("replay"))
    if not client_order_id:
        failures.append("client_order_id_probe_missing")
        return
    if (
        str(initial.get("status", "")).lower() != "ok"
        or str(initial.get("client_order_id", "")).strip() != client_order_id
    ):
        failures.append("client_order_id_echo_failed")
    initial_order_id = str(initial.get("order_id", "")).strip()
    if (
        str(replay.get("status", "")).lower() != "ok"
        or str(replay.get("client_order_id", "")).strip() != client_order_id
        or replay.get("idempotency_replayed") is not True
        or not initial_order_id
        or str(replay.get("order_id", "")).strip() != initial_order_id
    ):
        failures.append("idempotency_replay_failed")


def _validate_drills(value: object, failures: list[str]) -> list[str]:
    drills = _mapping(value)
    unknown = sorted(set(drills) - set(REQUIRED_DRILLS))
    if unknown:
        failures.append("unknown_drill")
    passed: list[str] = []
    for name in REQUIRED_DRILLS:
        if name not in drills:
            continue
        result = _mapping(drills[name])
        digest = str(result.get("artifact_sha256", "")).strip().lower()
        if result.get("passed") is True and _DIGEST_RE.fullmatch(digest):
            passed.append(name)
        else:
            failures.append(f"drill_{name}_failed")
    return passed


def _parse_heartbeats(value: object, failures: list[str]) -> list[datetime]:
    if not isinstance(value, list) or len(value) < 2:
        failures.append("heartbeat_evidence_missing")
        return []
    parsed: list[datetime] = []
    for item in value:
        timestamp = _try_datetime(item)
        if timestamp is None:
            failures.append("heartbeat_timestamp_invalid")
            return []
        if parsed and timestamp <= parsed[-1]:
            failures.append("heartbeat_order_invalid")
            return []
        parsed.append(timestamp)
    return parsed


def _paper_identity_proven(payload: Mapping[str, Any], profile_id: str) -> bool:
    payload_profile = str(payload.get("profile_id") or payload.get("profile") or "").strip().lower()
    if payload_profile and payload_profile != profile_id:
        return False
    environment = str(payload.get("environment", "")).strip().lower()
    markers = (
        payload.get("is_paper") is True,
        payload.get("is_testnet") is True,
        payload.get("is_demo") is True,
        environment == "paper",
        str(payload.get("trd_env", "")).strip().lower() in {"paper", "simulate", "simulation"},
        bool(str(payload.get("paper_guard", "")).strip()),
    )
    return any(markers)


def _extract_account_ref(payload: Mapping[str, Any]) -> str:
    keys = ("account_ref", "account_number", "account_id", "acc_id", "uid")
    for source in (payload, _mapping(payload.get("account"))):
        for key in keys:
            value = source.get(key)
            if value not in (None, "") and not isinstance(value, (dict, list)):
                return str(value).strip()
    account_value = payload.get("account")
    return str(account_value).strip() if isinstance(account_value, str) else ""


def _parse_calendar(calendar: Mapping[str, object]) -> tuple[str, list[dict[str, str]]]:
    if not isinstance(calendar, Mapping):
        raise PaperSoakError("calendar must be an object")
    calendar_id = _bounded_text(calendar.get("calendar_id"), "calendar_id", 128)
    raw_sessions = calendar.get("sessions")
    if not isinstance(raw_sessions, list) or len(raw_sessions) < MIN_PAPER_SOAK_TRADING_DAYS:
        raise PaperSoakError("calendar must contain at least 30 scheduled trading sessions")
    sessions: list[dict[str, str]] = []
    previous_day: date | None = None
    previous_close: datetime | None = None
    for raw in raw_sessions:
        if not isinstance(raw, Mapping):
            raise PaperSoakError("calendar session must be an object")
        try:
            trading_day = date.fromisoformat(str(raw.get("trading_day", "")))
        except ValueError as exc:
            raise PaperSoakError("trading_day must be ISO-8601") from exc
        opens_at = _parse_datetime(raw.get("opens_at"), "opens_at")
        closes_at = _parse_datetime(raw.get("closes_at"), "closes_at")
        if closes_at <= opens_at or (closes_at - opens_at).total_seconds() > 24 * 60 * 60:
            raise PaperSoakError("calendar session bounds are invalid")
        if previous_day is not None and trading_day <= previous_day:
            raise PaperSoakError("calendar trading days must be unique and increasing")
        if previous_close is not None and opens_at <= previous_close:
            raise PaperSoakError("calendar sessions must not overlap")
        sessions.append(
            {
                "trading_day": trading_day.isoformat(),
                "opens_at": opens_at.isoformat(),
                "closes_at": closes_at.isoformat(),
            }
        )
        previous_day = trading_day
        previous_close = closes_at
    return calendar_id, sessions


def _load_campaign(
    campaign_id: str,
    *,
    require_private: bool = False,
) -> tuple[
    dict[str, Any],
    Ed25519PrivateKey | None,
    list[dict[str, str]],
    list[dict[str, Any]],
]:
    campaign_dir = _campaign_dir(campaign_id)
    if campaign_dir.is_symlink() or not campaign_dir.is_dir():
        raise PaperSoakError("paper-soak campaign is missing or insecure")
    _assert_private(campaign_dir, directory=True)
    manifest_path = campaign_dir / _MANIFEST_FILENAME
    _assert_private(manifest_path)
    if manifest_path.stat().st_size > _MAX_MANIFEST_BYTES:
        raise PaperSoakError("paper-soak manifest is too large")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise PaperSoakError("paper-soak campaign cannot be read") from exc
    if not isinstance(manifest, dict):
        raise PaperSoakError("paper-soak manifest is invalid")
    public_hex = str(manifest.get("public_key_ed25519", "")).strip().lower()
    if not _DIGEST_RE.fullmatch(public_hex):
        raise PaperSoakError("paper-soak public signing key is invalid")
    try:
        public_key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_hex))
    except ValueError as exc:
        raise PaperSoakError("paper-soak public signing key is invalid") from exc
    supplied = str(manifest.get("manifest_signature", ""))
    if not _verify_signature(public_key, manifest, supplied):
        raise PaperSoakError("paper-soak manifest signature is invalid")
    if (
        manifest.get("schema_version") != SOAK_EVIDENCE_SCHEMA_VERSION
        or manifest.get("evidence_policy_version") != SOAK_EVIDENCE_POLICY_VERSION
        or manifest.get("campaign_id") != campaign_id
        or manifest.get("required_trading_days") != MIN_PAPER_SOAK_TRADING_DAYS
        or tuple(manifest.get("required_drills", ())) != REQUIRED_DRILLS
    ):
        raise PaperSoakError("paper-soak manifest schema is invalid")
    _, sessions = _parse_calendar(
        {"calendar_id": manifest.get("calendar_id"), "sessions": manifest.get("sessions")}
    )
    if not secrets.compare_digest(
        str(manifest.get("calendar_sha256", "")),
        hashlib.sha256(_canonical(sessions)).hexdigest(),
    ):
        raise PaperSoakError("paper-soak calendar integrity failure")
    private_key: Ed25519PrivateKey | None = None
    if require_private:
        key_path = _private_key_path(campaign_id)
        _assert_private(key_path)
        if key_path.stat().st_size != 32:
            raise PaperSoakError("paper-soak private signing key is invalid")
        try:
            private_key = Ed25519PrivateKey.from_private_bytes(key_path.read_bytes())
        except (OSError, ValueError) as exc:
            raise PaperSoakError("paper-soak private signing key is invalid") from exc
        derived_public = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        ).hex()
        if not secrets.compare_digest(public_hex, derived_public):
            raise PaperSoakError("paper-soak private key does not match the manifest")
    records = _read_records(campaign_id, public_key, supplied, sessions)
    return manifest, private_key, sessions, records


def _read_records(
    campaign_id: str,
    public_key: Ed25519PublicKey,
    manifest_signature: str,
    sessions: Sequence[Mapping[str, str]],
) -> list[dict[str, Any]]:
    path = evidence_ledger_path(campaign_id)
    if path.is_symlink():
        raise PaperSoakError("paper-soak evidence ledger must not be a symlink")
    if not path.exists():
        return []
    _assert_private(path)
    if path.stat().st_size > _MAX_LEDGER_BYTES:
        raise PaperSoakError("paper-soak evidence ledger is too large")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise PaperSoakError("paper-soak evidence ledger is unreadable") from exc
    records: list[dict[str, Any]] = []
    previous = manifest_signature
    for index, line in enumerate(lines, start=1):
        try:
            record = json.loads(line)
        except (TypeError, ValueError) as exc:
            raise PaperSoakError(f"paper-soak evidence integrity failure at line {index}") from exc
        if not isinstance(record, dict) or index > len(sessions):
            raise PaperSoakError(f"paper-soak evidence integrity failure at line {index}")
        session = sessions[index - 1]
        if (
            record.get("schema_version") != SOAK_EVIDENCE_SCHEMA_VERSION
            or record.get("sequence") != index
            or record.get("campaign_id") != campaign_id
            or record.get("trading_day") != session["trading_day"]
            or record.get("opens_at") != session["opens_at"]
            or record.get("closes_at") != session["closes_at"]
            or record.get("previous_signature") != previous
        ):
            raise PaperSoakError(f"paper-soak evidence integrity failure at line {index}")
        supplied = str(record.get("signature", ""))
        if not _verify_signature(public_key, record, supplied):
            raise PaperSoakError(f"paper-soak evidence signature failure at line {index}")
        if not isinstance(record.get("accepted"), bool):
            raise PaperSoakError(f"paper-soak evidence integrity failure at line {index}")
        previous = supplied
        records.append(record)
    return records


def _append_record(campaign_id: str, record: Mapping[str, Any]) -> None:
    path = evidence_ledger_path(campaign_id)
    encoded = (json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
    current_size = path.stat().st_size if path.exists() else 0
    if current_size + len(encoded) > _MAX_LEDGER_BYTES:
        raise PaperSoakError("paper-soak evidence ledger is too large")
    try:
        fd = os.open(
            path,
            os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW,
            0o600,
        )
    except OSError as exc:
        raise PaperSoakError("paper-soak evidence ledger cannot be opened safely") from exc
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, encoded)
        os.fsync(fd)
    finally:
        os.close(fd)


def _enter_registry_paper_soak(
    *,
    broker: str,
    account_ref: str,
    build_revision: str,
    actor: str,
    current: datetime,
    last_session_close: datetime,
) -> None:
    with _registry_lock():
        registry = _read_registry_raw()
        record = _find_registry_record(registry, broker, account_ref, build_revision)
        if record is None:
            disabled_at = current - timedelta(microseconds=1)
            record = {
                "broker": broker,
                "account_ref": account_ref,
                "build_revision": build_revision,
                "policy_version": QUALIFICATION_POLICY_VERSION,
                "state": QualificationState.PAPER_SOAK.value,
                "expires_at": max(
                    current + timedelta(days=120),
                    last_session_close + timedelta(days=30),
                ).isoformat(),
                "state_history": [
                    {
                        "state": QualificationState.DISABLED.value,
                        "at": disabled_at.isoformat(),
                        "actor": actor,
                    },
                    {
                        "state": QualificationState.PAPER_SOAK.value,
                        "at": current.isoformat(),
                        "actor": actor,
                    },
                ],
                "paper_soak": {
                    "required_trading_days": MIN_PAPER_SOAK_TRADING_DAYS,
                    "observed_trading_days": [],
                    "consecutive": False,
                    "accepted": False,
                    "evidence_policy_version": SOAK_EVIDENCE_POLICY_VERSION,
                },
                "evidence_refs": [],
            }
            registry["records"].append(record)
        else:
            state = QualificationState(str(record.get("state")))
            if state not in (QualificationState.DISABLED, QualificationState.REVOKED):
                raise PaperSoakError(
                    f"qualification state {state.value} cannot start another paper soak"
                )
            if not transition_allowed(state, QualificationState.PAPER_SOAK):
                raise PaperSoakError("qualification transition to paper_soak is forbidden")
            last_at = _parse_datetime(record["state_history"][-1]["at"], "state_history.at")
            if current <= last_at:
                raise PaperSoakError("paper-soak transition timestamp is not monotonic")
            record["state"] = QualificationState.PAPER_SOAK.value
            record["state_history"].append(
                {"state": "paper_soak", "at": current.isoformat(), "actor": actor}
            )
            record["paper_soak"] = {
                "required_trading_days": MIN_PAPER_SOAK_TRADING_DAYS,
                "observed_trading_days": [],
                "consecutive": False,
                "accepted": False,
                "evidence_policy_version": SOAK_EVIDENCE_POLICY_VERSION,
            }
            record["evidence_refs"] = []
        _write_registry_raw(registry)


def _sync_registry_progress(
    manifest: Mapping[str, Any], records: Sequence[Mapping[str, Any]]
) -> None:
    observed = _accepted_streak(records)
    ref = _evidence_ref(str(manifest["campaign_id"]), records[-1]["signature"])
    with _registry_lock():
        registry = _read_registry_raw()
        record = _find_registry_record_from_manifest(registry, manifest)
        if record is None or record.get("state") != QualificationState.PAPER_SOAK.value:
            raise PaperSoakError("paper-soak registry record is missing or not in paper_soak")
        record["paper_soak"] = {
            "required_trading_days": MIN_PAPER_SOAK_TRADING_DAYS,
            "observed_trading_days": [day.isoformat() for day in observed],
            "consecutive": bool(observed),
            "accepted": False,
            "evidence_policy_version": SOAK_EVIDENCE_POLICY_VERSION,
        }
        record["evidence_refs"] = [ref]
        _write_registry_raw(registry)


def _promote_registry(
    manifest: Mapping[str, Any],
    observed: Sequence[date],
    evidence_ref: str,
    actor: str,
    current: datetime,
) -> None:
    with _registry_lock():
        registry = _read_registry_raw()
        record = _find_registry_record_from_manifest(registry, manifest)
        if record is None or record.get("state") != QualificationState.PAPER_SOAK.value:
            raise PaperSoakError("only paper_soak may advance to pilot_eligible")
        if not transition_allowed(
            QualificationState.PAPER_SOAK, QualificationState.PILOT_ELIGIBLE
        ):
            raise PaperSoakError("pilot_eligible transition is forbidden")
        last_at = _parse_datetime(record["state_history"][-1]["at"], "state_history.at")
        if current <= last_at:
            raise PaperSoakError("pilot eligibility timestamp is not monotonic")
        record["state"] = QualificationState.PILOT_ELIGIBLE.value
        record["state_history"].append(
            {
                "state": QualificationState.PILOT_ELIGIBLE.value,
                "at": current.isoformat(),
                "actor": actor,
            }
        )
        record["paper_soak"] = {
            "required_trading_days": MIN_PAPER_SOAK_TRADING_DAYS,
            "observed_trading_days": [day.isoformat() for day in observed],
            "consecutive": True,
            "accepted": True,
            "evidence_policy_version": SOAK_EVIDENCE_POLICY_VERSION,
        }
        record["evidence_refs"] = [evidence_ref]
        _write_registry_raw(registry)


def _registry_state(manifest: Mapping[str, Any]) -> QualificationState:
    registry = _read_registry_raw()
    record = _find_registry_record_from_manifest(registry, manifest)
    if record is None:
        raise PaperSoakError("paper-soak registry record is missing")
    return QualificationState(str(record.get("state")))


def _find_registry_record_from_manifest(
    registry: Mapping[str, Any], manifest: Mapping[str, Any]
) -> dict[str, Any] | None:
    account_hash = str(manifest["account_ref_sha256"])
    matches = [
        record
        for record in registry["records"]
        if isinstance(record, dict)
        and record.get("broker") == manifest["broker"]
        and record.get("build_revision") == manifest["build_revision"]
        and record.get("policy_version") == manifest["qualification_policy_version"]
        and secrets.compare_digest(
            _sha256_text(str(record.get("account_ref", ""))), account_hash
        )
    ]
    if len(matches) > 1:
        raise PaperSoakError("qualification registry has an ambiguous exact key")
    return matches[0] if matches else None


def _find_registry_record(
    registry: Mapping[str, Any], broker: str, account_ref: str, build_revision: str
) -> dict[str, Any] | None:
    matches = [
        record
        for record in registry["records"]
        if isinstance(record, dict)
        and (
            record.get("broker"),
            record.get("account_ref"),
            record.get("build_revision"),
            record.get("policy_version"),
        )
        == (broker, account_ref, build_revision, QUALIFICATION_POLICY_VERSION)
    ]
    if len(matches) > 1:
        raise PaperSoakError("qualification registry has an ambiguous exact key")
    return matches[0] if matches else None


def _read_registry_raw() -> dict[str, Any]:
    path = live_root() / _REGISTRY_FILENAME
    if not path.exists():
        return {"schema_version": QUALIFICATION_SCHEMA_VERSION, "records": []}
    if path.is_symlink() or not path.is_file():
        raise PaperSoakError("qualification registry must be a regular private file")
    _assert_private(path)
    if path.stat().st_size > _MAX_REGISTRY_BYTES:
        raise PaperSoakError("qualification registry is too large")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise PaperSoakError("qualification registry is unreadable") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != QUALIFICATION_SCHEMA_VERSION
        or not isinstance(payload.get("records"), list)
    ):
        raise PaperSoakError("qualification registry schema is invalid")
    return payload


def _write_registry_raw(registry: Mapping[str, Any]) -> None:
    from src.live.qualification import _parse_registry

    try:
        _parse_registry(dict(registry))
    except (KeyError, TypeError, ValueError) as exc:
        raise PaperSoakError(f"qualification registry update is invalid: {exc}") from exc
    path = live_root() / _REGISTRY_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    _tighten_directory(path.parent)
    _atomic_write_json(path, registry, replace=True)


@contextmanager
def _campaign_lock(campaign_id: str) -> Iterator[None]:
    campaign_dir = _campaign_dir(campaign_id)
    _assert_private(campaign_dir, directory=True)
    path = campaign_dir / _LOCK_FILENAME
    try:
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    except OSError as exc:
        raise PaperSoakError("paper-soak campaign lock cannot be opened safely") from exc
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


@contextmanager
def _registry_lock() -> Iterator[None]:
    root = live_root()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    _tighten_directory(root)
    try:
        fd = os.open(
            root / _REGISTRY_LOCK_FILENAME,
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
            0o600,
        )
    except OSError as exc:
        raise PaperSoakError("qualification registry lock cannot be opened safely") from exc
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


def _accepted_streak(records: Sequence[Mapping[str, Any]]) -> tuple[date, ...]:
    streak: list[date] = []
    for record in reversed(records):
        if record.get("accepted") is not True:
            break
        streak.append(date.fromisoformat(str(record["trading_day"])))
    return tuple(reversed(streak))


def _completed_drills(records: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    current_streak: list[Mapping[str, Any]] = []
    for record in reversed(records):
        if record.get("accepted") is not True:
            break
        current_streak.append(record)
    seen = {
        str(name)
        for record in current_streak
        for name in record.get("passed_drills", [])
        if str(name) in REQUIRED_DRILLS
    }
    return tuple(name for name in REQUIRED_DRILLS if name in seen)


def _evidence_ref(campaign_id: str, signature: object) -> str:
    value = str(signature or "")
    if not _SIGNATURE_RE.fullmatch(value):
        raise PaperSoakError("paper-soak evidence signature is invalid")
    return f"node12-paper-soak-v1://{campaign_id}/{value}"


def _campaign_dir(campaign_id: str) -> Path:
    value = str(campaign_id or "").strip().lower()
    if not _CAMPAIGN_RE.fullmatch(value):
        raise PaperSoakError("campaign_id must be 24 lowercase hex characters")
    return live_root() / _EVIDENCE_DIRNAME / value


def _private_key_path(campaign_id: str) -> Path:
    value = str(campaign_id or "").strip().lower()
    if not _CAMPAIGN_RE.fullmatch(value):
        raise PaperSoakError("campaign_id must be 24 lowercase hex characters")
    return live_root() / _SIGNING_KEY_DIRNAME / f"{value}.ed25519"


def _signature(private_key: Ed25519PrivateKey, payload: Mapping[str, Any]) -> str:
    unsigned = {key: value for key, value in payload.items() if key not in {"signature", "manifest_signature"}}
    return private_key.sign(_canonical(unsigned)).hex()


def _verify_signature(
    public_key: Ed25519PublicKey,
    payload: Mapping[str, Any],
    signature: str,
) -> bool:
    if not _SIGNATURE_RE.fullmatch(str(signature or "")):
        return False
    unsigned = {
        key: value
        for key, value in payload.items()
        if key not in {"signature", "manifest_signature"}
    }
    try:
        public_key.verify(bytes.fromhex(signature), _canonical(unsigned))
    except (InvalidSignature, ValueError):
        return False
    return True


def _canonical(payload: object) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")


def _payload_digest(payload: object) -> str:
    return hashlib.sha256(_canonical(payload)).hexdigest()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def _atomic_write_json(path: Path, payload: Mapping[str, Any], *, replace: bool) -> None:
    encoded = (json.dumps(payload, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
    if len(encoded) > _MAX_REGISTRY_BYTES:
        raise PaperSoakError(f"{path.name} exceeds the safety size limit")
    if not replace and path.exists():
        raise PaperSoakError(f"{path.name} already exists")
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}-{secrets.token_hex(4)}")
    fd = os.open(
        tmp,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
    )
    try:
        os.write(fd, encoded)
        os.fsync(fd)
    finally:
        os.close(fd)
    if not replace and path.exists():
        tmp.unlink(missing_ok=True)
        raise PaperSoakError(f"{path.name} already exists")
    os.replace(tmp, path)
    path.chmod(0o600)


def _write_new_bytes(path: Path, payload: bytes) -> None:
    fd = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
    )
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, payload)
        os.fsync(fd)
    finally:
        os.close(fd)


def _assert_private(path: Path, *, directory: bool = False) -> None:
    if path.is_symlink():
        raise PaperSoakError(f"{path.name} must not be a symlink")
    try:
        metadata = path.stat()
    except OSError as exc:
        raise PaperSoakError(f"{path.name} cannot be inspected") from exc
    if directory and not stat.S_ISDIR(metadata.st_mode):
        raise PaperSoakError(f"{path.name} must be a directory")
    if not directory and not stat.S_ISREG(metadata.st_mode):
        raise PaperSoakError(f"{path.name} must be a regular file")
    if stat.S_IMODE(metadata.st_mode) & 0o077:
        raise PaperSoakError(f"{path.name} permissions are not private")
    if hasattr(os, "getuid") and metadata.st_uid != os.getuid():
        raise PaperSoakError(f"{path.name} has the wrong owner")


def _tighten_directory(path: Path) -> None:
    if path.is_symlink() or not path.is_dir():
        raise PaperSoakError(f"{path.name} must be a private directory")
    try:
        path.chmod(0o700)
    except OSError as exc:
        raise PaperSoakError(f"cannot protect directory {path.name}") from exc
    _assert_private(path, directory=True)


def _utc_now(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        raise PaperSoakError("now must be timezone-aware")
    return value.astimezone(timezone.utc)


def _try_datetime(value: object) -> datetime | None:
    try:
        return _parse_datetime(value, "timestamp")
    except PaperSoakError:
        return None


def _parse_datetime(value: object, field: str) -> datetime:
    token = str(value or "").strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(token)
    except ValueError as exc:
        raise PaperSoakError(f"{field} must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise PaperSoakError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _bounded_text(value: object, field: str, limit: int) -> str:
    token = str(value or "").strip()
    if not token or len(token) > limit or any(ch in token for ch in ("\n", "\r", "\x00")):
        raise PaperSoakError(f"{field} is invalid")
    return token


def _actor(value: object) -> str:
    token = str(value or "").strip()
    if not _ACTOR_RE.fullmatch(token):
        raise PaperSoakError("actor is invalid")
    return token


def _positive_float(value: object) -> float | None:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) and parsed > 0 else None


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _dedupe(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(values))
