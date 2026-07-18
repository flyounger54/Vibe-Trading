"""Cross-connector configuration and defensive-adapter contract coverage."""

from __future__ import annotations

from dataclasses import fields
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from src.trading.connectors.alpaca import sdk as alpaca
from src.trading.connectors.binance import sdk as binance
from src.trading.connectors.futu import sdk as futu
from src.trading.connectors.longbridge import sdk as longbridge
from src.trading.connectors.okx import sdk as okx
from src.trading.connectors.tiger import sdk as tiger


pytestmark = pytest.mark.unit


CONNECTORS = (
    (alpaca, alpaca.AlpacaConfig, alpaca.AlpacaConfigError, alpaca.AlpacaDependencyError),
    (binance, binance.BinanceConfig, binance.BinanceConfigError, binance.BinanceDependencyError),
    (futu, futu.FutuConfig, futu.FutuConfigError, futu.FutuDependencyError),
    (longbridge, longbridge.LongbridgeConfig, longbridge.LongbridgeConfigError, longbridge.LongbridgeDependencyError),
    (okx, okx.OKXConfig, okx.OKXConfigError, okx.OKXDependencyError),
    (tiger, tiger.TigerConfig, tiger.TigerConfigError, tiger.TigerDependencyError),
)


@pytest.mark.parametrize(("module", "config_cls", "config_error", "dependency_error"), CONNECTORS)
def test_connector_config_validation_override_build_and_persistence(
    module, config_cls, config_error, dependency_error, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_loader = module.load_config
    with pytest.raises(config_error):
        config_cls.from_mapping({"profile": "invalid"})

    default = config_cls.from_mapping()
    live = default.with_overrides(profile="live-readonly")
    assert live.profile == "live-readonly"
    assert default.profile == "paper"

    monkeypatch.setattr(module, "load_config", lambda: default)
    built = module.build_config({"profile": "live-readonly", "ignored": None}, {"profile": "paper", "unknown": "x"})
    assert built.profile == "paper"
    assert module.build_config({}, {}).profile == "paper"

    path = tmp_path / f"{module.__name__.split('.')[-2]}.json"
    monkeypatch.setattr(module, "config_path", lambda: path)
    monkeypatch.setattr(module, "load_config", original_loader)
    assert module.load_config().profile == "paper"
    assert module.save_config(live) == path
    restored = module.load_config()
    assert restored.profile == "live-readonly"
    assert path.stat().st_mode & 0o777 == 0o600
    path.write_text("not-json", encoding="utf-8")
    with pytest.raises(config_error):
        module.load_config()

    require_name = next(name for name in dir(module) if name.startswith("_require_"))
    require = getattr(module, require_name)
    monkeypatch.setattr(module, require_name, lambda: SimpleNamespace())
    available_name = next(name for name in dir(module) if name.endswith("_available"))
    assert getattr(module, available_name)()
    monkeypatch.setattr(module, require_name, lambda: (_ for _ in ()).throw(dependency_error("missing")))
    assert not getattr(module, available_name)()
    monkeypatch.setattr(module, require_name, require)

    public = module._public_config(restored)
    assert public["profile"] == "live-readonly"
    for field in fields(restored):
        if "secret" in field.name and getattr(restored, field.name):
            assert getattr(restored, field.name) not in str(public)


def test_alpaca_defensive_conversion_and_timeframes() -> None:
    assert alpaca._as_iter(None) == []
    assert alpaca._as_iter((1, 2)) == [1, 2]
    assert alpaca._as_iter(1) == [1]
    assert alpaca._obj_get(None, "x", "fallback") == "fallback"
    assert alpaca._obj_get({"x": 1}, "x") == 1
    assert alpaca._obj_get(SimpleNamespace(x=2), "x") == 2
    row = SimpleNamespace(
        id="o1", symbol="AAPL", side="buy", order_type="market", qty="2",
        avg_entry_price="10", market_value="20", current_price="11", unrealized_pl="2",
        cost_basis="20", notional="20", filled_qty="2", filled_avg_price="10", limit_price=None,
        status="filled", submitted_at="now", timestamp="now", open=1, high=2, low=0.5, close=1.5, volume=10,
    )
    assert alpaca._position_to_dict(row)["symbol"] == "AAPL"
    assert alpaca._order_to_dict(row)["order_id"] == "o1"
    assert alpaca._bar_to_dict(row)["close"] == 1.5

    class Unit:
        Minute = "minute"
        Hour = "hour"
        Week = "week"
        Month = "month"

    class Frame:
        Day = "day"

        def __init__(self, amount, unit) -> None:
            self.amount = amount
            self.unit = unit

    assert alpaca._timeframe("5m", Frame, Unit).amount == 5
    assert alpaca._timeframe("4h", Frame, Unit).unit == "hour"
    assert alpaca._timeframe("1w", Frame, Unit).unit == "week"
    assert alpaca._timeframe("1M", Frame, Unit).unit == "month"
    assert alpaca._timeframe("1d", Frame, Unit) == "day"
    assert alpaca._missing_fields(alpaca.AlpacaConfig()) == ["api_key", "secret_key"]


def test_binance_symbol_host_balances_and_serializers(monkeypatch: pytest.MonkeyPatch) -> None:
    assert binance.normalize_symbol(" btc-usdt ") == "BTC/USDT"
    assert binance.normalize_symbol("ethusdc") == "ETH/USDC"
    assert binance.normalize_symbol("unknown") == "UNKNOWN"
    binance._assert_host(SimpleNamespace(is_testnet=True, host="https://test", testnet_host="https://test"))
    with pytest.raises(binance.BinanceConfigError):
        binance._assert_host(SimpleNamespace(is_testnet=True, host="https://live", testnet_host="https://test"))
    binance._assert_host(SimpleNamespace(is_testnet=False, host=binance.LIVE_HOST, testnet_host=""))
    with pytest.raises(binance.BinanceConfigError):
        binance._assert_host(SimpleNamespace(is_testnet=False, host="https://test", testnet_host=""))

    fake_ccxt = SimpleNamespace(ArgumentsRequired=ValueError, BadSymbol=KeyError, NotSupported=RuntimeError)
    monkeypatch.setattr(binance, "_require_ccxt", lambda: fake_ccxt)
    assert len(binance._symbol_required_errors()) == 3
    monkeypatch.setattr(binance, "_require_ccxt", lambda: SimpleNamespace())
    assert binance._symbol_required_errors() == (ValueError,)

    assert binance._as_iter(None) == [] and binance._as_iter("x") == ["x"]
    assert binance._obj_get({"x": 1}, "x") == 1
    assert binance._to_float("1.2") == 1.2 and binance._to_float("bad") is None
    balances = binance._nonzero_balances(
        {
            "BTC": {"free": 1, "used": 2, "total": 3},
            "ETH": {"free": 0, "used": 0, "total": 0},
            "free": {"BTC": 1},
            "total": {"BTC": 3},
        }
    )
    assert balances == [{"asset": "BTC", "free": 1.0, "used": 2.0, "total": 3.0}]
    assert binance._nonzero_balances(None) == []
    order = {"id": "o", "symbol": "BTC/USDT", "side": "buy", "type": "limit", "amount": 1, "price": 10, "status": "open"}
    assert binance._order_to_dict(order)["order_id"] == "o"
    assert binance._trade_to_dict(order)["symbol"] == "BTC/USDT"
    assert binance._ohlcv_to_dict([1, 2, 3, 4, 5, 6])["close"] == 5
    assert binance._ohlcv_to_dict([1])["close"] is None


def test_futu_gateway_accounts_unwrap_records_and_serializers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(futu, "tcp_port_open", lambda *args: False)
    with pytest.raises(futu.FutuConfigError):
        futu._assert_gateway(futu.FutuConfig())
    monkeypatch.setattr(futu, "tcp_port_open", lambda *args: True)
    futu._assert_gateway(futu.FutuConfig())
    fake_sdk = SimpleNamespace(RET_OK=0)
    monkeypatch.setattr(futu, "_require_futu", lambda: fake_sdk)
    assert futu._unwrap("raw") == "raw"
    assert futu._unwrap((1, "error")) is None
    assert futu._unwrap((0, {"ok": True})) == {"ok": True}
    frame = pd.DataFrame([{"acc_id": 1, "trd_env": "SIMULATE"}])
    assert futu._records(frame)[0]["acc_id"] == 1
    assert futu._records(None) == []
    assert futu._records({"x": 1}) == [{"x": 1}]
    assert futu._records([{"x": 1}, "raw"])[1] == {"value": "raw"}

    ctx = SimpleNamespace(get_acc_list=lambda: (0, frame))
    assert futu._resolve_acc_id(futu.FutuConfig(), ctx) == 1
    assert futu._resolve_acc_id(futu.FutuConfig(acc_id=1), ctx) == 1
    with pytest.raises(futu.FutuProfileMismatchError):
        futu._resolve_acc_id(futu.FutuConfig(acc_id=2), ctx)
    with pytest.raises(futu.FutuProfileMismatchError):
        futu._resolve_acc_id(futu.FutuConfig(profile="live"), ctx)
    assert futu._acc_id_of({"acc_id": "bad"}) == 0
    assert futu._first({"a": None, "b": 2}, ("a", "b")) == 2
    row = {"code": "HK.00700", "qty": 10, "order_id": "o", "deal_id": "d", "last_price": 300, "time_key": "now"}
    assert futu._position_to_dict(row)["code"] == "HK.00700"
    assert futu._order_to_dict(row)["order_id"] == "o"
    assert futu._deal_to_dict(row)["deal_id"] == "d"
    assert futu._quote_to_dict(row)["last"] == 300
    assert futu._bar_to_dict(row)["time"] == "now"

    monkeypatch.delenv(futu.LIVE_TRADE_PWD_ENV, raising=False)
    assert futu._unlock_if_live(futu.FutuConfig(), object(), fake_sdk) is None
    assert futu.LIVE_TRADE_PWD_ENV in futu._unlock_if_live(futu.FutuConfig(profile="live"), object(), fake_sdk)
    monkeypatch.setenv(futu.LIVE_TRADE_PWD_ENV, "hash")
    assert "failed" in futu._unlock_if_live(
        futu.FutuConfig(profile="live"), SimpleNamespace(unlock_trade=lambda **kwargs: (1, "denied")), fake_sdk,
    )
    assert futu._unlock_if_live(
        futu.FutuConfig(profile="live"), SimpleNamespace(unlock_trade=lambda **kwargs: (0, "ok")), fake_sdk,
    ) is None
    futu._close(SimpleNamespace(close=lambda: (_ for _ in ()).throw(RuntimeError("close"))))


def test_longbridge_normalization_conversion_and_safe_calls() -> None:
    assert longbridge._normalize_status("OrderStatus.NewStatus") == "new"
    assert longbridge._normalize_status("filled") == "filled"
    assert longbridge._normalize_status("unknown") == "unknown"
    assert longbridge._is_open_order({"status": "new"})
    assert not longbridge._is_open_order({"status": "filled"})
    assert longbridge._as_iter(None) == [] and longbridge._as_iter(1) == [1]
    assert longbridge._obj_get({"x": 1}, "x") == 1
    assert longbridge._first({"a": None, "b": 2}, ("a", "b")) == 2
    depth = {"asks": [{"price": 10}], "bids": [{"price": 9}]}
    assert longbridge._top_of_book(depth) == (9, 10)
    row = {
        "symbol": "700.HK",
        "total_cash": 100,
        "quantity": 10,
        "order_id": "o",
        "trade_id": "t",
        "last_done": 300,
        "timestamp": "now",
    }
    assert longbridge._balance_to_dict(row)["total_cash"] == 100
    assert longbridge._position_to_dict(row)["symbol"] == "700.HK"
    assert longbridge._order_to_dict(row)["order_id"] == "o"
    assert longbridge._execution_to_dict(row)["trade_id"] == "t"
    assert longbridge._quote_to_dict(row)["last"] == 300
    assert longbridge._bar_to_dict(row)["time"] == "now"

    client = SimpleNamespace(value=lambda value: value, fallback=lambda: "fallback")
    assert longbridge._call(client, "value", 1) == 1
    with pytest.raises(longbridge.LongbridgeConfigError):
        longbridge._call(client, "missing")
    assert longbridge._safe_call(client, "fallback") == "fallback"
    assert longbridge._safe_call(client, "missing") is None


def test_okx_response_order_extract_conversion_and_safe_call() -> None:
    cfg = okx.OKXConfig(api_key="k", api_secret="s", passphrase="p")
    assert okx._order_error(cfg, "bad")["status"] == "error"
    assert okx._resp_message({"msg": "denied"}) == "denied"
    assert okx._resp_message("raw") == ""
    assert okx._extract_data({"code": "0", "data": [{"x": 1}]}) == [{"x": 1}]
    assert okx._extract_data({"code": "1", "msg": "bad"}) == []
    assert okx._extract_data(None) == []
    assert okx._as_iter(1) == [1] and okx._as_iter(None) == []
    assert okx._obj_get({"x": 1}, "x") == 1
    assert okx._first({"a": None, "b": 2}, ("a", "b")) == 2
    row = {"ccy": "USDT", "cashBal": "10", "instId": "BTC-USDT", "pos": "1", "ordId": "o", "tradeId": "t", "last": "100", "ts": "1"}
    assert okx._balance_detail_to_dict(row)["currency"] == "USDT"
    assert okx._position_to_dict(row)["symbol"] == "BTC-USDT"
    assert okx._order_to_dict(row)["order_id"] == "o"
    assert okx._fill_to_dict(row)["trade_id"] == "t"
    assert okx._quote_to_dict(row)["last"] == "100"
    assert okx._candle_to_dict([1, 2, 3, 4, 5, 6, 7, 8, "1"])["close"] == 5
    assert okx._candle_to_dict([1])["close"] is None
    client = SimpleNamespace(value=lambda value: value, fallback=lambda: "fallback")
    assert okx._safe_call(client, "value", 1, ignored=True) == 1
    assert okx._safe_call(client, "fallback", 1) == "fallback"
    assert okx._safe_call(client, "missing") is None


def test_tiger_profile_conversion_and_safe_call() -> None:
    assert tiger.is_paper_account(" 12345678901234567 ")
    assert not tiger.is_paper_account("paper")
    assert not tiger.is_paper_account("live")
    assert tiger._as_iter(None) == [] and tiger._as_iter(1) == [1]
    assert tiger._obj_get({"x": 1}, "x") == 1
    assert tiger._first({"a": None, "b": 2}, ("a", "b")) == 2
    row = {"net_liquidation": 100, "symbol": "AAPL", "quantity": 2, "order_id": "o", "latest_price": 10, "time": "now"}
    assert tiger._asset_to_dict(row)["net_liquidation"] == 100
    assert tiger._position_to_dict(row)["symbol"] == "AAPL"
    assert tiger._order_to_dict(row)["order_id"] == "o"
    assert tiger._quote_to_dict(row)["last"] == 10
    assert tiger._bar_to_dict(row)["time"] == "now"
    client = SimpleNamespace(value=lambda value: value, fallback=lambda: "fallback")
    assert tiger._safe_call(client, "value", 1, ignored=True) == 1
    assert tiger._safe_call(client, "fallback", 1) == "fallback"
    assert tiger._safe_call(client, "missing") is None
