"""Extended API endpoint error semantics and live/SSE adapter branches."""

from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks, HTTPException, UploadFile
from starlette.requests import Request

import api_server as api


pytestmark = pytest.mark.unit
_SESSION_ID = "a1b2c3d4e5f6"


def _request(
    path: str = "/api/v1/test",
    *,
    method: str = "GET",
    client: str | None = "127.0.0.1",
    headers: dict[str, str] | None = None,
) -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": [
                (key.lower().encode(), value.encode())
                for key, value in (headers or {}).items()
            ],
            "client": (client, 12345) if client is not None else None,
            "server": ("testserver", 80),
        }
    )


def test_llm_settings_validation_and_update_branches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = dict(provider="openai", model_name="gpt", base_url="https://api.example.com")
    cases = [
        ({**base, "provider": "missing"}, "Unsupported"),
        ({**base, "model_name": "   "}, "Model name"),
        ({**base, "temperature": -1}, "Temperature"),
        ({**base, "temperature": 3}, "Temperature"),
        ({**base, "reasoning_effort": "extreme"}, "Reasoning effort"),
    ]
    for values, match in cases:
        with pytest.raises(HTTPException, match=match):
            asyncio.run(api.update_llm_settings(api.UpdateLLMSettingsRequest(**values)))

    env = tmp_path / ".env"
    env.write_text("OPENAI_API_KEY=existing\nLANGCHAIN_REASONING_EFFORT=low\n", encoding="utf-8")
    monkeypatch.setattr(api, "ENV_PATH", env)
    monkeypatch.setattr(api, "_read_settings_env_values", lambda: api._read_env_values(env))
    monkeypatch.setattr(api, "validate_outbound_url", lambda value, **kwargs: value)
    monkeypatch.setattr(api, "_sync_runtime_env", lambda provider, updates: None)
    response = asyncio.run(
        api.update_llm_settings(
            api.UpdateLLMSettingsRequest(
                **base, api_key="new-key", reasoning_effort="high"
            )
        )
    )
    assert response.provider == "openai" and response.reasoning_effort == "high"
    response = asyncio.run(
        api.update_llm_settings(
            api.UpdateLLMSettingsRequest(**base, clear_api_key=True)
        )
    )
    assert not response.api_key_configured

    monkeypatch.setattr(
        api,
        "validate_outbound_url",
        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("blocked URL")),
    )
    with pytest.raises(HTTPException, match="blocked URL"):
        asyncio.run(api.update_llm_settings(api.UpdateLLMSettingsRequest(**base)))


def test_data_source_settings_clear_keep_and_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = tmp_path / ".env"
    env.write_text("TUSHARE_TOKEN=existing\n", encoding="utf-8")
    monkeypatch.setattr(api, "ENV_PATH", env)
    monkeypatch.setattr(api, "_read_settings_env_values", lambda: api._read_env_values(env))
    asyncio.run(api.update_data_source_settings(api.UpdateDataSourceSettingsRequest()))
    assert __import__("os").environ["TUSHARE_TOKEN"] == "existing"
    asyncio.run(
        api.update_data_source_settings(
            api.UpdateDataSourceSettingsRequest(tushare_token="new")
        )
    )
    assert __import__("os").environ["TUSHARE_TOKEN"] == "new"
    asyncio.run(
        api.update_data_source_settings(
            api.UpdateDataSourceSettingsRequest(clear_tushare_token=True)
        )
    )
    assert "TUSHARE_TOKEN" not in __import__("os").environ


def test_correlation_validation_success_and_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backtest import correlation

    for codes, method, match in (
        ("AAPL", "pearson", "At least"),
        (",".join(f"S{i}" for i in range(21)), "pearson", "Maximum"),
        ("A,B", "kendall", "method"),
    ):
        with pytest.raises(HTTPException, match=match):
            asyncio.run(api.get_correlation_matrix(codes, 30, method))
    monkeypatch.setattr(correlation, "compute_correlation_matrix", lambda **kwargs: {"ok": True})
    assert asyncio.run(api.get_correlation_matrix("A,B", 30, "spearman")) == {
        "ok": True
    }
    monkeypatch.setattr(
        correlation,
        "compute_correlation_matrix",
        lambda **kwargs: (_ for _ in ()).throw(ValueError("bad data")),
    )
    with pytest.raises(HTTPException) as exc:
        asyncio.run(api.get_correlation_matrix("A,B", 30, "pearson"))
    assert exc.value.status_code == 400
    monkeypatch.setattr(
        correlation,
        "compute_correlation_matrix",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("down")),
    )
    with pytest.raises(HTTPException) as exc:
        asyncio.run(api.get_correlation_matrix("A,B", 30, "pearson"))
    assert exc.value.status_code == 500


