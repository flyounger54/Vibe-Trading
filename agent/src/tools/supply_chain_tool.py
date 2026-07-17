"""Supply-chain chokepoint analysis tool.

Four modes:
* **universe** — query the pre-built 181-ticker AI supply-chain universe
* **graph** — build a directed supply-chain graph for a given theme
* **score** — compute 6-dimension chokepoint score for a single ticker
* **discover** — dynamically discover tickers for themes outside the
  pre-built universe (robotics, EV, defense, etc.)

All modes return a JSON envelope ``{"ok": true, "data": ...}`` or
``{"ok": false, "error": ...}``.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from src.agent.tools import BaseTool
from src.tools._supply_chain_universe import get_layers, get_sub_sectors, get_universe

logger = logging.getLogger(__name__)

_VALID_MODES = ("universe", "graph", "score", "discover")
_VALID_MARKETS = ("A", "US", "HK")

_SCORE_DIMENSIONS = [
    {"key": "supply_concentration", "weight": 22, "label": "供给集中度"},
    {"key": "irreplaceability", "weight": 22, "label": "不可替代性"},
    {"key": "supply_demand_gap", "weight": 16, "label": "供需缺口"},
    {"key": "certification_barrier", "weight": 16, "label": "认证壁垒"},
    {"key": "info_asymmetry", "weight": 14, "label": "信息不对称"},
    {"key": "catalyst_optionality", "weight": 10, "label": "催化剂/期权性"},
]


def _ok(data: Any) -> str:
    return json.dumps({"ok": True, "data": data}, ensure_ascii=False, default=str)


def _error(message: str) -> str:
    return json.dumps({"ok": False, "error": message}, ensure_ascii=False)


class SupplyChainTool(BaseTool):
    """Analyze supply-chain chokepoints: universe lookup, graph build, scoring."""

    name = "get_supply_chain"
    description = (
        "Supply-chain chokepoint analysis. "
        "mode='universe': query pre-built AI supply-chain universe (181 tickers across A/US/HK). "
        "mode='graph': build supply-chain directed graph for a theme. "
        "mode='score': compute 6-dimension chokepoint score for a ticker. "
        "mode='discover': dynamically find tickers for themes outside the pre-built universe."
    )
    parameters = {
        "type": "object",
        "properties": {
            "mode": {
                "type": "string",
                "enum": list(_VALID_MODES),
                "description": (
                    "universe: query pre-built universe | "
                    "graph: build supply-chain graph | "
                    "score: compute chokepoint score | "
                    "discover: find tickers for new themes"
                ),
            },
            "topic": {
                "type": "string",
                "description": "Theme or topic (e.g. '800G光模块', 'CPO', 'AI算力电力'). Required for graph/discover.",
            },
            "code": {
                "type": "string",
                "description": "Stock code (e.g. '300308.SZ', 'COHR'). Required for score mode.",
            },
            "layer": {
                "type": "string",
                "description": "Filter by supply-chain layer: compute/network/storage/software/application.",
            },
            "sub_sector": {
                "type": "string",
                "description": "Filter by sub-sector (e.g. optical_module, cpo, advanced_packaging, gpu).",
            },
            "market": {
                "type": "string",
                "enum": list(_VALID_MARKETS),
                "description": "Filter by market: A (A-shares), US, HK.",
            },
        },
        "required": ["mode"],
    }
    repeatable = True
    is_readonly = True

    def execute(self, **kwargs: Any) -> str:
        mode = kwargs.get("mode", "")
        if mode not in _VALID_MODES:
            return _error(f"Invalid mode '{mode}'. Must be one of {_VALID_MODES}.")

        if mode == "universe":
            return self._universe(kwargs)
        if mode == "graph":
            return self._graph(kwargs)
        if mode == "score":
            return self._score(kwargs)
        if mode == "discover":
            return self._discover(kwargs)
        return _error(f"Unhandled mode: {mode}")

    # ── universe mode ──

    def _universe(self, kwargs: dict[str, Any]) -> str:
        layer = kwargs.get("layer")
        sub_sector = kwargs.get("sub_sector")
        market = kwargs.get("market")

        tickers = get_universe(layer=layer, sub_sector=sub_sector, market=market)

        return _ok({
            "mode": "universe",
            "filters": {
                "layer": layer,
                "sub_sector": sub_sector,
                "market": market,
            },
            "count": len(tickers),
            "available_layers": get_layers(),
            "available_sub_sectors": get_sub_sectors(),
            "tickers": tickers,
        })

    # ── graph mode ──

    def _graph(self, kwargs: dict[str, Any]) -> str:
        topic = kwargs.get("topic", "")
        if not topic:
            return _error("'topic' is required for graph mode.")

        graph = self._build_graph_for_topic(topic, kwargs.get("market"))
        return _ok({
            "mode": "graph",
            "topic": topic,
            "layers": graph,
            "instructions": (
                "This is a template graph. The LLM should enrich each layer "
                "with specific companies, market share data, and chokepoint "
                "flags based on its knowledge and available tools. "
                "Use get_research_reports, get_financial_statements, and "
                "sector_tool to validate each node."
            ),
        })

    def _build_graph_for_topic(
        self, topic: str, market: str | None
    ) -> list[dict[str, Any]]:
        topic_lower = topic.lower()

        universe = get_universe(market=market)
        matched: dict[str, list[dict[str, Any]]] = {}
        for t in universe:
            key = t["layer"]
            if key not in matched:
                matched[key] = []
            if self._topic_matches(topic_lower, t):
                matched[key].append(t)

        layers = []
        for layer_name in get_layers():
            companies = matched.get(layer_name, [])
            layers.append({
                "layer": layer_name,
                "layer_label": {
                    "compute": "算力层 (Compute)",
                    "network": "网络层 (Network)",
                    "storage": "存储层 (Storage)",
                    "software": "软件层 (Software)",
                    "application": "应用层 (Application)",
                }.get(layer_name, layer_name),
                "companies": [
                    {
                        "code": c["code"],
                        "name": c["name"],
                        "sub_sector": c["sub_sector"],
                        "key_products": c["key_products"],
                        "chokepoint_flag": False,
                    }
                    for c in companies
                ],
                "company_count": len(companies),
            })
        return layers

    @staticmethod
    def _topic_matches(topic_lower: str, ticker: dict[str, Any]) -> bool:
        searchable = " ".join([
            ticker.get("sub_sector", ""),
            ticker.get("key_products", ""),
            ticker.get("name", ""),
        ]).lower()

        keywords = topic_lower.replace("/", " ").replace("_", " ").split()
        cjk_chars = [ch for ch in topic_lower if "一" <= ch <= "鿿"]
        if cjk_chars:
            keywords.extend(
                topic_lower[i : i + 2]
                for i in range(len(topic_lower) - 1)
                if "一" <= topic_lower[i] <= "鿿"
            )
        return any(kw in searchable for kw in keywords) if keywords else True

    # ── score mode ──

    def _score(self, kwargs: dict[str, Any]) -> str:
        code = kwargs.get("code", "")
        if not code:
            return _error("'code' is required for score mode.")

        universe = get_universe()
        ticker_info = next(
            (t for t in universe if t["code"] == code), None
        )

        return _ok({
            "mode": "score",
            "code": code,
            "ticker_info": ticker_info,
            "score_dimensions": _SCORE_DIMENSIONS,
            "instructions": (
                "The LLM should score each dimension (0 to max weight) based "
                "on evidence gathered from available tools. For each dimension:\n"
                "1. supply_concentration: Use industry reports to estimate top-3 "
                "supplier market share. Apply non-linear curve: "
                "30%→5, 50%→12, 70%→18, 90%→22.\n"
                "2. irreplaceability: Assess material-science moat × "
                "qualification cycle length.\n"
                "3. supply_demand_gap: Compare demand CAGR vs capacity CAGR.\n"
                "4. certification_barrier: Check designed-in status and "
                "certification cycle (>2yr = high).\n"
                "5. info_asymmetry: Use get_research_reports to count analyst "
                "coverage; use get_stock_profile for market cap. "
                "Small cap + few analysts = high score.\n"
                "6. catalyst_optionality: Check insider buying, short interest, "
                "M&A potential via get_stock_news.\n\n"
                "Apply evidence governance: dimensions without P0 evidence "
                "get score × 0.5. Sum all dimensions for total (max 100)."
            ),
        })

    # ── discover mode ──

    def _discover(self, kwargs: dict[str, Any]) -> str:
        topic = kwargs.get("topic", "")
        if not topic:
            return _error("'topic' is required for discover mode.")

        market = kwargs.get("market")

        in_universe = [
            t for t in get_universe(market=market)
            if self._topic_matches(topic.lower(), t)
        ]

        return _ok({
            "mode": "discover",
            "topic": topic,
            "market": market,
            "pre_built_matches": in_universe,
            "pre_built_count": len(in_universe),
            "instructions": (
                "The pre-built universe returned {count} matches for '{topic}'. "
                "To discover additional tickers outside the pre-built universe:\n"
                "A-shares: Use sector_tool(mode='membership') to reverse-lookup "
                "concept boards matching the topic. Use iwencai_tool for semantic "
                "search (e.g. 'CPO供应链公司').\n"
                "US: Use yfinance screener for industry filtering. Use "
                "sec_filings_tool to search 10-K filings mentioning the topic.\n"
                "HK: Use yfinance industry filter + northbound_tool to "
                "cross-validate with institutional holdings.\n"
                "Deduplicate results and classify each into supply-chain layers."
            ).format(count=len(in_universe), topic=topic),
        })
