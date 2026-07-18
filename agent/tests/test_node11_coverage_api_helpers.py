"""Branch-focused API security, settings, artifact, and upload contracts."""

from __future__ import annotations

import ipaddress
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from starlette.requests import Request
from starlette.responses import Response

import api_server as api


pytestmark = pytest.mark.unit


def _request(
    path: str = "/api/v1/test",
    *,
    method: str = "GET",
    client: str | None = "127.0.0.1",
    headers: dict[str, str] | None = None,
    query: str = "",
) -> Request:
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": query.encode(),
        "headers": [(key.lower().encode(), value.encode()) for key, value in (headers or {}).items()],
        "client": (client, 12345) if client is not None else None,
        "server": ("testserver", 80),
    }
    return Request(scope)


def _cred(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def test_cors_and_host_parsing_contracts(monkeypatch: pytest.MonkeyPatch) -> None:
    assert api._parse_cors_origins(None) == api._DEFAULT_CORS_ORIGINS
    assert api._parse_cors_origins("  ") == api._DEFAULT_CORS_ORIGINS
    assert api._parse_cors_origins("https://a.test, ,https://b.test") == [
        "https://a.test",
        "https://b.test",
    ]
    with pytest.raises(RuntimeError, match="not allowed"):
        api._parse_cors_origins("https://a.test,*")

    assert api._parse_extra_loopback_hosts(None) == set()
    assert api._parse_extra_loopback_hosts(" DEV.local., 127.0.0.2 ") == {
        "dev.local",
        "127.0.0.2",
    }
    assert api._host_without_port("") == ""
    assert api._host_without_port("LOCALHOST.:8000") == "localhost."
    assert api._host_without_port("[::1]:8899") == "[::1]"
    assert api._host_without_port("[broken") == "[broken"
    assert api._host_without_port("2001:db8::1") == "2001:db8::1"
    monkeypatch.setattr(api, "_EXTRA_LOOPBACK_HOSTS", {"dev.local"})
    assert api._is_allowed_loopback_host("dev.local:8899")
    assert not api._is_allowed_loopback_host("evil.test")


@pytest.mark.parametrize(
    "origin,expected",
    [
        ("http://localhost:5173", True),
        ("https://127.0.0.1", True),
        ("http://[::1]", True),
        ("ftp://localhost", False),
        ("http://example.com", False),
        ("not a url", False),
        ("http://[broken", False),
    ],
)
def test_loopback_origin_detection(origin, expected) -> None:
    assert api._is_loopback_origin(origin) is expected


def test_auth_credential_and_cross_site_contracts() -> None:
    assert api._auth_credential_from_header_or_query(_cred("header"), "query", allow_query=True) == "header"
    assert api._auth_credential_from_header_or_query(None, "query", allow_query=True) == "query"
    assert api._auth_credential_from_header_or_query(None, "query", allow_query=False) == ""
    api._reject_cross_site_browser_request(_request(method="POST"))
    api._reject_cross_site_browser_request(
        _request(method="POST", headers={"origin": "http://localhost:5173"})
    )
    for headers in (
        {"sec-fetch-site": "cross-site"},
        {"origin": "https://evil.test"},
    ):
        with pytest.raises(HTTPException) as exc:
            api._reject_cross_site_browser_request(_request(method="POST", headers=headers))
        assert exc.value.status_code == 403


def test_validate_and_shutdown_auth_branches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api, "_configured_api_key", lambda: "secret")
    api._validate_api_auth(request=_request(), cred=_cred("secret"))
    api._validate_api_auth(
        request=_request(), cred=None, query_api_key="secret", allow_query=True
    )
    for credential in (None, _cred("wrong")):
        with pytest.raises(HTTPException) as exc:
            api._validate_api_auth(request=_request(), cred=credential)
        assert exc.value.status_code == 401
    api._require_shutdown_authorization(request=_request(method="POST"), cred=_cred("secret"))
    with pytest.raises(HTTPException) as exc:
        api._require_shutdown_authorization(request=_request(method="POST"), cred=None)
    assert exc.value.status_code == 401

    monkeypatch.setattr(api, "_configured_api_key", lambda: "")
    api._require_shutdown_authorization(request=_request(method="POST"), cred=None)
    with pytest.raises(HTTPException) as exc:
        api._require_shutdown_authorization(
            request=_request(method="POST", client="203.0.113.5"), cred=None
        )
    assert exc.value.status_code == 403


