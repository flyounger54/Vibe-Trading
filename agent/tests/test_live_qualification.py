"""Node 12A: fail-closed live qualification state machine and registry."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

import src.live.paths as live_paths
from src.live.qualification import (
    BUILD_REVISION_ENV,
    LIVE_BROKER_ENV,
    QUALIFICATION_POLICY_VERSION,
    QualificationState,
    evaluate_live_qualification,
    transition_allowed,
)


BUILD = "e9f54e0ef19054a690690bdb3c12fa2154b20ebb"


@pytest.fixture()
def qualification_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(live_paths, "get_runtime_root", lambda: tmp_path)
    monkeypatch.delenv(LIVE_BROKER_ENV, raising=False)
    monkeypatch.delenv(BUILD_REVISION_ENV, raising=False)
    return tmp_path


def _trading_days(count: int = 30) -> list[str]:
    first = date(2026, 1, 2)
    return [(first + timedelta(days=offset)).isoformat() for offset in range(count)]


def _record(*, state: str = "pilot_active", account_ref: str = "acct-1") -> dict:
    history = [
        {"state": "disabled", "at": "2026-01-01T00:00:00+00:00", "actor": "operator"},
        {"state": "paper_soak", "at": "2026-01-02T00:00:00+00:00", "actor": "operator"},
        {"state": "pilot_eligible", "at": "2026-02-01T00:00:00+00:00", "actor": "release-gate"},
        {"state": "pilot_active", "at": "2026-02-02T00:00:00+00:00", "actor": "operator"},
    ]
    if state == "revoked":
        history.append(
            {
                "state": "revoked",
                "at": "2026-02-03T00:00:00+00:00",
                "actor": "operator",
                "reason": "ledger mismatch",
            }
        )
    elif state != "pilot_active":
        history = history[: {"disabled": 1, "paper_soak": 2, "pilot_eligible": 3}[state]]
    return {
        "broker": "alpaca",
        "account_ref": account_ref,
        "build_revision": BUILD,
        "policy_version": QUALIFICATION_POLICY_VERSION,
        "state": state,
        "expires_at": "2099-01-01T00:00:00+00:00",
        "state_history": history,
        "paper_soak": {
            "required_trading_days": 30,
            "observed_trading_days": _trading_days(),
            "consecutive": True,
            "accepted": True,
        },
        "evidence_refs": ["node12-paper-soak://alpaca/acct-1/run-1"],
    }


def _write_registry(root: Path, records: list[dict]) -> Path:
    live_dir = root / "live"
    live_dir.mkdir(parents=True, exist_ok=True)
    path = live_dir / "qualification-registry.json"
    path.write_text(json.dumps({"schema_version": 1, "records": records}), encoding="utf-8")
    path.chmod(0o600)
    return path


def _enable(monkeypatch: pytest.MonkeyPatch, broker: str = "alpaca") -> None:
    monkeypatch.setenv(LIVE_BROKER_ENV, broker)
    monkeypatch.setenv(BUILD_REVISION_ENV, BUILD)


def test_default_is_closed_without_explicit_single_broker_selection(
    qualification_root: Path,
) -> None:
    decision = evaluate_live_qualification("alpaca", "acct-1")

    assert decision.allowed is False
    assert decision.code == "live_broker_not_enabled"
    assert decision.state == QualificationState.DISABLED


def test_exact_qualified_key_allows_pilot(
    qualification_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable(monkeypatch)
    _write_registry(qualification_root, [_record()])

    decision = evaluate_live_qualification("alpaca", "acct-1")

    assert decision.allowed is True
    assert decision.code == "qualified"
    assert decision.state == QualificationState.PILOT_ACTIVE
    assert decision.build_revision == BUILD
    assert decision.policy_version == QUALIFICATION_POLICY_VERSION


@pytest.mark.parametrize(
    "mutation,expected",
    [
        (lambda record: record.update(account_ref="acct-other"), "qualification_not_found"),
        (lambda record: record.update(build_revision="0" * 40), "qualification_not_found"),
        (lambda record: record.update(policy_version="old-policy"), "qualification_not_found"),
        (lambda record: record.update(state="revoked"), "qualification_invalid"),
        (lambda record: record.update(expires_at="2020-01-01T00:00:00+00:00"), "qualification_expired"),
    ],
)
def test_mismatch_revocation_and_expiry_fail_closed(
    qualification_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation,
    expected: str,
) -> None:
    _enable(monkeypatch)
    record = _record()
    mutation(record)
    _write_registry(qualification_root, [record])

    decision = evaluate_live_qualification("alpaca", "acct-1")

    assert decision.allowed is False
    assert decision.code == expected


def test_insecure_registry_permissions_fail_closed(
    qualification_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable(monkeypatch)
    path = _write_registry(qualification_root, [_record()])
    path.chmod(0o644)

    decision = evaluate_live_qualification("alpaca", "acct-1")

    assert decision.allowed is False
    assert decision.code == "qualification_registry_insecure"


def test_30_day_soak_and_state_history_are_mandatory(
    qualification_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable(monkeypatch)
    record = _record()
    record["paper_soak"]["observed_trading_days"] = _trading_days(29)
    _write_registry(qualification_root, [record])

    decision = evaluate_live_qualification("alpaca", "acct-1")

    assert decision.allowed is False
    assert decision.code == "qualification_invalid"


def test_revoked_exact_key_is_valid_but_never_allowed(
    qualification_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable(monkeypatch)
    _write_registry(qualification_root, [_record(state="revoked")])

    decision = evaluate_live_qualification("alpaca", "acct-1")

    assert decision.allowed is False
    assert decision.code == "qualification_state_not_active"
    assert decision.state == QualificationState.REVOKED


def test_in_progress_paper_soak_is_visible_but_not_live_eligible(
    qualification_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable(monkeypatch)
    record = _record(state="paper_soak")
    record["paper_soak"]["observed_trading_days"] = _trading_days(7)
    record["paper_soak"]["accepted"] = False
    record["evidence_refs"] = []
    _write_registry(qualification_root, [record])

    decision = evaluate_live_qualification("alpaca", "acct-1")

    assert decision.allowed is False
    assert decision.code == "qualification_state_not_active"
    assert decision.state == QualificationState.PAPER_SOAK
    assert decision.observed_trading_days == 7


def test_state_machine_rejects_skipping_manual_pilot_eligibility() -> None:
    assert transition_allowed(QualificationState.DISABLED, QualificationState.PAPER_SOAK)
    assert transition_allowed(QualificationState.PAPER_SOAK, QualificationState.PILOT_ELIGIBLE)
    assert transition_allowed(QualificationState.PILOT_ELIGIBLE, QualificationState.PILOT_ACTIVE)
    assert transition_allowed(QualificationState.PILOT_ACTIVE, QualificationState.REVOKED)
    assert not transition_allowed(QualificationState.DISABLED, QualificationState.PILOT_ACTIVE)
    assert not transition_allowed(QualificationState.PAPER_SOAK, QualificationState.PILOT_ACTIVE)


def test_pilot_activation_must_be_explicitly_operator_owned(
    qualification_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable(monkeypatch)
    record = _record()
    record["state_history"][-1]["actor"] = "agent"
    _write_registry(qualification_root, [record])

    decision = evaluate_live_qualification("alpaca", "acct-1")

    assert decision.allowed is False
    assert decision.code == "qualification_invalid"


def test_future_dated_qualification_never_activates(
    qualification_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable(monkeypatch)
    record = _record()
    record["state_history"][-1]["at"] = "2098-01-01T00:00:00+00:00"
    _write_registry(qualification_root, [record])

    decision = evaluate_live_qualification("alpaca", "acct-1")

    assert decision.allowed is False
    assert decision.code == "qualification_not_yet_valid"
