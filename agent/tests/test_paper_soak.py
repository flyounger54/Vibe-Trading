"""Node 12C signed paper-soak evidence and pilot-eligibility gate."""

from __future__ import annotations

import copy
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.live import paper_soak
from src.live import paths as live_paths
from src.live.paper_soak import (
    PaperSoakError,
    collect_paper_soak_day,
    evidence_ledger_path,
    load_paper_soak_status,
    promote_paper_soak,
    record_paper_soak_day,
    run_paper_soak_session,
    start_paper_soak,
)
from src.live.qualification import (
    BUILD_REVISION_ENV,
    LIVE_BROKER_ENV,
    QualificationState,
    evaluate_live_qualification,
)

pytestmark = pytest.mark.unit

BUILD = "6d60fe0afdd5d1f537aefd797f0e8a0a8fa7653d"
PROFILE_ID = "alpaca-paper-trade"
ACCOUNT_REF = "acct-paper-1"
UTC = timezone.utc


@pytest.fixture()
def soak_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(live_paths, "get_runtime_root", lambda: tmp_path)
    monkeypatch.setattr(
        paper_soak,
        "profile_by_id",
        lambda profile_id: SimpleNamespace(
            id=profile_id,
            connector="alpaca",
            environment="paper",
            transport="broker_sdk",
            capabilities=("account.read", "orders.read", "quotes.read", "orders.place"),
            readonly=False,
        ),
    )
    return tmp_path


def _calendar(count: int = 30) -> dict[str, object]:
    first = date(2026, 8, 3)
    sessions = []
    for offset in range(count):
        day = first + timedelta(days=offset)
        opens_at = datetime.combine(day, datetime.min.time(), UTC) + timedelta(hours=14)
        closes_at = opens_at + timedelta(hours=6)
        sessions.append(
            {
                "trading_day": day.isoformat(),
                "opens_at": opens_at.isoformat(),
                "closes_at": closes_at.isoformat(),
            }
        )
    return {"calendar_id": "test-calendar-v1", "sessions": sessions}


def _observation(session: dict[str, str], *, all_drills: bool = False) -> dict[str, object]:
    opens_at = datetime.fromisoformat(session["opens_at"])
    closes_at = datetime.fromisoformat(session["closes_at"])
    heartbeat_times = []
    cursor = opens_at - timedelta(minutes=1)
    while cursor < closes_at:
        heartbeat_times.append(cursor.isoformat())
        cursor += timedelta(minutes=4)
    heartbeat_times.append((closes_at + timedelta(minutes=1)).isoformat())
    client_order_id = f"vt_soak_{session['trading_day'].replace('-', '')}"
    artifact = "a" * 64
    drills = {}
    if all_drills:
        drills = {
            name: {"passed": True, "artifact_sha256": artifact}
            for name in paper_soak.REQUIRED_DRILLS
        }
    return {
        "profile_id": PROFILE_ID,
        "symbol": "AAPL",
        "heartbeat_times": heartbeat_times,
        "connection_history": [
            {"at": timestamp, "status": "ok"} for timestamp in heartbeat_times
        ],
        "baseline_observed_at": (opens_at - timedelta(minutes=2)).isoformat(),
        "opening_equity_usd": 100_000.0,
        "connection": {
            "status": "ok",
            "profile_id": PROFILE_ID,
            "environment": "paper",
            "is_paper": True,
        },
        "account": {
            "status": "ok",
            "profile_id": PROFILE_ID,
            "environment": "paper",
            "is_paper": True,
            "account": {
                "account_number": ACCOUNT_REF,
                "equity": "100000",
                "currency": "USD",
            },
        },
        "open_orders": {
            "status": "ok",
            "profile_id": PROFILE_ID,
            "environment": "paper",
            "is_paper": True,
            "open_orders": [],
        },
        "quote": {
            "status": "ok",
            "profile_id": PROFILE_ID,
            "environment": "paper",
            "symbol": "AAPL",
            "quote": {
                "bid": 100.0,
                "ask": 100.1,
                "time": (closes_at - timedelta(seconds=1)).isoformat(),
                "currency": "USD",
            },
        },
        "order_probe": {
            "client_order_id": client_order_id,
            "initial": {
                "status": "ok",
                "order_id": f"broker-{session['trading_day']}",
                "client_order_id": client_order_id,
            },
            "replay": {
                "status": "ok",
                "order_id": f"broker-{session['trading_day']}",
                "client_order_id": client_order_id,
                "idempotency_replayed": True,
            },
        },
        "drills": drills,
    }


