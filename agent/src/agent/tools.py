"""BaseTool + ToolRegistry: tool infrastructure."""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from functools import wraps
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


_DATA_TOOL_SOURCES = {
    "get_market_data": "multi-provider",
    "get_fund_flow": "eastmoney",
    "get_dragon_tiger": "eastmoney",
    "get_northbound_flow": "eastmoney",
    "get_margin_trading": "eastmoney",
    "get_block_trades": "eastmoney",
    "get_shareholder_count": "eastmoney",
    "get_lockup_expiry": "eastmoney",
    "get_sector_info": "eastmoney",
    "get_research_reports": "eastmoney+ths",
    "get_stock_news": "eastmoney+yahoo",
    "get_sec_filings": "sec-edgar",
    "get_financial_statements": "eastmoney",
    "get_options_chain": "yahoo",
    "get_stock_profile": "yahoo",
    "screen_market": "eastmoney",
    "search_symbol": "eastmoney+yahoo",
    "get_macro_series": "fred",
    "iwencai_search": "iwencai",
}


def _enrich_data_tool_result(tool_name: str, result: object) -> object:
    """Add uniform provenance/as-of/failure fields to specialist data tools."""
    if tool_name not in _DATA_TOOL_SOURCES or not isinstance(result, str):
        return result
    try:
        payload = json.loads(result)
    except (TypeError, json.JSONDecodeError):
        return result
    if not isinstance(payload, dict):
        return result
    payload.setdefault("source", _DATA_TOOL_SOURCES[tool_name])
    payload.setdefault("as_of", datetime.now(timezone.utc).isoformat())
    if payload.get("ok") is False or "error" in payload:
        reason = payload.get("error") or payload.get("reason") or "data provider failed"
        payload.setdefault("failure_reason", str(reason))
    return json.dumps(payload, ensure_ascii=False, allow_nan=False)


class BaseTool(ABC):
    """Tool base class.

    Attributes:
        name: Unique tool identifier.
        description: Tool description shown to the LLM.
        parameters: Parameter definition in JSON Schema format.
        repeatable: Whether the tool may be called more than once.
    """

    name: str = ""
    description: str = ""
    parameters: Dict[str, Any] = {}
    repeatable: bool = False
    is_readonly: bool = True

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Wrap specialist data-tool responses in the common provenance contract."""
        super().__init_subclass__(**kwargs)
        execute = cls.__dict__.get("execute")
        if execute is None or getattr(cls, "name", "") not in _DATA_TOOL_SOURCES:
            return

        @wraps(execute)
        def enriched(self: "BaseTool", **call_kwargs: Any) -> object:
            return _enrich_data_tool_result(cls.name, execute(self, **call_kwargs))

        cls.execute = enriched  # type: ignore[method-assign]

    @classmethod
    def check_available(cls) -> bool:
        """Check if this tool's dependencies are met.

        Override in subclasses to check for API keys, packages, etc.
        Tools that return False are excluded from the registry.
        """
        return True

    @abstractmethod
    def execute(self, **kwargs: Any) -> str:
        """Execute the tool and return a JSON string."""

    def to_openai_schema(self) -> Dict[str, Any]:
        """Convert to OpenAI function calling format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters or {"type": "object", "properties": {}, "required": []},
            },
        }


class ToolRegistry:
    """Tool registry."""

    def __init__(self) -> None:
        self._tools: Dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        """Register a tool."""
        self._tools[tool.name] = tool

    def get(self, name: str) -> Optional[BaseTool]:
        """Retrieve a tool by name."""
        return self._tools.get(name)

    def get_definitions(self) -> List[Dict[str, Any]]:
        """Return all tools in OpenAI function calling format."""
        return [t.to_openai_schema() for t in self._tools.values()]

    def execute(self, name: str, params: Dict[str, Any]) -> str:
        """Execute a tool and guarantee a valid JSON return value."""
        tool = self._tools.get(name)
        if not tool:
            return json.dumps({"status": "error", "error": f"Tool '{name}' not found"}, ensure_ascii=False)
        try:
            return tool.execute(**params)
        except Exception as exc:
            logger.exception("Tool %s failed", name)
            return json.dumps({
                "status": "error", "tool": name,
                "error": str(exc),
            }, ensure_ascii=False)

    @property
    def tool_names(self) -> List[str]:
        return list(self._tools.keys())

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools
