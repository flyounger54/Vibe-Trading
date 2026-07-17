#!/usr/bin/env python3
"""Small real-provider smoke canary; never imported by deterministic tests."""

from __future__ import annotations

import argparse
import json
from datetime import date, timedelta

from backtest.loaders.astock_loader import DataLoader as AStockLoader
from backtest.loaders.global_loader import DataLoader as GlobalLoader
from backtest.loaders.okx import DataLoader as OkxLoader


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--alert-only", action="store_true")
    args = parser.parse_args()
    end = date.today() - timedelta(days=3)
    start = end - timedelta(days=14)
    cases = [
        ("a_share_qfq", AStockLoader(), ["600519.SH"], "qfq"),
        ("us_equity", GlobalLoader(), ["AAPL.US"], "none"),
        ("crypto", OkxLoader(), ["BTC-USDT"], "none"),
    ]
    results: dict[str, object] = {}
    failed = False
    for label, loader, symbols, adjustment in cases:
        try:
            data = loader.fetch(
                symbols,
                start.isoformat(),
                end.isoformat(),
                interval="1D",
                adjustment=adjustment,
            )
            ok = all(symbol in data and not data[symbol].empty for symbol in symbols)
            results[label] = {
                "ok": ok,
                "provider": loader.name,
                "symbols": symbols,
                "rows": {symbol: len(data.get(symbol, ())) for symbol in symbols},
            }
            failed = failed or not ok
        except Exception as exc:  # noqa: BLE001 - canary must report every provider
            failed = True
            results[label] = {
                "ok": False,
                "provider": loader.name,
                "symbols": symbols,
                "reason": f"{type(exc).__name__}: {exc}",
            }
    print(json.dumps({"as_of": date.today().isoformat(), "providers": results}, ensure_ascii=False))
    if failed and args.alert_only:
        print("::warning title=Market-data canary degraded::One or more real providers failed; deterministic CI is unaffected")
        return 0
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