def _start(now: datetime | None = None):
    calendar = _calendar()
    if now is None:
        now = datetime.fromisoformat(calendar["sessions"][0]["opens_at"]) - timedelta(days=1)
    status = start_paper_soak(
        PROFILE_ID,
        ACCOUNT_REF,
        BUILD,
        calendar,
        actor="operator:test",
        now=now,
    )
    return status, calendar


def test_start_creates_private_signed_campaign_and_paper_soak_registry(soak_root: Path) -> None:
    status, _ = _start()

    assert status.state == QualificationState.PAPER_SOAK
    assert status.accepted_days == 0
    assert status.next_trading_day == "2026-08-03"
    campaign_dir = evidence_ledger_path(status.campaign_id).parent
    assert (campaign_dir / "manifest.json").stat().st_mode & 0o077 == 0
    key_path = soak_root / "live" / "qualification-signing-keys" / f"{status.campaign_id}.ed25519"
    assert key_path.stat().st_mode & 0o077 == 0
    assert not (campaign_dir / ".signing-key").exists()
    registry = json.loads(
        (soak_root / "live" / "qualification-registry.json").read_text(encoding="utf-8")
    )
    assert registry["records"][0]["state"] == "paper_soak"
    assert registry["records"][0]["paper_soak"]["observed_trading_days"] == []


@pytest.mark.parametrize(
    ("profile", "message"),
    [
        (
            SimpleNamespace(
                id="alpaca-live-trade", connector="alpaca", environment="live",
                transport="broker_sdk", capabilities=("orders.place.requires_mandate",), readonly=False,
            ),
            "paper",
        ),
        (
            SimpleNamespace(
                id="alpaca-paper-sdk", connector="alpaca", environment="paper",
                transport="broker_sdk", capabilities=("account.read",), readonly=True,
            ),
            "orders.place",
        ),
    ],
)
def test_start_rejects_non_trading_paper_profiles(
    soak_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    profile: SimpleNamespace,
    message: str,
) -> None:
    monkeypatch.setattr(paper_soak, "profile_by_id", lambda profile_id: profile)
    with pytest.raises(PaperSoakError, match=message):
        start_paper_soak(
            profile.id,
            ACCOUNT_REF,
            BUILD,
            _calendar(),
            actor="operator:test",
            now=datetime(2026, 8, 1, tzinfo=UTC),
        )
    assert not (soak_root / "live" / "qualification-evidence").exists()