@pytest.mark.parametrize(
    "client,expected",
    [
        (None, False),
        ("localhost", True),
        ("testclient", True),
        ("127.0.0.1", True),
        ("::1", True),
        ("bad-host", False),
        ("203.0.113.10", False),
    ],
)
def test_local_client_detection(client, expected, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api, "_trusted_docker_loopback_ip", lambda ip: False)
    assert api._is_local_client(_request(client=client)) is expected


def test_env_gateway_and_docker_trust_branches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NODE11_FLAG", " yes ")
    assert api._env_flag_enabled("NODE11_FLAG")
    monkeypatch.setenv("NODE11_FLAG", "off")
    assert not api._env_flag_enabled("NODE11_FLAG")

    original = Path.read_text

    def route_text(path: Path, *args, **kwargs):
        if str(path) == "/proc/net/route":
            return (
                "Iface Destination Gateway Flags\n"
                "eth0 00000000 010011AC 0003\n"
                "eth0 BAD 010011AC 0003\n"
                "short\n"
                "eth0 00000000 nope 0003\n"
            )
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", route_text)
    assert ipaddress.IPv4Address("172.17.0.1") in api._default_gateway_ips()
    monkeypatch.setattr(Path, "read_text", lambda *args, **kwargs: (_ for _ in ()).throw(OSError()))
    assert api._default_gateway_ips() == set()

    assert not api._trusted_docker_loopback_ip(ipaddress.IPv6Address("::1"))
    monkeypatch.delenv(api._DOCKER_LOOPBACK_ENV, raising=False)
    assert not api._trusted_docker_loopback_ip(ipaddress.IPv4Address("172.17.0.1"))
    monkeypatch.setenv(api._DOCKER_LOOPBACK_ENV, "1")
    monkeypatch.setattr(api, "_default_gateway_ips", lambda: {ipaddress.IPv4Address("172.17.0.1")})
    assert api._trusted_docker_loopback_ip(ipaddress.IPv4Address("172.17.0.1"))


def test_request_classification_bearer_rate_and_security_headers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "assets" / "app.js").write_text("ok", encoding="utf-8")
    (dist / "index.html").write_text("spa", encoding="utf-8")
    monkeypatch.setattr(api, "_FRONTEND_DIST", dist)
    assert api._is_public_static_request(_request("/assets/app.js"))
    assert not api._is_public_static_request(_request("/assets/missing.js"))
    assert not api._is_public_static_request(_request("/assets/../secret"))
    assert not api._is_public_static_request(_request("/assets/app.js", method="POST"))
    assert api._is_public_static_request(_request("/"))
    assert api._is_public_static_request(
        _request("/runs/run-1", headers={"accept": "text/html"})
    )
    assert api._is_anonymous_request(_request("/anything", method="OPTIONS"))
    assert api._is_anonymous_request(_request("/healthz"))
    assert not api._is_anonymous_request(_request("/private"))
    assert api._bearer_token(_request(headers={"authorization": "Bearer token"})) == "token"
    assert api._bearer_token(_request(headers={"authorization": "Basic token"})) == ""

    monkeypatch.setenv("VIBE_TRADING_RATE_LIMIT_PER_MINUTE", "0")
    assert api._rate_limit() == 1
    monkeypatch.setenv("VIBE_TRADING_RATE_LIMIT_PER_MINUTE", "999999")
    assert api._rate_limit() == 100_000
    monkeypatch.setenv("VIBE_TRADING_RATE_LIMIT_PER_MINUTE", "bad")
    assert api._rate_limit() == 600
    response = api._secure_response(Response(media_type="text/event-stream"), "request-1")
    assert response.headers["X-Request-ID"] == "request-1"
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Frame-Options"] == "DENY"


