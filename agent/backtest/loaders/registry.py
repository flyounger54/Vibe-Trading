"""Loader registry with market-level fallback chains.

Loaders self-register via the ``@register`` decorator when their module is
first imported.  The ``_ensure_registered()`` helper lazily imports every
known loader module so that callers of ``resolve_loader`` /
``get_loader_cls_with_fallback`` never see an empty registry — regardless
of import order.
"""

from __future__ import annotations

import logging
from typing import Any, Type

from backtest.loaders.base import NoAvailableSourceError
from backtest.loaders.platform import BarRequest, FetchReport, ProviderRegistry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Global registry: source_name -> loader class
# ---------------------------------------------------------------------------

LOADER_REGISTRY: dict[str, Type[Any]] = {}

_registered = False

# Canonical set of accepted data-source names: every registered loader plus the
# ``"auto"`` cross-market selector. Single source of truth shared by the backtest
# config schema (``backtest.runner.BacktestConfigSchema``) and the agent-facing
# backtest tool (``src.tools.backtest_tool``) so the two can never drift apart.
# Keep in sync with ``_loader_modules`` below — the regression test
# ``test_valid_sources_covers_all_registered_loaders`` enforces full coverage.
VALID_SOURCES: set[str] = {
    "astock",
    "global",
    "tushare",
    "okx",
    "ccxt",
    "local",
    "auto",
}


def register(cls: Type[Any]) -> Type[Any]:
    """Class decorator: register a loader into the global registry.

    The class must have a ``name`` class attribute.
    """
    LOADER_REGISTRY[cls.name] = cls
    return cls


def _ensure_registered() -> None:
    """Import every known loader module so ``@register`` decorators fire.

    Safe to call multiple times — only runs the imports once.
    Loaders whose dependencies are missing (e.g. ``akshare`` not installed)
    are silently skipped.
    """
    global _registered
    if _registered:
        return
    _registered = True

    _loader_modules = [
        "backtest.loaders.astock_loader",
        "backtest.loaders.global_loader",
        "backtest.loaders.tushare",
        "backtest.loaders.okx",
        "backtest.loaders.ccxt_loader",
        "backtest.loaders.local_loader",
    ]
    import importlib
    for mod in _loader_modules:
        try:
            importlib.import_module(mod)
        except Exception:
            pass


# Sources that must NEVER silently fall through to a network loader when the
# caller asked for them explicitly. ``local`` reads the user's own configured
# files (``~/.vibe-trading/data-bridge/config.yaml``); its ``markets`` set spans
# every market only so the cross-market auto-resolver can *reach* it, not so an
# unavailable ``local`` request can degrade into an unrelated network source.
# An explicit ``local`` request that is unavailable is a config problem the user
# must see, not something to paper over with a Yahoo/Tencent fetch.
_NO_NETWORK_FALLBACK_SOURCES: frozenset[str] = frozenset({"local"})


# ---------------------------------------------------------------------------
# Fallback chains: market_type -> ordered list of source names
# ---------------------------------------------------------------------------

# Chains are ordered by IP-ban risk first (lighter, throttle-tolerant public
# endpoints lead; key-gated REST and rate-limit-prone sources trail), then by
# data quality. Eastmoney/Sina/Stooq/Yahoo are unauthenticated public sources
# that must be politely throttled; Finnhub/AlphaVantage/Tiingo/FMP are key-gated
# REST fallbacks placed deeper in the chain.
FALLBACK_CHAINS: dict[str, list[str]] = {
    "a_share":   ["astock", "tushare", "local"],
    "us_equity": ["global", "local"],
    "hk_equity": ["global", "local"],
    "crypto":    ["okx", "ccxt", "local"],
    "futures":   ["tushare", "local"],
    "fund":      ["tushare", "local"],
    "macro":     ["tushare", "local"],
    "forex":     ["local"],
}