def test_start_rejects_transport_build_and_late_start(
    soak_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    remote_profile = SimpleNamespace(
        id="remote-paper",
        connector="alpaca",
        environment="paper",
        transport="remote_mcp",
        capabilities=("orders.place",),
        readonly=False,
    )
    monkeypatch.setattr(paper_soak, "profile_by_id", lambda profile_id: remote_profile)
    with pytest.raises(PaperSoakError, match="broker_sdk"):
        start_paper_soak(
            remote_profile.id,
            ACCOUNT_REF,
            BUILD,
            _calendar(),
            actor="operator:test",
            now=datetime(2026, 8, 1, tzinfo=UTC),
        )

    valid_profile = SimpleNamespace(
        id=PROFILE_ID,
        connector="alpaca",
        environment="paper",
        transport="broker_sdk",
        capabilities=("orders.place",),
        readonly=False,
    )
    monkeypatch.setattr(paper_soak, "profile_by_id", lambda profile_id: valid_profile)
    with pytest.raises(PaperSoakError, match="40 lowercase hex"):
        start_paper_soak(
            PROFILE_ID,
            ACCOUNT_REF,
            "mutable-build",
            _calendar(),
            actor="operator:test",
            now=datetime(2026, 8, 1, tzinfo=UTC),
        )
    with pytest.raises(PaperSoakError, match="after its first"):
        start_paper_soak(
            PROFILE_ID,
            ACCOUNT_REF,
            BUILD,
            _calendar(),
            actor="operator:test",
            now=datetime(2026, 8, 3, 15, tzinfo=UTC),
        )
    assert not (soak_root / "live" / "qualification-evidence").exists()


def test_start_rejects_symlinked_evidence_directory(soak_root: Path) -> None:
    live_dir = soak_root / "live"
    live_dir.mkdir()
    redirected = soak_root / "redirected-evidence"
    redirected.mkdir()
    (live_dir / "qualification-evidence").symlink_to(
        redirected, target_is_directory=True
    )

    with pytest.raises(PaperSoakError, match="private directory"):
        _start()

    assert list(redirected.iterdir()) == []


def test_valid_day_is_signed_and_updates_trusted_progress(soak_root: Path) -> None:
    status, calendar = _start()
    session = calendar["sessions"][0]
    result = record_paper_soak_day(
        status.campaign_id,
        _observation(session),
        now=datetime.fromisoformat(session["closes_at"]) + timedelta(minutes=5),
    )

    assert result.last_day_accepted is True
    assert result.accepted_days == 1
    assert result.next_trading_day == "2026-08-04"
    reloaded = load_paper_soak_status(status.campaign_id)
    assert reloaded.accepted_days == 1
    ledger = evidence_ledger_path(status.campaign_id)
    record = json.loads(ledger.read_text(encoding="utf-8"))
    assert record["accepted"] is True
    assert len(record["signature"]) == 128
    assert "account" not in record["artifacts"]
    registry = json.loads(
        (soak_root / "live" / "qualification-registry.json").read_text(encoding="utf-8")
    )
    assert registry["records"][0]["paper_soak"]["observed_trading_days"] == ["2026-08-03"]


def test_daily_record_window_forbids_early_and_late_backfill(soak_root: Path) -> None:
    status, calendar = _start()
    session = calendar["sessions"][0]
    close = datetime.fromisoformat(session["closes_at"])
    with pytest.raises(PaperSoakError, match="before session close"):
        record_paper_soak_day(
            status.campaign_id, _observation(session), now=close - timedelta(seconds=1)
        )
    with pytest.raises(PaperSoakError, match="backfilling"):
        record_paper_soak_day(
            status.campaign_id,
            _observation(session),
            now=close + timedelta(hours=12, seconds=1),
        )


def test_campaign_lock_refuses_a_symlink(soak_root: Path) -> None:
    status, calendar = _start()
    target = soak_root / "lock-target"
    target.write_text("do-not-touch", encoding="utf-8")
    lock_path = evidence_ledger_path(status.campaign_id).parent / ".campaign.lock"
    lock_path.symlink_to(target)

    with pytest.raises(PaperSoakError, match="lock cannot be opened safely"):
        record_paper_soak_day(
            status.campaign_id,
            _observation(calendar["sessions"][0]),
            now=datetime.fromisoformat(calendar["sessions"][0]["closes_at"])
            + timedelta(minutes=1),
        )

    assert target.read_text(encoding="utf-8") == "do-not-touch"


def test_connector_collector_uses_real_read_surface_and_shared_paper_order_path(
    soak_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    status, calendar = _start()
    session = calendar["sessions"][0]
    proof = _observation(session, all_drills=True)
    calls: list[tuple[str, str]] = []

    monkeypatch.setattr(
        "src.trading.service.check_connection",
        lambda profile_id: proof["connection"],
    )
    monkeypatch.setattr(
        "src.trading.service.get_account",
        lambda profile_id: proof["account"],
    )
    monkeypatch.setattr(
        "src.trading.service.get_open_orders",
        lambda profile_id, include_executions=False: proof["open_orders"],
    )
    monkeypatch.setattr(
        "src.trading.service.get_quote",
        lambda symbol, profile_id: proof["quote"],
    )

    def place_order(symbol: str, profile_id: str, **kwargs: object) -> dict[str, object]:
        calls.append((symbol, profile_id))
        return {
            "status": "ok",
            "order_id": "paper-probe-1",
            "client_order_id": kwargs["client_order_id"],
            **({"idempotency_replayed": True} if len(calls) == 2 else {}),
        }

    monkeypatch.setattr("src.trading.service.place_order", place_order)
    result = collect_paper_soak_day(
        status.campaign_id,
        {
            "heartbeat_times": proof["heartbeat_times"],
            "connection_history": proof["connection_history"],
            "baseline_observed_at": proof["baseline_observed_at"],
            "opening_equity_usd": proof["opening_equity_usd"],
            "drills": proof["drills"],
        },
        symbol="AAPL",
        probe_order={
            "side": "buy",
            "quantity": 1,
            "order_type": "market",
            "client_order_id": "vt_soak_collect_0001",
        },
        now=datetime.fromisoformat(session["closes_at"]) + timedelta(minutes=1),
    )

    assert result.last_day_accepted is True
    assert calls == [("AAPL", PROFILE_ID), ("AAPL", PROFILE_ID)]


def test_session_runner_covers_full_window_and_records_without_network(
    soak_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    status, calendar = _start()
    session = calendar["sessions"][0]
    proof = _observation(session, all_drills=True)
    current = datetime.fromisoformat(session["opens_at"]) - timedelta(minutes=1)

    def now_fn() -> datetime:
        return current

    def sleep_fn(seconds: float) -> None:
        nonlocal current
        current += timedelta(seconds=seconds)

    monkeypatch.setattr(
        "src.trading.service.check_connection",
        lambda profile_id: proof["connection"],
    )
    monkeypatch.setattr(
        "src.trading.service.get_account",
        lambda profile_id: proof["account"],
    )
    monkeypatch.setattr(
        "src.trading.service.get_open_orders",
        lambda profile_id, include_executions=False: proof["open_orders"],
    )
    monkeypatch.setattr(
        "src.trading.service.get_quote",
        lambda symbol, profile_id: proof["quote"],
    )
    calls = 0

    def place_order(symbol: str, profile_id: str, **kwargs: object) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {
            "status": "ok",
            "order_id": "paper-runner-probe",
            "client_order_id": kwargs["client_order_id"],
            **({"idempotency_replayed": True} if calls == 2 else {}),
        }

    monkeypatch.setattr("src.trading.service.place_order", place_order)
    result = run_paper_soak_session(
        status.campaign_id,
        symbol="AAPL",
        probe_order={
            "side": "buy",
            "quantity": 1,
            "order_type": "market",
            "client_order_id": "vt_soak_runner_0001",
        },
        drills=proof["drills"],
        poll_seconds=240,
        now_fn=now_fn,
        sleep_fn=sleep_fn,
    )

    assert result.last_day_accepted is True
    assert current >= datetime.fromisoformat(session["closes_at"])


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (
            lambda obs: obs["account"]["account"].update(account_number="other"),
            "account_ref_mismatch",
        ),
        (
            lambda obs: obs["quote"]["quote"].update(time="2026-08-03T19:00:00+00:00"),
            "stale_quote",
        ),
        (
            lambda obs: obs["open_orders"].update(
                open_orders=[{"symbol": "AAPL", "quantity": 1, "limit_price": 100}]
            ),
            "open_order_client_id_missing",
        ),
        (
            lambda obs: obs["order_probe"]["replay"].update(idempotency_replayed=False),
            "idempotency_replay_failed",
        ),
    ],
)
def test_bad_daily_evidence_is_recorded_but_never_counted(
    soak_root: Path,
    mutate,
    code: str,
) -> None:
    status, calendar = _start()
    session = calendar["sessions"][0]
    observation = _observation(session)
    mutate(observation)

    result = record_paper_soak_day(
        status.campaign_id,
        observation,
        now=datetime.fromisoformat(session["closes_at"]) + timedelta(minutes=5),
    )

    assert result.last_day_accepted is False
    assert result.accepted_days == 0
    assert code in result.failure_codes


def test_rejected_day_breaks_consecutive_streak(soak_root: Path) -> None:
    status, calendar = _start()
    first, second, third = calendar["sessions"][:3]
    for session in (first,):
        record_paper_soak_day(
            status.campaign_id,
            _observation(session, all_drills=True),
            now=datetime.fromisoformat(session["closes_at"]) + timedelta(minutes=1),
        )
    rejected = _observation(second)
    rejected["connection"]["status"] = "error"
    record_paper_soak_day(
        status.campaign_id,
        rejected,
        now=datetime.fromisoformat(second["closes_at"]) + timedelta(minutes=1),
    )
    result = record_paper_soak_day(
        status.campaign_id,
        _observation(third),
        now=datetime.fromisoformat(third["closes_at"]) + timedelta(minutes=1),
    )

    assert result.accepted_days == 1
    assert result.observed_trading_days == ("2026-08-05",)
    assert result.missing_drills == paper_soak.REQUIRED_DRILLS


def test_tampering_or_skipping_the_next_session_fails_closed(soak_root: Path) -> None:
    status, calendar = _start()
    first = calendar["sessions"][0]
    record_paper_soak_day(
        status.campaign_id,
        _observation(first),
        now=datetime.fromisoformat(first["closes_at"]) + timedelta(minutes=1),
    )
    ledger = evidence_ledger_path(status.campaign_id)
    row = json.loads(ledger.read_text(encoding="utf-8"))
    row["accepted"] = False
    ledger.write_text(json.dumps(row) + "\n", encoding="utf-8")

    with pytest.raises(PaperSoakError, match="signature|integrity"):
        load_paper_soak_status(status.campaign_id)


def test_exported_public_evidence_verifies_without_private_signing_key(
    soak_root: Path,
) -> None:
    status, calendar = _start()
    first = calendar["sessions"][0]
    record_paper_soak_day(
        status.campaign_id,
        _observation(first),
        now=datetime.fromisoformat(first["closes_at"]) + timedelta(minutes=1),
    )
    key_path = (
        soak_root
        / "live"
        / "qualification-signing-keys"
        / f"{status.campaign_id}.ed25519"
    )
    key_path.unlink()

    assert load_paper_soak_status(status.campaign_id).accepted_days == 1
    with pytest.raises(PaperSoakError, match="private|inspected"):
        record_paper_soak_day(
            status.campaign_id,
            _observation(calendar["sessions"][1]),
            now=datetime.fromisoformat(calendar["sessions"][1]["closes_at"])
            + timedelta(minutes=1),
        )


def test_promotion_requires_30_consecutive_days_and_every_drill(soak_root: Path) -> None:
    status, calendar = _start()
    for index, session in enumerate(calendar["sessions"]):
        record_paper_soak_day(
            status.campaign_id,
            _observation(session, all_drills=index == 0),
            now=datetime.fromisoformat(session["closes_at"]) + timedelta(minutes=1),
        )

    promoted = promote_paper_soak(
        status.campaign_id,
        actor="release-gate:test",
        now=datetime.fromisoformat(calendar["sessions"][-1]["closes_at"]) + timedelta(minutes=2),
    )

    assert promoted.state == QualificationState.PILOT_ELIGIBLE
    assert promoted.accepted_days == 30
    assert promoted.eligible is True
    decision = evaluate_live_qualification(
        "alpaca",
        ACCOUNT_REF,
        environ={LIVE_BROKER_ENV: "alpaca", BUILD_REVISION_ENV: BUILD},
        now=datetime.fromisoformat(calendar["sessions"][-1]["closes_at"]) + timedelta(days=1),
    )
    assert decision.allowed is False
    assert decision.state == QualificationState.PILOT_ELIGIBLE
    assert decision.code == "qualification_state_not_active"
    assert decision.observed_trading_days == 30


def test_promotion_refuses_29_days_or_missing_drills(soak_root: Path) -> None:
    status, calendar = _start()
    for session in calendar["sessions"][:29]:
        record_paper_soak_day(
            status.campaign_id,
            _observation(session),
            now=datetime.fromisoformat(session["closes_at"]) + timedelta(minutes=1),
        )

    with pytest.raises(PaperSoakError, match="30 consecutive"):
        promote_paper_soak(
            status.campaign_id,
            actor="release-gate:test",
            now=datetime.fromisoformat(calendar["sessions"][28]["closes_at"]) + timedelta(minutes=2),
        )


def test_campaign_is_bound_to_exact_account_build_and_policy(soak_root: Path) -> None:
    status, calendar = _start()
    first = calendar["sessions"][0]
    record_paper_soak_day(
        status.campaign_id,
        _observation(first),
        now=datetime.fromisoformat(first["closes_at"]) + timedelta(minutes=1),
    )
    registry_path = soak_root / "live" / "qualification-registry.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    registry["records"][0]["account_ref"] = "other-account"
    registry_path.write_text(json.dumps(registry), encoding="utf-8")

    decision = evaluate_live_qualification(
        "alpaca",
        "other-account",
        environ={LIVE_BROKER_ENV: "alpaca", BUILD_REVISION_ENV: BUILD},
        now=datetime(2026, 8, 5, tzinfo=UTC),
    )
    assert decision.allowed is False
    assert decision.code == "qualification_invalid"


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ([], "must be an object"),
        ({"side": "buy", "quantity": 1, "client_order_id": "valid-id", "extra": 1}, "unknown fields"),
        ({"side": "hold", "quantity": 1, "client_order_id": "valid-id"}, "buy or sell"),
        ({"side": "buy", "client_order_id": "valid-id"}, "exactly one"),
        ({"side": "buy", "quantity": 1, "notional": 10, "client_order_id": "valid-id"}, "exactly one"),
        ({"side": "buy", "quantity": 1, "order_type": "stop", "client_order_id": "valid-id"}, "market or limit"),
        ({"side": "buy", "quantity": 1, "order_type": "limit", "client_order_id": "valid-id"}, "limit_price"),
        ({"side": "buy", "quantity": 1, "client_order_id": "short"}, "at least 8"),
    ],
)
def test_probe_order_schema_rejects_ambiguous_or_unsafe_inputs(
    payload: object, message: str
) -> None:
    with pytest.raises(PaperSoakError, match=message):
        paper_soak._parse_probe_order(payload)  # type: ignore[arg-type]