@pytest.mark.parametrize(
    "path,expected",
    [
        ("/", True),
        ("/correlation", True),
        ("/runs/r1", True),
        ("/runs/r1/", True),
        ("/runs/r1/code", False),
        ("/ml-training/models", True),
        ("/industry-chain/abcdef123456", True),
        ("/industry-chain/not-an-id", False),
    ],
)
def test_spa_route_classifier(path, expected) -> None:
    assert api._is_spa_html_route(path) is expected


def test_api_error_envelopes_and_path_validation() -> None:
    v1 = _request("/api/v1/test", headers={"x-request-id": "request-1"})
    response = api._api_error_response(v1, 422, {"field": "bad"})
    body = json.loads(response.body)
    assert body["request_id"] == "request-1"
    assert body["details"] == {"field": "bad"}
    legacy = api._api_error_response(_request("/legacy"), 400, "bad")
    assert json.loads(legacy.body) == {"detail": "bad"}
    api._validate_path_param("safe_id-1", "run_id")
    with pytest.raises(HTTPException) as exc:
        api._validate_path_param("../escape", "run_id")
    assert exc.value.status_code == 400


def test_provider_config_validation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "providers.json"
    monkeypatch.setattr(api, "LLM_PROVIDER_CONFIG_PATH", path)
    path.write_text("not-json", encoding="utf-8")
    with pytest.raises(RuntimeError, match="Failed to load"):
        api._load_llm_providers()
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(RuntimeError, match="must not be empty"):
        api._load_llm_providers()
    item = api.LLM_PROVIDERS[0].model_dump(mode="json")
    path.write_text(json.dumps([item, item]), encoding="utf-8")
    with pytest.raises(RuntimeError, match="Duplicate"):
        api._load_llm_providers()
    path.write_text(json.dumps([item]), encoding="utf-8")
    assert api._load_llm_providers()[0].name == item["name"]


def test_dotenv_read_write_format_and_path_helpers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = tmp_path / ".env"
    example = tmp_path / ".env.example"
    monkeypatch.setattr(api, "ENV_PATH", env)
    monkeypatch.setattr(api, "ENV_EXAMPLE_PATH", example)
    monkeypatch.setattr(api, "AGENT_DIR", tmp_path / "agent")
    assert api._read_env_values(env) == {}
    example.write_text("A='one' # note\n# B=old\nINVALID\n =empty\n", encoding="utf-8")
    assert api._read_settings_env_values() == {"A": "one"}
    api._ensure_agent_env_file()
    env.write_text("# A=old\nB=keep\n", encoding="utf-8")
    api._write_env_values(env, {"A": "new value", "C": "hash#value"})
    values = api._read_env_values(env)
    assert values == {"A": "new value", "B": "keep", "C": "hash#value"}
    assert api._format_env_value("") == ""
    assert api._format_env_value("plain") == "plain"
    assert api._format_env_value('a "quote"') == '"a \\"quote\\""'
    with pytest.raises(HTTPException):
        api._format_env_value("line1\nline2")
    assert api._project_relative_path(tmp_path / "agent" / ".env") == "agent/.env"
    assert api._project_relative_path(Path("/outside/secret")) == "secret"