def test_shutdown_and_session_disabled_or_missing_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(api, "_require_shutdown_authorization", lambda **kwargs: None)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            api.shutdown_local_api(
                BackgroundTasks(), _request(client="203.0.113.1", method="POST"), None
            )
        )
    assert exc.value.status_code == 403
    result = asyncio.run(
        api.shutdown_local_api(
            BackgroundTasks(), _request(client="127.0.0.1", method="POST"), None
        )
    )
    assert result["status"] == "shutting-down"

    monkeypatch.setattr(api, "_get_session_service", lambda: None)
    for call in (
        lambda: api._get_existing_session_or_404("s1"),
        lambda: api.get_session(_SESSION_ID),
        lambda: api.delete_session(_SESSION_ID),
        lambda: api.cancel_session(_SESSION_ID),
        lambda: api.get_messages(_SESSION_ID, 10),
    ):
        with pytest.raises(HTTPException) as disabled:
            result = call()
            if hasattr(result, "__await__"):
                asyncio.run(result)
        assert disabled.value.status_code == 501

    missing_service = SimpleNamespace(get_session=lambda session_id: None)
    monkeypatch.setattr(api, "_get_session_service", lambda: missing_service)
    with pytest.raises(HTTPException) as missing:
        api._get_existing_session_or_404(_SESSION_ID)
    assert missing.value.status_code == 404
    with pytest.raises(HTTPException) as missing:
        asyncio.run(api.get_session(_SESSION_ID))
    assert missing.value.status_code == 404


def test_session_cancel_delete_messages_and_send_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message = SimpleNamespace(
        message_id="m1", session_id=_SESSION_ID, role="user", content="hello",
        created_at="now", linked_attempt_id=None, metadata={},
    )
    service = SimpleNamespace(
        cancel_current=lambda session_id: False,
        delete_session=lambda session_id: False,
        get_messages=lambda session_id, limit: [message],
        send_message=lambda **kwargs: None,
    )
    monkeypatch.setattr(api, "_get_session_service", lambda: service)
    assert asyncio.run(api.cancel_session(_SESSION_ID)) == {"status": "no_active_loop"}
    service.cancel_current = lambda session_id: True
    assert asyncio.run(api.cancel_session(_SESSION_ID)) == {"status": "cancelled"}
    with pytest.raises(HTTPException) as exc:
        asyncio.run(api.delete_session(_SESSION_ID))
    assert exc.value.status_code == 404
    assert asyncio.run(api.get_messages(_SESSION_ID, 10))[0].metadata is None

    async def bad_send(**kwargs):
        raise ValueError("missing")

    service.send_message = bad_send
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            api.send_message(
                _SESSION_ID, SimpleNamespace(content="hello"), _request(method="POST")
            )
        )
    assert exc.value.status_code == 404


def test_upload_success_size_limit_and_missing_filename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(api, "UPLOADS_DIR", tmp_path)
    with pytest.raises(HTTPException, match="Missing filename"):
        asyncio.run(api.upload_file(UploadFile(io.BytesIO(b"x"), filename="")))
    upload = UploadFile(io.BytesIO(b"hello"), filename="note.txt", headers={"content-type": "text/plain"})
    result = asyncio.run(api.upload_file(upload))
    assert result["status"] == "ok" and list(tmp_path.glob("*.txt"))
    monkeypatch.setattr(api, "MAX_UPLOAD_SIZE", 3)
    too_big = UploadFile(io.BytesIO(b"hello"), filename="large.txt", headers={"content-type": "text/plain"})
    with pytest.raises(HTTPException) as exc:
        asyncio.run(api.upload_file(too_big))
    assert exc.value.status_code == 413