def test_probe_order_schema_accepts_a_bounded_limit_order() -> None:
    parsed = paper_soak._parse_probe_order(
        {
            "side": "sell",
            "notional": "25.5",
            "order_type": "limit",
            "limit_price": 101,
            "time_in_force": "gtc",
            "client_order_id": "vt_limit_probe_0001",
        }
    )
    assert parsed["side"] == "sell"
    assert parsed["notional"] == 25.5
    assert parsed["limit_price"] == 101.0


@pytest.mark.parametrize(
    "mode",
    [
        "not_object",
        "too_short",
        "row_not_object",
        "bad_day",
        "reverse_bounds",
        "too_long",
        "duplicate_day",
        "overlap",
    ],
)
def test_signed_calendar_rejects_ambiguous_session_boundaries(mode: str) -> None:
    calendar: object = copy.deepcopy(_calendar())
    if mode == "not_object":
        calendar = []
    elif mode == "too_short":
        calendar["sessions"] = calendar["sessions"][:29]  # type: ignore[index]
    elif mode == "row_not_object":
        calendar["sessions"][0] = "bad"  # type: ignore[index]
    elif mode == "bad_day":
        calendar["sessions"][0]["trading_day"] = "not-a-date"  # type: ignore[index]
    elif mode == "reverse_bounds":
        calendar["sessions"][0]["closes_at"] = calendar["sessions"][0]["opens_at"]  # type: ignore[index]
    elif mode == "too_long":
        opened = datetime.fromisoformat(calendar["sessions"][0]["opens_at"])  # type: ignore[index]
        calendar["sessions"][0]["closes_at"] = (opened + timedelta(hours=25)).isoformat()  # type: ignore[index]
    elif mode == "duplicate_day":
        calendar["sessions"][1]["trading_day"] = calendar["sessions"][0]["trading_day"]  # type: ignore[index]
    else:
        calendar["sessions"][1]["opens_at"] = calendar["sessions"][0]["closes_at"]  # type: ignore[index]

    with pytest.raises(PaperSoakError):
        paper_soak._parse_calendar(calendar)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda obs: obs.update(profile_id="other-paper"), "profile_mismatch"),
        (lambda obs: obs.update(heartbeat_times=obs["heartbeat_times"][1:-1]), "session_coverage_incomplete"),
        (
            lambda obs: obs.update(
                heartbeat_times=[obs["heartbeat_times"][0], obs["heartbeat_times"][-1]],
                connection_history=[
                    {"at": obs["heartbeat_times"][0], "status": "ok"},
                    {"at": obs["heartbeat_times"][-1], "status": "ok"},
                ],
            ),
            "heartbeat_gap",
        ),
        (lambda obs: obs.update(opening_equity_usd=0), "opening_equity_baseline_invalid"),
        (lambda obs: obs["connection"].update(is_paper=False, environment="live"), "paper_environment_unproven"),
        (lambda obs: obs["account"]["account"].update(equity="bad"), "account_equity_unavailable"),
        (lambda obs: obs["open_orders"].update(open_orders=["bad"]), "open_orders_invalid"),
        (
            lambda obs: obs["quote"]["quote"].update(time="2026-08-03T20:00:10+00:00"),
            "quote_clock_drift",
        ),
        (lambda obs: obs.update(connection_history=[]), "connection_history_invalid"),
        (
            lambda obs: obs["connection_history"][-1].update(status="error"),
            "disconnect_unrecovered",
        ),
        (
            lambda obs: obs["connection_history"][1].update(status="error"),
            "disconnect_recovery_unproven",
        ),
        (lambda obs: obs.update(order_probe={}), "client_order_id_probe_missing"),
        (
            lambda obs: obs["order_probe"]["initial"].update(client_order_id="wrong"),
            "client_order_id_echo_failed",
        ),
        (
            lambda obs: obs.update(drills={"unexpected": {"passed": True}}),
            "unknown_drill",
        ),
        (
            lambda obs: obs.update(
                drills={
                    "restart_recovery": {
                        "passed": False,
                        "artifact_sha256": "bad",
                    }
                }
            ),
            "drill_restart_recovery_failed",
        ),
    ],
)
def test_daily_certification_rejects_untrusted_evidence_edges(
    soak_root: Path, mutate, code: str
) -> None:
    status, calendar = _start()
    manifest, _, _, _ = paper_soak._load_campaign(status.campaign_id)
    session = calendar["sessions"][0]
    observation = _observation(session)
    mutate(observation)

    failures, _, _ = paper_soak._validate_observation(
        manifest, session, observation
    )

    assert code in failures