def test_secret_coercion_and_settings_response_branches(monkeypatch: pytest.MonkeyPatch) -> None:
    assert not api._is_configured_secret(" 'xxx' ", {"xxx"})
    assert api._is_configured_secret("real", {"xxx"})
    assert api._coerce_float("1.5", 0.0) == 1.5
    assert api._coerce_float("bad", 2.0) == 2.0
    assert api._coerce_int("3", 0) == 3
    assert api._coerce_int("bad", 4) == 4
    settings = api._build_llm_settings_response(
        {
            "LANGCHAIN_PROVIDER": "missing",
            "LANGCHAIN_TEMPERATURE": "bad",
            "TIMEOUT_SECONDS": "bad",
            "MAX_RETRIES": "bad",
        }
    )
    assert settings.provider == "openai"
    oauth = next((provider for provider in api.LLM_PROVIDERS if provider.auth_type == "oauth"), None)
    if oauth is not None:
        monkeypatch.setattr(
            "src.providers.openai_codex.get_openai_codex_login_status", lambda: {"logged_in": True}
        )
        assert api._build_llm_settings_response({"LANGCHAIN_PROVIDER": oauth.name}).api_key_configured
        monkeypatch.setattr(
            "src.providers.openai_codex.get_openai_codex_login_status",
            lambda: (_ for _ in ()).throw(RuntimeError("offline")),
        )
        assert not api._build_llm_settings_response({"LANGCHAIN_PROVIDER": oauth.name}).api_key_configured


@pytest.mark.parametrize(
    "supported,installed,message",
    [
        (True, True, "loader is available"),
        (False, True, "package is installed"),
        (False, False, "No BaoStock loader"),
    ],
)
def test_data_source_settings_messages(supported, installed, message, monkeypatch) -> None:
    monkeypatch.setattr(api, "_baostock_supported", lambda: supported)
    monkeypatch.setattr(api, "_baostock_installed", lambda: installed)
    response = api._build_data_source_settings_response({"TUSHARE_TOKEN": "real"})
    assert response.tushare_token_configured
    assert message in response.baostock_message


def test_runtime_environment_sync_branches(monkeypatch: pytest.MonkeyPatch) -> None:
    api_key_provider = next(provider for provider in api.LLM_PROVIDERS if provider.api_key_env)
    monkeypatch.setenv("OPENAI_API_KEY", "old")
    api._sync_runtime_env(
        api_key_provider,
        {
            api_key_provider.api_key_env: "real-key",
            api_key_provider.base_url_env: "https://api.test",
            "REMOVE_ME": "",
        },
    )
    assert __import__("os").environ["OPENAI_API_KEY"] == "real-key"
    assert __import__("os").environ["OPENAI_BASE_URL"] == "https://api.test"
    api._sync_runtime_env(api_key_provider, {api_key_provider.api_key_env: ""})
    assert "OPENAI_API_KEY" not in __import__("os").environ

    oauth = next((provider for provider in api.LLM_PROVIDERS if provider.auth_type == "oauth"), None)
    if oauth:
        api._sync_runtime_env(oauth, {oauth.base_url_env: ""})
        assert "OPENAI_API_BASE" not in __import__("os").environ
    local = SimpleNamespace(api_key_env=None, auth_type="none", base_url_env="LOCAL_URL")
    api._sync_runtime_env(local, {"LOCAL_URL": ""})
    assert __import__("os").environ["OPENAI_API_KEY"] == "ollama"


