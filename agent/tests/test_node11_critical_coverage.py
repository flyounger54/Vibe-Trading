"""Edge-case coverage for the security and live-order release gates.

These tests exercise fail-closed behavior and compatibility fallbacks that are
hard to reach through the HTTP and broker integration suites.  They deliberately
avoid network, broker SDK, and user-runtime writes.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from src.live import order_guard, sdk_order_gate
from src.live.enforcement import OrderIntent
from src.live.mandate.model import AssetClass, InstrumentType
from src.security import api_security


pytestmark = pytest.mark.unit


def _intent(
    *,
    quantity: float | None = None,
    notional: float | None = 100.0,
    asset_class: AssetClass | None = AssetClass.US_EQUITY,
) -> OrderIntent:
    return OrderIntent(
        symbol="AAPL",
        side="buy",
        notional_usd=notional,
        quantity=quantity,
        instrument_type=InstrumentType.EQUITY,
        asset_class=asset_class,
    )


class _FakePath:
    def __init__(self, *, text: str = "", exists: bool = False, chmod_error: bool = False) -> None:
        self.text = text
        self._exists = exists
        self.chmod_error = chmod_error
        self.parent = self
        self.mkdir_calls: list[tuple] = []
        self.chmod_calls: list[int] = []

    def __str__(self) -> str:
        return "/fake/api.key"

    def exists(self) -> bool:
        return self._exists

    def read_text(self, *, encoding: str) -> str:
        assert encoding == "utf-8"
        return self.text

    def mkdir(self, *args, **kwargs) -> None:
        self.mkdir_calls.append((args, kwargs))

    def chmod(self, mode: int) -> None:
        self.chmod_calls.append(mode)
        if self.chmod_error:
            raise OSError("chmod unavailable")


def test_api_key_permission_errors_remain_non_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    directory = _FakePath(chmod_error=True)
    api_security._ensure_private_parent(directory)  # type: ignore[arg-type]
    assert directory.mkdir_calls

    key_file = _FakePath(text="x" * 43, chmod_error=True)
    assert api_security._read_key(key_file) == "x" * 43  # type: ignore[arg-type]

    too_short = _FakePath(text="short")
    with pytest.raises(RuntimeError, match="too short"):
        api_security._read_key(too_short)  # type: ignore[arg-type]

    monkeypatch.setenv("API_AUTH_KEY", " configured-secret ")
    assert api_security.get_or_create_api_key() == "configured-secret"


def test_api_key_creation_race_and_final_chmod_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("API_AUTH_KEY", raising=False)
    raced = _FakePath(exists=False)
    monkeypatch.setattr(api_security, "api_key_path", lambda: raced)
    monkeypatch.setattr(api_security, "_ensure_private_parent", lambda path: None)
    monkeypatch.setattr(api_security.os, "open", lambda *args: (_ for _ in ()).throw(FileExistsError()))
    monkeypatch.setattr(api_security, "_read_key", lambda path: "r" * 43)
    assert api_security.get_or_create_api_key() == "r" * 43

    created = _FakePath(exists=False, chmod_error=True)
    writes: list[bytes] = []
    monkeypatch.setattr(api_security, "api_key_path", lambda: created)
    monkeypatch.setattr(api_security.secrets, "token_urlsafe", lambda size: "n" * 43)
    monkeypatch.setattr(api_security.os, "open", lambda *args: 17)
    monkeypatch.setattr(api_security.os, "write", lambda fd, data: writes.append(data) or len(data))
    monkeypatch.setattr(api_security.os, "fsync", lambda fd: None)
    monkeypatch.setattr(api_security.os, "close", lambda fd: None)
    assert api_security.get_or_create_api_key() == "n" * 43
    assert writes == [b"nnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnn\n"]


def test_redaction_filter_handles_mapping_tuple_and_string_args() -> None:
    redactor = api_security.SecretRedactionFilter()

    mapping_record = logging.LogRecord("x", logging.INFO, __file__, 1, "Bearer one", (), None)
    mapping_record.args = {"secret": "api_key=two", "count": 2}
    assert redactor.filter(mapping_record)
    assert "one" not in mapping_record.msg
    assert mapping_record.args == {"secret": "api_key=[redacted]", "count": 2}

    tuple_record = logging.LogRecord("x", logging.INFO, __file__, 1, "%s %s", (), None)
    tuple_record.args = ("Bearer three", 4)
    redactor.filter(tuple_record)
    assert tuple_record.args == ("Bearer [redacted]", 4)

    string_record = logging.LogRecord("x", logging.INFO, __file__, 1, "%s", (), None)
    string_record.args = "Bearer four"
    redactor.filter(string_record)
    assert string_record.args == "Bearer [redacted]"


def test_filter_installation_is_idempotent() -> None:
    loggers = [logging.getLogger(name) for name in ("uvicorn.access", "uvicorn.error", "api_server")]
    before = [sum(isinstance(item, api_security.SecretRedactionFilter) for item in logger.filters) for logger in loggers]
    api_security.install_secret_redaction_filters()
    api_security.install_secret_redaction_filters()
    after = [sum(isinstance(item, api_security.SecretRedactionFilter) for item in logger.filters) for logger in loggers]
    assert all(count == 1 for count in after)
    assert all(new >= old for old, new in zip(before, after, strict=True))


def test_idempotency_registry_rejects_expires_evicts_and_releases(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = api_security.IdempotencyRegistry()
    with pytest.raises(ValueError, match="Invalid"):
        registry.claim("short")

    moments = iter((1000.0, 2000.0, 3000.0))
    monkeypatch.setattr(api_security.time, "monotonic", lambda: next(moments))
    assert registry.claim("valid-key-0001", ttl_seconds=10)
    assert registry.claim("valid-key-0002", ttl_seconds=10)
    assert "valid-key-0001" not in registry._claimed

    registry._claimed = {f"key-{index:05d}": float(index) for index in range(10_000)}
    assert registry.claim("capacity-key", ttl_seconds=10_000)
    assert "key-00000" not in registry._claimed
    registry.release("capacity-key")
    assert "capacity-key" not in registry._claimed


@pytest.mark.parametrize(
    ("quote", "expected"),
    [
        ({"quote": {"last": "bad", "ask": "12.5"}, "status": "ok"}, 12.5),
        ({"quote": {"last": float("nan"), "ask": -1}, "status": "ok"}, None),
        ({"quote": [], "status": "ok"}, None),
        ({"status": "error"}, None),
        ("not-a-mapping", None),
    ],
)
def test_sdk_connector_quote_parsing_edges(quote, expected) -> None:
    connector = SimpleNamespace(get_quote=lambda symbol, *, config: quote)
    assert sdk_order_gate._connector_quote_price(connector, object(), "AAPL") == expected


def test_sdk_quote_and_read_failures_are_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    assert sdk_order_gate._connector_quote_price(SimpleNamespace(), object(), "AAPL") is None
    raising_quote = SimpleNamespace(get_quote=lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("quote")))
    assert sdk_order_gate._connector_quote_price(raising_quote, object(), "AAPL") is None

    no_asset = _intent(quantity=2, notional=None, asset_class=None)
    monkeypatch.setattr(sdk_order_gate, "instrument_asset_class", lambda value: None)
    assert sdk_order_gate._quote_price(no_asset, SimpleNamespace(), object()) is None

    monkeypatch.setattr(sdk_order_gate, "instrument_asset_class", lambda value: AssetClass.US_EQUITY)
    monkeypatch.setattr(sdk_order_gate, "last_price_usd", lambda *args: (_ for _ in ()).throw(RuntimeError("loader")))
    assert sdk_order_gate._quote_price(no_asset, SimpleNamespace(), object()) is None

    assert sdk_order_gate._safe_read(SimpleNamespace(), "missing", object()) is None
    raising_read = SimpleNamespace(read=lambda config: (_ for _ in ()).throw(RuntimeError("read")))
    assert sdk_order_gate._safe_read(raising_read, "read", object()) is None
    error_read = SimpleNamespace(read=lambda config: {"status": "error"})
    assert sdk_order_gate._safe_read(error_read, "read", object()) is None


def test_sdk_normalization_rejects_nan_and_nonpositive_prices(monkeypatch: pytest.MonkeyPatch) -> None:
    quantity_order = _intent(quantity=2, notional=1)
    monkeypatch.setattr(sdk_order_gate, "_quote_price", lambda *args: float("nan"))
    assert sdk_order_gate._normalize_notional(quantity_order, object(), object()) is None
    monkeypatch.setattr(sdk_order_gate, "_quote_price", lambda *args: -1.0)
    assert sdk_order_gate._normalize_notional(quantity_order, object(), object()) is None


def test_sdk_audit_fallback_failure_and_expiry_edges(monkeypatch: pytest.MonkeyPatch) -> None:
    mandate = SimpleNamespace(consent=SimpleNamespace(consent_token_sha256="hash", account_ref="account"))
    calls: list[tuple] = []

    def positional_only(event, *args, **kwargs):
        if kwargs:
            raise TypeError("old signature")
        calls.append((event,))
        return {"audited": True}

    monkeypatch.setattr(sdk_order_gate, "write_live_action", positional_only)
    record = sdk_order_gate._audit(
        "alpaca",
        "session",
        kind="order_rejected",
        outcome="blocked",
        mandate=mandate,
        intent=_intent(),
        broker_request=None,
        broker_response=None,
        gate_decision={"allowed": False},
        error="blocked",
    )
    assert record == {"audited": True}
    assert len(calls) == 1

    monkeypatch.setattr(sdk_order_gate, "LiveActionEvent", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("audit")))
    assert sdk_order_gate._audit(
        "alpaca", "session", kind="breach", outcome="blocked", mandate=None, intent=None,
        broker_request=None, broker_response=None, gate_decision={"allowed": False},
    ) is None

    assert sdk_order_gate._is_expired(SimpleNamespace(consent=SimpleNamespace(expires_at="invalid")))
    future_naive = (datetime.now() + timedelta(days=1)).isoformat()
    assert not sdk_order_gate._is_expired(SimpleNamespace(consent=SimpleNamespace(expires_at=future_naive)))


def test_sdk_result_helpers_cover_absent_records_breaches_and_messages() -> None:
    refusal = sdk_order_gate._refusal(
        "alpaca", decision="deny", reason="blocked", reauth=False, record=None,
    )
    assert "live_action" not in refusal

    assert sdk_order_gate._error_message({"message": "rejected"}) == "rejected"
    assert sdk_order_gate._error_message({"detail": "detail"}) == "detail"
    assert sdk_order_gate._error_message({"error": 123}) == "broker order returned an error"


@pytest.mark.parametrize(
    ("payload", "symbol", "expected"),
    [
        (None, "AAPL", None),
        ({"AAPL": {"ask": "10.5"}}, "AAPL", 10.5),
        ({"AAPL": {"ask": "bad"}}, "AAPL", None),
        ({"quotes": [{"symbol": "MSFT", "last": 2}, {"ticker": "AAPL", "last": 11}]}, "AAPL", 11.0),
        ({"data": [{"instrument": "AAPL", "price": 12}]}, "AAPL", 12.0),
        ({"results": [{"last": 13}]}, "AAPL", 13.0),
        ({"results": [{"symbol": "MSFT", "last": 13}, {"symbol": "TSLA", "last": 14}]}, "AAPL", None),
    ],
)
def test_mcp_quote_envelope_parsing(payload, symbol, expected) -> None:
    assert order_guard._parse_quote_price(payload, symbol) == expected


def test_mcp_price_parser_skips_bad_nan_and_negative_values() -> None:
    assert order_guard._price_from_quote_dict({"price": "bad", "ask": "9.5"}) == 9.5
    assert order_guard._price_from_quote_dict({"price": float("nan"), "ask": -1}) is None
    assert order_guard._match_quote_row([None, {"ticker": " aapl ", "last": 8}], "AAPL") == {
        "ticker": " aapl ", "last": 8,
    }


def test_mcp_guard_serialization_error_and_audit_compatibility(monkeypatch: pytest.MonkeyPatch) -> None:
    assert order_guard.LiveOrderGuardTool._safe_json("not-json") == {"raw": "not-json"}
    assert order_guard.LiveOrderGuardTool._safe_json("[1]") == {"raw": [1]}
    assert order_guard.LiveOrderGuardTool._is_error_envelope(None)
    assert not order_guard.LiveOrderGuardTool._is_error_envelope({"status": "ok"})
    assert order_guard.LiveOrderGuardTool._error_message({"message": "no funds"}) == "no funds"
    assert order_guard.LiveOrderGuardTool._error_message({"detail": "denied"}) == "denied"
    assert order_guard.LiveOrderGuardTool._error_message({"error": 1}) == "broker forward returned an error"
    assert order_guard.LiveOrderGuardTool._embed_live_action("{}", None) == "{}"
    assert order_guard.LiveOrderGuardTool._embed_live_action("bad", {"id": 1}) == "bad"
    assert order_guard.LiveOrderGuardTool._embed_live_action("[]", {"id": 1}) == "[]"
    assert json.loads(order_guard.LiveOrderGuardTool._embed_live_action("{}", {"id": 1}))["live_action"] == {"id": 1}

    calls: list[object] = []

    def positional_only(event, *args, **kwargs):
        if kwargs:
            raise TypeError("old signature")
        calls.append(event)
        return {"ok": True}

    monkeypatch.setattr(order_guard, "write_live_action", positional_only)
    assert order_guard._record_live_action(SimpleNamespace()) == {"ok": True}  # type: ignore[arg-type]
    assert len(calls) == 1


def test_mcp_guard_quote_and_audit_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    guard = object.__new__(order_guard.LiveOrderGuardTool)
    guard.broker = "robinhood"
    guard.session_id = "session"
    guard._spec = SimpleNamespace(remote_name="place_order")

    monkeypatch.setattr(guard, "_broker_quote_price", lambda symbol: None)
    monkeypatch.setattr(order_guard, "instrument_asset_class", lambda value: None)
    assert guard._quote_price(_intent(quantity=1, asset_class=None)) is None

    monkeypatch.setattr(order_guard, "instrument_asset_class", lambda value: AssetClass.US_EQUITY)
    monkeypatch.setattr(order_guard, "last_price_usd", lambda *args: (_ for _ in ()).throw(RuntimeError("loader")))
    assert guard._quote_price(_intent(quantity=1)) is None

    monkeypatch.setattr(order_guard, "LiveActionEvent", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("audit")))
    assert guard._audit(
        kind="order_rejected",
        outcome="blocked",
        mandate=None,
        intent=None,
        broker_request=None,
        broker_response=None,
        gate_decision={"allowed": False},
    ) is None

    invalid_expiry = SimpleNamespace(consent=SimpleNamespace(expires_at="invalid"))
    assert guard._is_expired(invalid_expiry)
    future_naive = (datetime.now() + timedelta(days=1)).isoformat()
    assert not guard._is_expired(SimpleNamespace(consent=SimpleNamespace(expires_at=future_naive)))


def test_mcp_broker_quote_continues_after_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    class Adapter:
        def call_tool(self, remote, arguments, *, local_name):
            if remote == "bad_quote":
                raise RuntimeError("unavailable")
            return {"status": "ok", "price": 15}

    guard = object.__new__(order_guard.LiveOrderGuardTool)
    guard._adapter = Adapter()
    monkeypatch.setattr(guard, "_read_tools", lambda operation, fallback: ("bad_quote", "good_quote"))
    assert guard._broker_quote_price("AAPL") == 15.0
