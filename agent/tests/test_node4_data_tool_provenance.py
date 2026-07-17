"""Uniform provenance contract for specialist market-data tools."""

from __future__ import annotations

import json

from src.agent.tools import BaseTool


class _SuccessDataTool(BaseTool):
    name = "get_fund_flow"

    def execute(self, **kwargs):
        return json.dumps({"ok": True, "data": {"rows": []}})


class _FailedDataTool(BaseTool):
    name = "get_sec_filings"

    def execute(self, **kwargs):
        return json.dumps({"ok": False, "error": "upstream timeout"})


def test_specialist_success_has_source_and_utc_as_of() -> None:
    result = json.loads(_SuccessDataTool().execute())
    assert result["source"] == "eastmoney"
    assert result["as_of"].endswith("+00:00")


def test_specialist_failure_has_provider_and_machine_readable_reason() -> None:
    result = json.loads(_FailedDataTool().execute())
    assert result["source"] == "sec-edgar"
    assert result["failure_reason"] == "upstream timeout"
    assert result["as_of"].endswith("+00:00")