def test_json_csv_and_run_response_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    missing = tmp_path / "missing"
    assert api._load_json_file(missing) is None
    assert api._load_csv_to_dict(missing) == []
    bad = tmp_path / "bad.json"
    bad.write_text("bad", encoding="utf-8")
    assert api._load_json_file(bad) is None
    csv_path = tmp_path / "rows.csv"
    csv_path.write_text("a,b\n1,2\n3,4\n", encoding="utf-8")
    assert api._load_csv_to_dict(csv_path, limit=1) == [{"a": "1", "b": "2"}]

    run = tmp_path / "run-1"
    artifacts = run / "artifacts"
    artifacts.mkdir(parents=True)
    (run / "state.json").write_text('{"status":"failed","reason":"boom"}', encoding="utf-8")
    (run / "planner_output.json").write_text('{"plan":1}', encoding="utf-8")
    (run / "design_spec.json").write_text('{"strategy":1}', encoding="utf-8")
    (run / "rag_metadata.json").write_text(
        '{"api_code":"daily","api_name":"Daily","score":"0.9"}', encoding="utf-8"
    )
    (artifacts / "metrics.csv").write_text(
        "final_value,total_return,annual_return,max_drawdown,sharpe,win_rate,trade_count,bad\n"
        "1100,0.1,0.2,-0.05,1.2,0.6,3,text\n",
        encoding="utf-8",
    )
    (artifacts / "equity.csv").write_text(
        "timestamp,equity,drawdown,ignored\n2025-01-01,1000,0,x\n", encoding="utf-8"
    )
    (artifacts / "trades.csv").write_text("symbol,pnl\nAAPL,10\n", encoding="utf-8")
    (artifacts / "validation.json").write_text('{"valid":true}', encoding="utf-8")
    (run / "run_card.json").write_text("bad", encoding="utf-8")
    (run / "llm_usage.json").write_text('{"tokens":10}', encoding="utf-8")
    response = api._build_response_from_run_dir(run, 1.5)
    assert response.status == "failed" and response.reason == "boom"
    assert response.metrics and response.metrics.final_value == 1100
    assert response.equity_curve == [{"time": "2025-01-01", "equity": "1000", "drawdown": "0"}]
    assert response.trade_log == [{"symbol": "AAPL", "pnl": "10"}]
    assert response.validation == {"valid": True}
    assert api._run_response_payload(response)["run_id"] == "run-1"

    monkeypatch.setattr(
        api,
        "build_run_analysis",
        lambda *args, **kwargs: {
            "chart_symbols": ["AAPL"],
            "run_stage": "complete",
            "run_context": {},
            "price_series": [],
            "indicator_series": [],
            "trade_markers": [],
            "run_logs": [],
        },
    )
    symbols: list[str] = []
    analyzed = api._build_response_from_run_dir(
        run,
        1.5,
        include_analysis=True,
        chart_symbol="AAPL",
        chart_payload="summary",
        chart_symbols_out=symbols,
    )
    assert analyzed.run_stage == "complete" and symbols == ["AAPL"]


@pytest.mark.parametrize(
    "filename,content_type,prefix,valid",
    [
        ("report.txt", "text/plain", b"hello", True),
        ("data.json", "application/json", b"{}", True),
        ("book.xlsx", "application/octet-stream", b"PK\x03\x04rest", True),
        ("image.webp", "image/webp", b"RIFF1234WEBP", True),
        ("image.png", "image/png", b"\x89PNG\r\n\x1a\n", True),
        ("script.sh", "text/plain", b"echo", False),
        ("README", "text/plain", b"hello", False),
        ("report.txt", "text/plain", b"MZbinary", False),
        ("report.txt", "text/plain", b"a\x00b", False),
        ("report.txt", "text/plain", b"\xff", False),
        ("report.txt", "image/png", b"hello", False),
        ("book.xlsx", None, b"bad", False),
        ("image.webp", None, b"bad", False),
        ("archive.zip", None, b"PK\x03\x04", False),
        ("image.png", None, b"bad", False),
    ],
)
def test_upload_content_validation_matrix(filename, content_type, prefix, valid) -> None:
    if valid:
        api._validate_upload_content(filename, content_type, prefix)
    else:
        with pytest.raises(HTTPException) as exc:
            api._validate_upload_content(filename, content_type, prefix)
        assert exc.value.status_code == 400


def test_permission_matrix_has_anonymous_and_bearer_routes() -> None:
    matrix = api.build_api_permission_matrix()
    health = next(row for row in matrix if row["path"] == "/healthz")
    private = next(row for row in matrix if row["path"] == "/metrics")
    assert health["access"] == "anonymous"
    assert private["access"] == "bearer"
