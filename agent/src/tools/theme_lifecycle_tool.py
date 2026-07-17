"""Theme lifecycle tracking tool.

Tracks investment themes through four stages and computes alpha multipliers:
Discovery (0.9) → Validation (0.7) → Mainstream (0.3) → Exhaustion (0.1).

Supports per-market stage detection and cross-market stage divergence
(e.g. a theme in Discovery in A-shares but Mainstream in US → arbitrage signal).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from src.agent.tools import BaseTool

logger = logging.getLogger(__name__)

_STAGES = {
    "discovery": {
        "label": "发现 (Discovery)",
        "alpha_multiplier": 0.9,
        "description": "Few analysts covering, low concept-board turnover, early adopters only.",
    },
    "validation": {
        "label": "验证 (Validation)",
        "alpha_multiplier": 0.7,
        "description": "Order confirmations appearing, analyst coverage accelerating, concept board active.",
    },
    "mainstream": {
        "label": "主流 (Mainstream)",
        "alpha_multiplier": 0.3,
        "description": "Broad coverage, high turnover, consensus formed. Alpha decaying.",
    },
    "exhaustion": {
        "label": "衰竭 (Exhaustion)",
        "alpha_multiplier": 0.1,
        "description": "Overcrowded, declining momentum, substitutes emerging. Minimal alpha.",
    },
}


def _ok(data: Any) -> str:
    return json.dumps({"ok": True, "data": data}, ensure_ascii=False, default=str)


def _error(message: str) -> str:
    return json.dumps({"ok": False, "error": message}, ensure_ascii=False)


class ThemeLifecycleTool(BaseTool):
    """Track investment theme lifecycle stage and compute alpha multipliers."""

    name = "get_theme_lifecycle"
    description = (
        "Track investment theme lifecycle stage: "
        "Discovery (alpha 0.9) → Validation (0.7) → Mainstream (0.3) → "
        "Exhaustion (0.1). Detects cross-market stage divergence for "
        "arbitrage signals."
    )
    parameters = {
        "type": "object",
        "properties": {
            "theme": {
                "type": "string",
                "description": (
                    "Investment theme to track (e.g. 'CPO/硅光子', "
                    "'800G光模块', 'AI算力电力', '人形机器人')"
                ),
            },
            "market": {
                "type": "string",
                "enum": ["A", "US", "HK", "all"],
                "description": "Target market. Use 'all' for cross-market comparison.",
            },
        },
        "required": ["theme"],
    }
    repeatable = True
    is_readonly = True

    def execute(self, **kwargs: Any) -> str:
        theme = kwargs.get("theme", "")
        if not theme:
            return _error("'theme' is required.")

        market = kwargs.get("market", "all")

        return _ok({
            "theme": theme,
            "stages_reference": _STAGES,
            "market": market,
            "stage_determination_guide": self._build_guide(market),
            "cross_market_divergence_instructions": (
                "When the same theme is at different stages across markets "
                "(e.g. A-share Discovery + US Mainstream), this signals "
                "potential catch-up arbitrage in the lagging market. "
                "Flag this explicitly in the output."
            ),
        })

    @staticmethod
    def _build_guide(market: str) -> dict[str, Any]:
        guides: dict[str, dict[str, Any]] = {}

        if market in ("A", "all"):
            guides["A"] = {
                "label": "A股",
                "indicators": [
                    {
                        "name": "concept_board_turnover",
                        "description": "概念板块换手率 vs 20日均值的 z-score",
                        "tool": "sector_tool(mode='ranking')",
                        "thresholds": {
                            "discovery": "z < 0.5 (低于均值)",
                            "validation": "0.5 ≤ z < 1.5",
                            "mainstream": "1.5 ≤ z < 3.0",
                            "exhaustion": "z ≥ 3.0 或从高位回落 >50%",
                        },
                    },
                    {
                        "name": "analyst_coverage_acceleration",
                        "description": "30日内新增研报数量变化率",
                        "tool": "get_research_reports",
                        "thresholds": {
                            "discovery": "覆盖研报 < 5篇/月",
                            "validation": "5-15篇/月 且环比增长 >50%",
                            "mainstream": ">15篇/月",
                            "exhaustion": "研报数量开始下降",
                        },
                    },
                    {
                        "name": "northbound_concentration",
                        "description": "北向资金在该主题标的的持仓集中度变化",
                        "tool": "northbound_tool",
                        "thresholds": {
                            "discovery": "北向持仓占比 < 行业均值",
                            "validation": "持仓占比加速上升",
                            "mainstream": "持仓占比高位企稳",
                            "exhaustion": "持仓占比开始下降",
                        },
                    },
                ],
            }

        if market in ("US", "all"):
            guides["US"] = {
                "label": "美股",
                "indicators": [
                    {
                        "name": "analyst_coverage_count",
                        "description": "分析师覆盖数量 (yfinance recommendations)",
                        "tool": "yfinance Ticker.recommendations",
                        "thresholds": {
                            "discovery": "覆盖分析师 < 5人",
                            "validation": "5-15人 且近3月新增覆盖",
                            "mainstream": ">15人",
                            "exhaustion": "覆盖数量停滞或下降",
                        },
                    },
                    {
                        "name": "etf_fund_flow",
                        "description": "相关主题ETF资金流入",
                        "tool": "us-etf-flow skill",
                        "thresholds": {
                            "discovery": "无专用ETF或规模极小",
                            "validation": "ETF净流入加速",
                            "mainstream": "ETF规模 >$1B",
                            "exhaustion": "ETF净流出",
                        },
                    },
                    {
                        "name": "sec_13f_holdings",
                        "description": "机构13F持仓变化",
                        "tool": "edgar-sec-filings",
                        "thresholds": {
                            "discovery": "少数基金持有",
                            "validation": "新增机构买入加速",
                            "mainstream": "大型基金广泛持有",
                            "exhaustion": "机构开始减持",
                        },
                    },
                ],
            }

        if market in ("HK", "all"):
            guides["HK"] = {
                "label": "港股",
                "indicators": [
                    {
                        "name": "southbound_flow",
                        "description": "南向资金流向",
                        "tool": "hk-connect-flow skill",
                        "thresholds": {
                            "discovery": "南向净买入偏低",
                            "validation": "南向净买入加速",
                            "mainstream": "南向持仓集中",
                            "exhaustion": "南向开始净卖出",
                        },
                    },
                    {
                        "name": "turnover_share",
                        "description": "主题标的成交额占市场比例变化",
                        "tool": "yfinance volume data",
                        "thresholds": {
                            "discovery": "成交占比低于历史均值",
                            "validation": "成交占比上升中",
                            "mainstream": "成交占比高位",
                            "exhaustion": "成交占比回落",
                        },
                    },
                ],
            }

        return guides
