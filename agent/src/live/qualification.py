"""Fail-closed live-execution qualification gate (upgrade Node 12A).

Live execution is disabled unless all of the following independently agree:

* the operator explicitly selects exactly one broker in
  ``VIBE_TRADING_LIVE_BROKER``;
* the running build declares its immutable 40-hex revision in
  ``VIBE_TRADING_BUILD_REVISION``;
* a protected, read-only qualification registry contains an exact
  ``(broker, account_ref, build_revision, policy_version)`` record;
* the record followed the full disabled → paper soak → pilot eligible →
  pilot active state machine and carries at least 30 accepted trading days;
* the qualification has not expired or been revoked.

This module deliberately exposes no registry writer. Qualification promotion
is an operator/release action outside the agent tool surface. Missing,
ambiguous, malformed, insecurely permissioned, stale, or mismatched state is a
DENY. Read-only broker operations, direct-SDK cancellation, and the operator
halt/flatten path do not depend on this qualification decision. The legacy
remote-MCP cancellation wrapper remains a separate Node 12 follow-up.
"""

from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import Enum
from typing import Mapping

from src.live.paths import live_root

QUALIFICATION_SCHEMA_VERSION = 1
QUALIFICATION_POLICY_VERSION = "node12a-live-qualification-v1"
MIN_PAPER_SOAK_TRADING_DAYS = 30

LIVE_BROKER_ENV = "VIBE_TRADING_LIVE_BROKER"
BUILD_REVISION_ENV = "VIBE_TRADING_BUILD_REVISION"

_REGISTRY_FILENAME = "qualification-registry.json"
_MAX_REGISTRY_BYTES = 1_048_576
_BUILD_RE = re.compile(r"^[0-9a-f]{40}$")
_BROKER_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class QualificationState(str, Enum):
    """Operator-owned lifecycle for one exact live qualification key."""

    DISABLED = "disabled"
    PAPER_SOAK = "paper_soak"
    PILOT_ELIGIBLE = "pilot_eligible"
    PILOT_ACTIVE = "pilot_active"
    REVOKED = "revoked"


_ALLOWED_TRANSITIONS: dict[QualificationState, frozenset[QualificationState]] = {
    QualificationState.DISABLED: frozenset({QualificationState.PAPER_SOAK}),
    QualificationState.PAPER_SOAK: frozenset(
        {QualificationState.PILOT_ELIGIBLE, QualificationState.REVOKED}
    ),
    QualificationState.PILOT_ELIGIBLE: frozenset(
        {QualificationState.PILOT_ACTIVE, QualificationState.REVOKED}
    ),
    QualificationState.PILOT_ACTIVE: frozenset({QualificationState.REVOKED}),
    # A revoked key must repeat paper qualification; it cannot jump back live.
    QualificationState.REVOKED: frozenset({QualificationState.PAPER_SOAK}),
}


def transition_allowed(current: QualificationState, target: QualificationState) -> bool:
    """Return whether an operator may move between two qualification states."""
    return target in _ALLOWED_TRANSITIONS[current]