@pytest.mark.parametrize(
    ("heartbeats", "code"),
    [
        (None, "heartbeat_evidence_missing"),
        (["bad", "2026-08-03T14:01:00+00:00"], "heartbeat_timestamp_invalid"),
        (
            ["2026-08-03T14:01:00+00:00", "2026-08-03T14:01:00+00:00"],
            "heartbeat_order_invalid",
        ),
    ],
)
def test_heartbeat_parser_is_strictly_ordered(heartbeats: object, code: str) -> None:
    failures: list[str] = []
    assert paper_soak._parse_heartbeats(heartbeats, failures) == []
    assert failures == [code]


def test_identity_and_scalar_parsers_fail_closed() -> None:
    assert not paper_soak._paper_identity_proven(
        {"profile_id": "other", "is_paper": True}, PROFILE_ID
    )
    assert paper_soak._paper_identity_proven(
        {"profile_id": PROFILE_ID, "paper_guard": "enabled"}, PROFILE_ID
    )
    assert paper_soak._extract_account_ref({"account_id": 42}) == "42"
    assert paper_soak._extract_account_ref({"account": "nested-account"}) == "nested-account"
    assert paper_soak._positive_float("bad") is None
    assert paper_soak._positive_float(float("nan")) is None
    assert paper_soak._positive_float(0) is None
    assert paper_soak._positive_float("1.5") == 1.5
    with pytest.raises(PaperSoakError, match="timezone-aware"):
        paper_soak._utc_now(datetime(2026, 8, 1))
    with pytest.raises(PaperSoakError, match="timezone"):
        paper_soak._parse_datetime("2026-08-01T00:00:00", "at")
    with pytest.raises(PaperSoakError, match="ISO-8601"):
        paper_soak._parse_datetime("bad", "at")
    with pytest.raises(PaperSoakError, match="actor"):
        paper_soak._actor("bad actor")
    with pytest.raises(PaperSoakError, match="invalid"):
        paper_soak._bounded_text("bad\nvalue", "value", 20)

    assert paper_soak._safe_connector_call(lambda: "bad", "probe")["status"] == "error"
    assert paper_soak._safe_connector_call(
        lambda: (_ for _ in ()).throw(RuntimeError("offline")), "probe"
    )["status"] == "error"
    with pytest.raises(PaperSoakError, match="evidence reference"):
        paper_soak.verify_paper_soak_evidence_ref(
            "bad-ref",
            broker="alpaca",
            account_ref_sha256="0" * 64,
            build_revision=BUILD,
            policy_version="node12a-live-qualification-v1",
        )
    with pytest.raises(PaperSoakError, match="campaign_id"):
        paper_soak._campaign_dir("../escape")
    with pytest.raises(PaperSoakError, match="campaign_id"):
        paper_soak._private_key_path("../escape")
    with pytest.raises(PaperSoakError, match="signature"):
        paper_soak._evidence_ref("0" * 24, "bad")