class FallbackLoader:
    """Compatibility adapter exposing per-symbol fallback as a DataLoader.

    Existing backtest and tool call sites still receive a loader with ``name``
    and ``fetch``. The adapter delegates execution to :class:`ProviderRegistry`
    and retains the structured report for observability.
    """

    requires_auth = False

    def __init__(self, market: str, *, preferred: str | None = None) -> None:
        self.market = market
        self.markets = {market}
        self.preferred = preferred
        self.name = preferred or (FALLBACK_CHAINS.get(market) or ["auto"])[0]
        self.last_report: FetchReport | None = None
        self._registry = ProviderRegistry(
            providers=LOADER_REGISTRY,
            fallback_chains=FALLBACK_CHAINS,
        )

    def is_available(self) -> bool:
        return any(_loader_is_available(name) for name in self._candidate_names())

    def _candidate_names(self) -> list[str]:
        chain = list(FALLBACK_CHAINS.get(self.market, ()))
        if self.preferred:
            return [self.preferred, *(name for name in chain if name != self.preferred)]
        return chain

    def fetch(
        self,
        codes: list[str],
        start_date: str,
        end_date: str,
        *,
        interval: str = "1D",
        fields: list[str] | None = None,
        adjustment: str = "none",
    ) -> dict[str, Any]:
        report = self._registry.fetch(
            BarRequest(
                symbols=tuple(codes),
                market=self.market,
                start_date=start_date,
                end_date=end_date,
                interval=interval,
                fields=tuple(fields or ()),
                adjustment=adjustment,
            ),
            preferred=self.preferred,
        )
        self.last_report = report
        for symbol, failure in report.failures.items():
            logger.warning("market data failed for %s: %s", symbol, failure.reason)
        return report.data


def _loader_is_available(name: str) -> bool:
    loader_cls = LOADER_REGISTRY.get(name)
    if loader_cls is None:
        return False
    try:
        return bool(loader_cls().is_available())
    except Exception as exc:  # noqa: BLE001 - availability must not abort fallback
        logger.debug("loader %s failed availability check: %s", name, exc)
        return False


def _first_available_name(market: str) -> str | None:
    for name in FALLBACK_CHAINS.get(market, ()):
        if _loader_is_available(name):
            return name
    return None


def resolve_loader(market: str) -> Any:
    """Return a loader that applies the market fallback chain per symbol.

    Walks the fallback chain and returns the first loader whose
    ``is_available()`` returns ``True``.

    Args:
        market: Market type key (e.g. ``"a_share"``, ``"crypto"``).

    Returns:
        A loader instance.

    Raises:
        NoAvailableSourceError: If every candidate is unavailable.
    """
    _ensure_registered()
    chain = FALLBACK_CHAINS.get(market, [])
    primary = _first_available_name(market)
    if primary is not None:
        return FallbackLoader(market, preferred=primary)
    raise NoAvailableSourceError(
        f"No available data source for market '{market}'. "
        f"Tried: {chain}. Check dependencies, network, and API token config."
    )


def get_loader_cls_with_fallback(source: str) -> Type[Any]:
    """Return a loader *class* for *source*, falling back if unavailable.

    Args:
        source: Requested data source name.

    Returns:
        A DataLoader class (not instance).

    Raises:
        NoAvailableSourceError: If the source and all fallbacks are unavailable.
    """
    _ensure_registered()
    if source not in LOADER_REGISTRY:
        raise NoAvailableSourceError(f"Unknown data source: {source}")

    loader_cls = LOADER_REGISTRY[source]
    try:
        instance = loader_cls()
    except Exception as exc:
        logger.debug("loader %s failed to construct: %s", source, exc)
        instance = None
    if instance is not None and instance.is_available():
        return loader_cls

    # Some sources must never silently degrade to an unrelated network loader
    # when explicitly requested. ``local`` is the canonical case: its broad
    # ``markets`` set exists only to make it reachable from the cross-market
    # auto-resolver, so falling back through it would fetch network data the
    # user never asked for and mask a Data Bridge config problem. Fail loudly.
    if source in _NO_NETWORK_FALLBACK_SOURCES:
        raise NoAvailableSourceError(
            f"Data source '{source}' is unavailable and does not fall back to a "
            f"network source. Check your local Data Bridge config "
            f"(~/.vibe-trading/data-bridge/config.yaml) — it must exist and list "
            f"at least one source."
        )

    # Source unavailable — try same-market fallback
    for market in loader_cls.markets:
        fallback_name = _first_available_name(market)
        if fallback_name is not None:
            logger.warning(
                "%s is unavailable, falling back to %s for market %s",
                source, fallback_name, market,
            )
            return LOADER_REGISTRY[fallback_name]

    raise NoAvailableSourceError(
        f"Data source '{source}' is unavailable and no fallback found."
    )