@dataclass(frozen=True)
class QualificationRecord:
    """Validated registry record for one exact broker/account/build/policy key."""

    broker: str
    account_ref: str
    build_revision: str
    policy_version: str
    state: QualificationState
    state_changed_at: datetime
    expires_at: datetime
    observed_trading_days: tuple[date, ...]
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True)
class QualificationDecision:
    """Stable gate result shared by order paths, runner control, and status."""

    allowed: bool
    code: str
    reason: str
    broker: str
    account_ref: str
    build_revision: str | None
    policy_version: str
    state: QualificationState
    observed_trading_days: int = 0

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable, credential-free decision snapshot."""
        return {
            "allowed": self.allowed,
            "code": self.code,
            "reason": self.reason,
            "broker": self.broker,
            "account_ref": self.account_ref,
            "build_revision": self.build_revision,
            "policy_version": self.policy_version,
            "state": self.state.value,
            "observed_trading_days": self.observed_trading_days,
            "required_trading_days": MIN_PAPER_SOAK_TRADING_DAYS,
        }


class _RegistryError(ValueError):
    """Internal fail-closed registry validation error carrying a stable code."""

    def __init__(self, code: str, reason: str) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason


def evaluate_live_qualification(
    broker: str,
    account_ref: str,
    *,
    environ: Mapping[str, str] | None = None,
    now: datetime | None = None,
) -> QualificationDecision:
    """Evaluate whether one exact broker account may execute live orders.

    The registry path is fixed under the runtime root and cannot be supplied by
    a caller. No broker network operation is performed here.
    """
    env = os.environ if environ is None else environ
    key = _normalize_broker(broker)
    account = str(account_ref or "").strip()
    current = _utc_now(now)

    selected_raw = str(env.get(LIVE_BROKER_ENV, "")).strip().lower()
    if not selected_raw:
        return _decision(
            False,
            "live_broker_not_enabled",
            "live execution is disabled; no broker was explicitly enabled",
            key,
            account,
        )
    if _normalize_broker(selected_raw) != selected_raw:
        return _decision(
            False,
            "live_broker_selection_invalid",
            "the live broker selection is invalid; exactly one broker key is required",
            key,
            account,
        )
    if selected_raw != key:
        return _decision(
            False,
            "live_broker_not_enabled",
            f"live execution is enabled only for {selected_raw}",
            key,
            account,
        )
    if not account or len(account) > 128:
        return _decision(
            False,
            "account_ref_invalid",
            "a bounded non-empty mandate account_ref is required",
            key,
            account,
        )

    build = str(env.get(BUILD_REVISION_ENV, "")).strip().lower()
    if not _BUILD_RE.fullmatch(build):
        return _decision(
            False,
            "build_revision_invalid",
            "the running build has no immutable 40-hex revision",
            key,
            account,
            build_revision=build or None,
        )

    try:
        records = _load_registry()
    except _RegistryError as exc:
        return _decision(
            False,
            exc.code,
            exc.reason,
            key,
            account,
            build_revision=build,
        )

    matches = [
        record
        for record in records
        if (
            record.broker,
            record.account_ref,
            record.build_revision,
            record.policy_version,
        )
        == (key, account, build, QUALIFICATION_POLICY_VERSION)
    ]
    if not matches:
        return _decision(
            False,
            "qualification_not_found",
            "no qualification matches this broker, account, build, and policy",
            key,
            account,
            build_revision=build,
        )
    if len(matches) != 1:
        return _decision(
            False,
            "qualification_ambiguous",
            "multiple qualifications match the same exact key",
            key,
            account,
            build_revision=build,
        )

    record = matches[0]
    if record.state_changed_at > current or (
        record.observed_trading_days
        and record.observed_trading_days[-1] > current.date()
    ):
        return _decision(
            False,
            "qualification_not_yet_valid",
            "qualification history or paper evidence is dated in the future",
            key,
            account,
            build_revision=build,
            state=record.state,
            observed_trading_days=len(record.observed_trading_days),
        )
    if current >= record.expires_at:
        return _decision(
            False,
            "qualification_expired",
            "the live qualification has expired",
            key,
            account,
            build_revision=build,
            state=record.state,
            observed_trading_days=len(record.observed_trading_days),
        )
    if record.state is not QualificationState.PILOT_ACTIVE:
        return _decision(
            False,
            "qualification_state_not_active",
            f"qualification state {record.state.value} does not permit live execution",
            key,
            account,
            build_revision=build,
            state=record.state,
            observed_trading_days=len(record.observed_trading_days),
        )
    return _decision(
        True,
        "qualified",
        "exact live pilot qualification is active",
        key,
        account,
        build_revision=build,
        state=record.state,
        observed_trading_days=len(record.observed_trading_days),
    )


def _load_registry() -> tuple[QualificationRecord, ...]:
    path = live_root() / _REGISTRY_FILENAME
    if not path.is_file():
        raise _RegistryError("qualification_registry_missing", "qualification registry is missing")
    if path.is_symlink():
        raise _RegistryError(
            "qualification_registry_insecure", "qualification registry must not be a symlink"
        )
    try:
        metadata = path.stat()
    except OSError as exc:
        raise _RegistryError(
            "qualification_registry_unreadable", f"qualification registry cannot be inspected: {exc}"
        ) from exc
    if metadata.st_size > _MAX_REGISTRY_BYTES:
        raise _RegistryError("qualification_registry_invalid", "qualification registry is too large")
    if os.name == "posix":
        if stat.S_IMODE(metadata.st_mode) & 0o077:
            raise _RegistryError(
                "qualification_registry_insecure",
                "qualification registry must not be accessible by group or other users",
            )
        if hasattr(os, "getuid") and metadata.st_uid != os.getuid():
            raise _RegistryError(
                "qualification_registry_insecure",
                "qualification registry must be owned by the current runtime user",
            )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise _RegistryError(
            "qualification_registry_invalid", f"qualification registry is unreadable or invalid JSON: {exc}"
        ) from exc
    try:
        return _parse_registry(raw)
    except (KeyError, TypeError, ValueError) as exc:
        raise _RegistryError(
            "qualification_invalid", f"qualification registry failed structural validation: {exc}"
        ) from exc


def _parse_registry(raw: object) -> tuple[QualificationRecord, ...]:
    if not isinstance(raw, dict):
        raise TypeError("registry root must be an object")
    if int(raw["schema_version"]) != QUALIFICATION_SCHEMA_VERSION:
        raise ValueError("unknown qualification registry schema")
    raw_records = raw["records"]
    if not isinstance(raw_records, list):
        raise TypeError("records must be a list")

    records = tuple(_parse_record(item) for item in raw_records)
    identities = [
        (record.broker, record.account_ref, record.build_revision, record.policy_version)
        for record in records
    ]
    if len(set(identities)) != len(identities):
        raise ValueError("duplicate qualification key")
    return records


def _parse_record(raw: object) -> QualificationRecord:
    if not isinstance(raw, dict):
        raise TypeError("qualification record must be an object")
    broker = _normalize_broker(str(raw["broker"]))
    if not broker:
        raise ValueError("broker is invalid")
    account_ref = str(raw["account_ref"]).strip()
    if not account_ref or len(account_ref) > 128:
        raise ValueError("account_ref is invalid")
    build = str(raw["build_revision"]).strip().lower()
    if not _BUILD_RE.fullmatch(build):
        raise ValueError("build_revision must be 40 lowercase hex characters")
    policy = str(raw["policy_version"]).strip()
    if not policy:
        raise ValueError("policy_version is required")
    state = QualificationState(str(raw["state"]))
    expires_at = _parse_datetime(raw["expires_at"], "expires_at")
    history = _validate_state_history(raw["state_history"], state)

    soak = raw["paper_soak"]
    if not isinstance(soak, dict):
        raise TypeError("paper_soak must be an object")
    required_days = int(soak["required_trading_days"])
    if required_days < MIN_PAPER_SOAK_TRADING_DAYS:
        raise ValueError("paper soak requires at least 30 trading days")
    observed = _parse_trading_days(soak["observed_trading_days"])
    consecutive = soak["consecutive"]
    accepted = soak["accepted"]
    if not isinstance(consecutive, bool) or not isinstance(accepted, bool):
        raise TypeError("paper soak consecutive/accepted flags must be booleans")
    if state in (QualificationState.PILOT_ELIGIBLE, QualificationState.PILOT_ACTIVE):
        if len(observed) < required_days:
            raise ValueError("paper soak has fewer observed days than required")
        if consecutive is not True or accepted is not True:
            raise ValueError("paper soak must be consecutive and accepted")
        eligible_at = next(
            at for history_state, at in history
            if history_state is QualificationState.PILOT_ELIGIBLE
        )
        if eligible_at.date() < observed[-1]:
            raise ValueError("pilot eligibility predates the final observed soak day")

    evidence = raw["evidence_refs"]
    if not isinstance(evidence, list):
        raise TypeError("evidence_refs must be a list")
    if state in (QualificationState.PILOT_ELIGIBLE, QualificationState.PILOT_ACTIVE) and not evidence:
        raise ValueError("at least one paper-soak evidence reference is required")
    evidence_refs = tuple(str(item).strip() for item in evidence)
    if any(not item or len(item) > 500 for item in evidence_refs):
        raise ValueError("evidence reference is invalid")

    return QualificationRecord(
        broker=broker,
        account_ref=account_ref,
        build_revision=build,
        policy_version=policy,
        state=state,
        state_changed_at=history[-1][1],
        expires_at=expires_at,
        observed_trading_days=observed,
        evidence_refs=evidence_refs,
    )


def _validate_state_history(
    raw: object, current: QualificationState
) -> tuple[tuple[QualificationState, datetime], ...]:
    if not isinstance(raw, list) or not raw:
        raise ValueError("state_history must be a non-empty list")
    previous_state: QualificationState | None = None
    previous_at: datetime | None = None
    history: list[tuple[QualificationState, datetime]] = []
    for index, event in enumerate(raw):
        if not isinstance(event, dict):
            raise TypeError("state history event must be an object")
        state_value = QualificationState(str(event["state"]))
        at = _parse_datetime(event["at"], "state_history.at")
        actor = str(event["actor"]).strip()
        if not actor or len(actor) > 128:
            raise ValueError("state history actor is invalid")
        if state_value is QualificationState.PILOT_ACTIVE and not (
            actor == "operator" or actor.startswith("operator:")
        ):
            raise ValueError("pilot activation requires an explicit operator actor")
        if state_value is QualificationState.REVOKED and not str(
            event.get("reason", "")
        ).strip():
            raise ValueError("revocation requires a reason")
        if index == 0 and state_value is not QualificationState.DISABLED:
            raise ValueError("state history must begin disabled")
        if previous_state is not None and not transition_allowed(previous_state, state_value):
            raise ValueError(f"invalid state transition {previous_state.value} -> {state_value.value}")
        if previous_at is not None and at <= previous_at:
            raise ValueError("state history timestamps must be strictly increasing")
        previous_state = state_value
        previous_at = at
        history.append((state_value, at))
    if previous_state is not current:
        raise ValueError("state history does not end at the declared current state")
    return tuple(history)


def _parse_trading_days(raw: object) -> tuple[date, ...]:
    if not isinstance(raw, list):
        raise TypeError("observed_trading_days must be a list")
    days: list[date] = []
    for item in raw:
        try:
            parsed = date.fromisoformat(str(item))
        except ValueError as exc:
            raise ValueError("observed trading day must be ISO-8601") from exc
        if days and parsed <= days[-1]:
            raise ValueError("observed trading days must be unique and strictly increasing")
        days.append(parsed)
    return tuple(days)


def _parse_datetime(raw: object, field: str) -> datetime:
    text = str(raw).strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{field} must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _utc_now(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return now.astimezone(timezone.utc)


def _normalize_broker(value: str) -> str:
    key = str(value or "").strip().lower()
    return key if _BROKER_RE.fullmatch(key) else ""


def _decision(
    allowed: bool,
    code: str,
    reason: str,
    broker: str,
    account_ref: str,
    *,
    build_revision: str | None = None,
    state: QualificationState = QualificationState.DISABLED,
    observed_trading_days: int = 0,
) -> QualificationDecision:
    return QualificationDecision(
        allowed=allowed,
        code=code,
        reason=reason,
        broker=broker,
        account_ref=account_ref,
        build_revision=build_revision,
        policy_version=QUALIFICATION_POLICY_VERSION,
        state=state,
        observed_trading_days=observed_trading_days,
    )