def test_live_event_frames_and_broker_ceiling_matrix(monkeypatch: pytest.MonkeyPatch) -> None:
    proposal_id = "mp_" + "a" * 32
    event = SimpleNamespace(event_type="tool_result", data={}, session_id="s1")
    assert api._mandate_proposal_frame_from_tool_result(event) is None
    event.data = {"tool": api._PROPOSAL_TOOL_NAME, "status": "ok", "preview": "none"}
    assert api._mandate_proposal_frame_from_tool_result(event) is None
    event.data["preview"] = json.dumps({"proposal_id": proposal_id})
    monkeypatch.setattr(api, "_load_full_proposal", lambda proposal_id: None)
    assert api._mandate_proposal_frame_from_tool_result(event) is None
    monkeypatch.setattr(
        api,
        "_load_full_proposal",
        lambda proposal_id: {"type": "mandate.proposal", "proposal_id": proposal_id},
    )
    assert "event: mandate.proposal" in api._mandate_proposal_frame_from_tool_result(event)

    action = SimpleNamespace(event_type="tool_result", data={"preview": "none"}, session_id="s1")
    assert api._live_action_frame_from_tool_result(action) is None
    action.data["preview"] = '{"live_action":{},"audit_id":"la_abc"}'
    monkeypatch.setattr(api, "_load_live_action_record", lambda audit_id: None)
    assert api._live_action_frame_from_tool_result(action) is None
    monkeypatch.setattr(api, "_load_live_action_record", lambda audit_id: {"audit_id": audit_id})
    assert "event: live.action" in api._live_action_frame_from_tool_result(action)

    monkeypatch.setattr(
        api,
        "_live_broker_adapter",
        lambda broker: SimpleNamespace(call_tool=lambda *args: {"status": "error"}),
    )
    assert api._fetch_broker_ceilings("broker") is None
    monkeypatch.setattr(
        api,
        "_live_broker_adapter",
        lambda broker: SimpleNamespace(
            call_tool=lambda *args: {"result": {"buying_power": "bad", "cash": "100"}}
        ),
    )
    assert api._fetch_broker_ceilings("broker")["account_funding_usd"] == 100
    monkeypatch.setattr(
        api,
        "_live_broker_adapter",
        lambda broker: (_ for _ in ()).throw(api.LiveRunnerUnavailable("missing")),
    )
    assert api._fetch_broker_ceilings("broker") is None


def test_swarm_endpoint_error_and_success_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    status = SimpleNamespace(value="completed")
    run = SimpleNamespace(
        id="r1", status=status, preset_name="p", user_vars={}, created_at="now",
        completed_at="later", tasks=[], agents=[], final_report="done",
    )

    class Runtime:
        def __init__(self):
            self.mode = "ok"
            self._store = SimpleNamespace(
                list_runs=lambda limit: [run], reconcile_run=lambda item, write: item,
                is_run_stale=lambda item: False, load_run=lambda run_id: run if run_id == "r1" else None,
            )

        def start_run(self, *args, **kwargs):
            if self.mode == "missing":
                raise FileNotFoundError("preset")
            if self.mode == "bad":
                raise ValueError("dag")
            return run

        def cancel_run(self, run_id):
            return run_id == "r1"

    runtime = Runtime()
    monkeypatch.setattr(api, "_get_swarm_runtime", lambda: runtime)
    request = _request(method="POST")
    assert asyncio.run(api.create_swarm_run({"preset_name": "p"}, request))["id"] == "r1"
    for mode, code in (("missing", 404), ("bad", 400)):
        runtime.mode = mode
        with pytest.raises(HTTPException) as exc:
            asyncio.run(api.create_swarm_run({"preset_name": "p"}, request))
        assert exc.value.status_code == code
    runtime.mode = "ok"
    assert asyncio.run(api.list_swarm_runs(10))[0]["completed_count"] == 0
    assert asyncio.run(api.get_swarm_run("r1"))["id"] == "r1"
    with pytest.raises(HTTPException):
        asyncio.run(api.get_swarm_run("missing"))
    assert asyncio.run(api.cancel_swarm_run("r1")) == {"status": "cancelled"}
    with pytest.raises(HTTPException):
        asyncio.run(api.cancel_swarm_run("missing"))
