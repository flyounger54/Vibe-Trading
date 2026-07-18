"""Dense branch coverage for provider normalization and LLM adapters."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import src.providers.llm as llm


pytestmark = pytest.mark.unit


def test_thought_signature_extract_collect_and_mutation_edges() -> None:
    adapter = llm.ChatOpenAIWithReasoning
    assert adapter is not None
    assert adapter._extract_tool_call_thought_signature(None) is None
    assert adapter._extract_tool_call_thought_signature(
        {"extra_content": {"google": {"thoughtSignature": "gemini"}}}
    ) == "gemini"
    assert adapter._extract_tool_call_thought_signature(
        {"function": {"thought_signature": "function"}}
    ) == "function"
    assert adapter._extract_tool_call_thought_signature(
        {"thoughtSignature": "root"}
    ) == "root"
    assert adapter._extract_tool_call_thought_signature({"function": "bad"}) is None

    assert adapter._collect_tool_call_thought_signatures("bad") == []
    collected = adapter._collect_tool_call_thought_signatures(
        [
            None,
            {},
            {"id": "call-1", "index": 7, "thought_signature": "one"},
            {"thoughtSignature": "two"},
        ]
    )
    assert collected == [
        {"index": 7, "thought_signature": "one", "id": "call-1"},
        {"index": 3, "thought_signature": "two"},
    ]

    assert adapter._signature_maps(SimpleNamespace(additional_kwargs={})) == ({}, {})
    by_id, by_index = adapter._signature_maps(
        SimpleNamespace(
            additional_kwargs={
                "tool_call_thought_signatures": {
                    "id": "call-1",
                    "index": 2,
                    "thought_signature": "saved",
                },
                "tool_calls": [
                    None,
                    {"id": "call-2", "thoughtSignature": "raw"},
                    {"id": "missing"},
                ],
            }
        )
    )
    assert by_id == {"call-1": "saved", "call-2": "raw"}
    assert by_index == {2: "saved", 1: "raw"}

    call: dict[str, object] = {"extra_content": "bad"}
    adapter._set_tool_call_thought_signature(call, "sig")
    assert call["extra_content"] == {"google": {"thought_signature": "sig"}}
    adapter._set_tool_call_thought_signature(None, "ignored")

    outbound = [{"id": "call-1"}, {}, "bad"]
    adapter._inject_tool_call_thought_signatures(
        outbound,
        SimpleNamespace(
            additional_kwargs={
                "tool_call_thought_signatures": [
                    None,
                    {},
                    {"id": "call-1", "thought_signature": "by-id"},
                    {"index": 1, "thought_signature": "by-index"},
                ]
            }
        ),
    )
    assert outbound[0]["extra_content"]["google"]["thought_signature"] == "by-id"
    assert outbound[1]["extra_content"]["google"]["thought_signature"] == "by-index"
    adapter._inject_tool_call_thought_signatures("bad", SimpleNamespace())
    adapter._inject_tool_call_thought_signatures([], SimpleNamespace(additional_kwargs={}))

    adapter._strip_tool_call_extra_content("bad")
    adapter._strip_tool_call_extra_content([{"extra_content": {}}, None])


def test_diagnostic_redaction_and_adapter_mode_edges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert llm._redact_env_source(None) == "none (no .env file found)"
    assert llm._redact_env_source(llm._ENV_CANDIDATES[1]) == "<AGENT_DIR>/.env"
    assert llm._redact_env_source(Path("/outside/.env")) == "<.env>"

    assert llm._redact_base_url_for_log(None) == "(unset)"
    assert llm._redact_base_url_for_log("not-a-url") == "<base-url>"
    assert llm._redact_base_url_for_log("https://[2001:db8::1]:443/path?q=secret") == (
        "https://[2001:db8::1]:443"
    )
    assert llm._redact_base_url_for_log("https://example.com:bad") == "https://example.com"

    monkeypatch.setattr(
        llm,
        "version",
        lambda package: (_ for _ in ()).throw(llm.PackageNotFoundError(package)),
    )
    assert llm._package_version("missing") == "not_installed"
    monkeypatch.delenv("NODE11_FLAG", raising=False)
    assert llm._redact_env_flag("NODE11_FLAG") == "unset"
    monkeypatch.setenv("NODE11_FLAG", "secret")
    assert llm._redact_env_flag("NODE11_FLAG") == "set"
    assert llm._redact_proxy_url("HTTPS_PROXY", None) == "unset"
    assert llm._redact_proxy_url("NO_PROXY", "localhost") == "set"

    for value, expected in (
        (" compat ", "openai-compatible"),
        ("OPENAI_COMPATIBLE", "openai-compatible"),
        ("", "auto"),
        ("native", "native"),
    ):
        monkeypatch.setenv("VIBE_TRADING_DEEPSEEK_ADAPTER", value)
        assert llm._deepseek_adapter_mode() == expected


def test_optional_native_adapter_and_fallback_envs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        llm,
        "import_module",
        lambda name: (_ for _ in ()).throw(ImportError("not installed")),
    )
    assert llm._build_native_deepseek(model="deepseek", temperature=0.2) is None

    captured: dict[str, object] = {}

    class FakeDeepSeek:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(
        llm,
        "import_module",
        lambda name: SimpleNamespace(ChatDeepSeek=FakeDeepSeek),
    )
    env = {
        "OPENAI_API_KEY": "fallback-key",
        "OPENAI_API_BASE": "https://fallback.example/v1",
        "TIMEOUT_SECONDS": "9",
        "MAX_RETRIES": "4",
    }
    with patch.dict(os.environ, env, clear=True):
        native = llm._build_native_deepseek(
            model="deepseek", temperature=0.2, callbacks=["callback"]
        )
    assert isinstance(native, FakeDeepSeek)
    assert captured["api_key"] == "fallback-key"
    assert captured["base_url"] == "https://fallback.example/v1"


def test_fallback_dotenv_parser_and_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\n\ninvalid\n =ignored\nONE='first'\nTWO=\"second\"\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(llm, "load_dotenv", None)
    monkeypatch.delenv("ONE", raising=False)
    monkeypatch.delenv("TWO", raising=False)
    llm._load_env_file(env_file)
    assert os.environ["ONE"] == "first" and os.environ["TWO"] == "second"

    calls: list[Path] = []
    missing = tmp_path / "missing"
    monkeypatch.setattr(llm, "_ENV_CANDIDATES", [missing, env_file])
    monkeypatch.setattr(llm, "_ENV_LABELS", ("missing", "selected"))
    monkeypatch.setattr(llm, "_load_env_file", calls.append)
    monkeypatch.setattr(llm, "_dotenv_loaded", False)
    llm._ensure_dotenv()
    llm._ensure_dotenv()
    assert calls == [env_file]

    monkeypatch.setattr(llm, "_ENV_CANDIDATES", [missing])
    monkeypatch.setattr(llm, "_ENV_LABELS", ("missing",))
    monkeypatch.setattr(llm, "_dotenv_loaded", False)
    llm._ensure_dotenv()
    assert llm._dotenv_loaded


def test_url_normalization_and_provider_sync_empty_edges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert llm._normalize_ollama_base_url("  ") == ""
    assert llm._normalize_ollama_base_url("http://localhost:11434/v1/") == (
        "http://localhost:11434/v1"
    )
    assert llm._normalize_ollama_base_url("http://localhost:11434") == (
        "http://localhost:11434/v1"
    )

    monkeypatch.setattr(llm, "_dotenv_loaded", True)
    with patch.dict(
        os.environ,
        {"LANGCHAIN_PROVIDER": "openai_codex", "OPENAI_API_KEY": "remove-me"},
        clear=True,
    ):
        llm._sync_provider_env()
        assert "OPENAI_API_KEY" not in os.environ
        assert os.environ["OPENAI_API_BASE"].endswith("/codex/responses")

    with patch.dict(os.environ, {"LANGCHAIN_PROVIDER": "openai"}, clear=True):
        llm._sync_provider_env()
        assert "OPENAI_API_KEY" not in os.environ
        assert "OPENAI_API_BASE" not in os.environ

    with patch.dict(os.environ, {"LANGCHAIN_PROVIDER": "ollama"}, clear=True):
        llm._sync_provider_env()
        assert os.environ["OPENAI_API_KEY"] == "ollama"
        assert "OPENAI_API_BASE" not in os.environ


def test_build_llm_error_codex_deepseek_and_moonshot_edges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(llm, "_dotenv_loaded", True)
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(RuntimeError, match="LANGCHAIN_MODEL_NAME"):
            llm.build_llm()

    captured: dict[str, object] = {}

    class FakeCodex:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setitem(
        sys.modules,
        "src.providers.openai_codex",
        SimpleNamespace(OpenAICodexLLM=FakeCodex),
    )
    codex_env = {
        "LANGCHAIN_PROVIDER": "openai-codex",
        "LANGCHAIN_MODEL_NAME": "gpt-codex",
        "LANGCHAIN_TEMPERATURE": "0.3",
        "TIMEOUT_SECONDS": "8",
        "LANGCHAIN_REASONING_EFFORT": "high",
    }
    with patch.dict(os.environ, codex_env, clear=True):
        assert isinstance(llm.build_llm(), FakeCodex)
    assert captured["reasoning_effort"] == "high"

    monkeypatch.setattr(llm, "_build_native_deepseek", lambda **kwargs: None)
    deepseek_env = {
        "LANGCHAIN_PROVIDER": "deepseek",
        "LANGCHAIN_MODEL_NAME": "deepseek-v4",
        "VIBE_TRADING_DEEPSEEK_ADAPTER": "native",
    }
    with patch.dict(os.environ, deepseek_env, clear=True):
        with pytest.raises(RuntimeError, match="langchain-deepseek"):
            llm.build_llm()

    monkeypatch.setattr(llm, "ChatOpenAI", None)
    with patch.dict(
        os.environ,
        {"LANGCHAIN_PROVIDER": "openai", "LANGCHAIN_MODEL_NAME": "gpt"},
        clear=True,
    ):
        with pytest.raises(RuntimeError, match="langchain-openai"):
            llm.build_llm()

    class FakeOpenAI:
        def __init__(self, **kwargs: object) -> None:
            captured.clear()
            captured.update(kwargs)

    monkeypatch.setattr(llm, "ChatOpenAI", object())
    monkeypatch.setattr(llm, "ChatOpenAIWithReasoning", FakeOpenAI)
    moonshot_env = {
        "LANGCHAIN_PROVIDER": "moonshot",
        "LANGCHAIN_MODEL_NAME": "kimi-k2-thinking",
        "LANGCHAIN_TEMPERATURE": "0.2",
        "MOONSHOT_USER_AGENT": "   ",
    }
    with patch.dict(os.environ, moonshot_env, clear=True):
        llm.build_llm()
    assert captured["temperature"] == 1.0
    assert captured["default_headers"]["User-Agent"].startswith("Vibe-Trading/")
