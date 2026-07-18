"""Node 9 acceptance contracts: industry research is durable and evidence-led."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient
from uvicorn.logging import AccessFormatter

from src.api import industry_chain_routes as routes
from src.api.industry_chain_routes import _ingest
from src.industry_chain.refresh import IndustryChainRefreshService, QUEUE_NAME
from src.industry_chain.store import Chain, IndustryChainStore, Segment
from src.security.api_security import SecretRedactionFilter
from src.state.database import ConcurrentUpdateError, StateDatabase
from src.state.jobs import JobStatus


def _result(*, evidence_status: str = "active", conflict: bool = False) -> dict:
    conflicts = []
    if conflict:
        conflicts = [{
            "conflict_id": "conflict_optics",
            "subject_id": "seg_optics",
            "evidence_ids": ["ev_optics", "ev_other"],
            "description": "两份来源对供给集中度结论不一致",
            "status": "open",
        }]
    return {
        "schema_version": 1,
        "as_of": "2026-07-18T00:00:00+00:00",
        "overview": {
            "structure_summary": "高速互联环节受制于高端封装与客户认证周期。",
            "lifecycle_stage": "Validation",
            "prosperity_score": 72,
            "sector_score": 70,
            "evidence_ids": ["ev_overview"],
            "confidence": "medium",
        },
        "nodes": [{
            "node_id": "node_optics",
            "name": "光模块",
            "node_type": "component",
            "description": "高速光互联组件",
            "evidence_ids": ["ev_optics"],
            "confidence": "high",
        }],
        "edges": [],
        "segments": [{
            "segment_id": "seg_optics",
            "name": "光模块",
            "name_en": "Optical module",
            "order": 1,
            "positioning": "中游",
            "value_weight": "约 25%",
            "localization_rate": "约 40%",
            "international_competition": "海外厂商主导高端产品",
            "domestic_competition": "本土厂商追赶中",
            "barrier_type": "技术壁垒",
            "barrier_description": "封装良率与客户认证",
            "chokepoint_score": {"supply_concentration": 18},
            "chokepoint_total": 82,
            "evidence_ids": ["ev_optics"],
            "confidence": "medium",
            "tickers": [{
                "code": "300308.SZ",
                "name": "中际旭创",
                "market": "A",
                "score": 85,
                "tier": "Core",
                "classification": "Controller",
                "confidence": "high",
                "key_products": "800G 光模块",
                "red_team_note": "估值已反映部分增长预期",
                "evidence_ids": ["ev_optics"],
            }],
        }],
        "evidence": [
            {
                "evidence_id": "ev_optics",
                "claim": "高速光模块供给与竞争格局",
                "source_name": "已核验行业来源",
                "source_url": "https://example.com/optics",
                "as_of": "2026-07-18T00:00:00+00:00",
                "retrieved_at": "2026-07-18T00:00:00+00:00",
                "expires_at": "2026-10-18T00:00:00+00:00",
                "confidence": "high",
                "status": evidence_status,
            },
            {
                "evidence_id": "ev_overview",
                "claim": "链级景气与生命周期",
                "source_name": "已核验行业来源",
                "source_url": "https://example.com/overview",
                "as_of": "2026-07-18T00:00:00+00:00",
                "retrieved_at": "2026-07-18T00:00:00+00:00",
                "confidence": "medium",
                "status": "active",
            },
            {
                "evidence_id": "ev_other",
                "claim": "另一份集中度判断",
                "source_name": "第二来源",
                "source_url": "https://example.com/other",
                "as_of": "2026-07-18T00:00:00+00:00",
                "retrieved_at": "2026-07-18T00:00:00+00:00",
                "confidence": "low",
                "status": "active",
            },
        ],
        "conflicts": conflicts,
    }


def _fenced(payload: dict) -> str:
    import json

    return f"研究摘要\n```json\n{json.dumps(payload)}\n```"


def test_structured_ingest_rejects_prose_and_marks_stale_or_conflicting_evidence() -> None:
    chain = Chain(name="AI算力", segments=[Segment(name="光模块", segment_id="seg_optics")])

    assert not _ingest(chain, "光模块卡脖子评分 82，建议关注。")
    assert chain.overview.prosperity_score is None

    assert _ingest(chain, _fenced(_result(evidence_status="stale", conflict=True)))
    segment = chain.segments[0]
    assert segment.evidence_state == "stale"
    assert segment.status == "inconclusive"
    assert chain.overview.evidence_state == "supported"
    assert chain.research_version == 1
    assert chain.evidence[0]["as_of"]
    assert chain.conflicts[0]["status"] == "open"

    conflicting = Chain(name="AI算力")
    assert _ingest(conflicting, _fenced(_result(conflict=True)))
    assert conflicting.segments[0].evidence_state == "conflicting"

    missing = Chain(name="AI算力")
    assert _ingest(missing, _fenced(_result(evidence_status="missing")))
    assert missing.segments[0].evidence_state == "missing"


def test_store_uses_unified_state_optimistic_lock_and_immutable_history(tmp_path: Path) -> None:
    store = IndustryChainStore(root=tmp_path / "legacy")
    chain = store.save_chain(Chain(name="半导体"))
    assert store.database.get_record("industry_chain", chain.chain_id) is not None
    assert not (tmp_path / "legacy" / chain.chain_id / "chain.json").exists()

    writer_a = store.get_chain(chain.chain_id)
    writer_b = store.get_chain(chain.chain_id)
    assert writer_a is not None and writer_b is not None
    writer_a.description = "first"
    store.save_chain(writer_a)
    writer_b.description = "stale writer"
    with pytest.raises(ConcurrentUpdateError):
        store.save_chain(writer_b)

    first = store.append_history(chain.chain_id, {"prosperity_score": 50})
    second = store.append_history(chain.chain_id, {"prosperity_score": 60})
    history = store.load_history(chain.chain_id)
    assert [snapshot["snapshot_id"] for snapshot in history] == [first["snapshot_id"], second["snapshot_id"]]
    assert store.get_snapshot(chain.chain_id, first["snapshot_id"])["prosperity_score"] == 50


@dataclass
class _Clock:
    value: float = 0.0

    def __call__(self) -> float:
        return self.value


class _Runtime:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.started: list[dict[str, str]] = []
        self.cancelled: list[str] = []

    def start_run(self, preset: str, user_vars: dict[str, str]):
        if self.fail:
            raise RuntimeError("provider unavailable")
        self.started.append(user_vars)
        return SimpleNamespace(id=f"run-{len(self.started)}")

    def cancel_run(self, run_id: str) -> bool:
        self.cancelled.append(run_id)
        return True


def test_refresh_queue_is_idempotent_retries_cancels_and_recovers_after_restart(tmp_path: Path) -> None:
    clock = _Clock()
    database = StateDatabase(tmp_path / "state.db")
    store = IndustryChainStore(root=tmp_path / "legacy", database=database)
    chain = store.save_chain(Chain(name="机器人"))
    runtime = _Runtime()
    service = IndustryChainRefreshService(store, lambda: runtime, now_fn=clock)

    first = service.request_analysis(chain.chain_id, idempotency_key="same-request")
    duplicate = service.request_analysis(chain.chain_id, idempotency_key="same-request")
    assert first.job_id == duplicate.job_id
    assert service.get_job(first.job_id).status == JobStatus.COMPLETED
    assert len(runtime.started) == 1

    cancelled = service.queue.enqueue(
        QUEUE_NAME,
        {"chain_id": chain.chain_id, "market": "A", "trigger": "manual"},
        idempotency_key="cancel-me",
        concurrency_key=chain.chain_id,
    )
    assert service.cancel(cancelled.job_id)
    assert service.get_job(cancelled.job_id).status == JobStatus.CANCELLED

    current = store.get_chain(chain.chain_id)
    assert current is not None
    current.status = "error"
    store.save_chain(current)
    runtime.fail = True
    failed = service.request_analysis(chain.chain_id, idempotency_key="will-retry")
    assert service.get_job(failed.job_id).status == JobStatus.RETRY_WAIT
    runtime.fail = False
    clock.value = 2.0
    service.tick()
    assert service.get_job(failed.job_id).status == JobStatus.COMPLETED

    current = store.get_chain(chain.chain_id)
    assert current is not None
    current.status = "error"
    store.save_chain(current)
    stranded = service.queue.enqueue(
        QUEUE_NAME,
        {"chain_id": chain.chain_id, "market": "A", "trigger": "manual"},
        idempotency_key="restart-recovery",
        concurrency_key=chain.chain_id,
    )
    assert service.queue.claim(QUEUE_NAME, "dead-worker", lease_seconds=1.0) is not None
    clock.value = 4.0
    restarted = IndustryChainRefreshService(store, lambda: runtime, now_fn=clock)
    restarted.tick()
    assert restarted.get_job(stranded.job_id).status == JobStatus.COMPLETED


def test_routes_enforce_auth_support_cas_schedule_and_static_compare(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    store = IndustryChainStore(root=tmp_path / "legacy")
    monkeypatch.setattr(routes, "_store", store)
    runtime = _Runtime()
    app = FastAPI()

    async def require_auth(authorization: str | None = Header(default=None)) -> None:
        if authorization != "Bearer test-key":
            raise HTTPException(status_code=401, detail="unauthorized")

    routes.register_industry_chain_routes(app, require_auth, lambda: runtime)
    headers = {"Authorization": "Bearer test-key"}
    with TestClient(app) as client:
        assert client.get("/industry-chain/list").status_code == 401
        first = client.post("/industry-chain", headers=headers, json={"name": "AI算力", "segment_names": ["光模块"]})
        second = client.post("/industry-chain", headers=headers, json={"name": "半导体", "segment_names": ["设备"]})
        assert first.status_code == 200 and second.status_code == 200
        first_chain = first.json()["chain"]
        response = client.put(
            f"/industry-chain/{first_chain['chain_id']}",
            headers=headers,
            json={"description": "v1", "row_version": first_chain["row_version"]},
        )
        assert response.status_code == 200
        stale = client.put(
            f"/industry-chain/{first_chain['chain_id']}",
            headers=headers,
            json={"description": "stale", "row_version": first_chain["row_version"]},
        )
        assert stale.status_code == 409
        compared = client.get(
            f"/industry-chain/compare?ids={first_chain['chain_id']},{second.json()['chain_id']}",
            headers=headers,
        )
        assert compared.status_code == 200
        scheduled = client.put(
            f"/industry-chain/{first_chain['chain_id']}/schedule",
            headers=headers,
            json={"schedule": "weekly", "row_version": response.json()["chain"]["row_version"]},
        )
        assert scheduled.status_code == 200
        assert scheduled.json()["schedule"]["next_due_at"] is not None


def test_access_log_redaction_preserves_uvicorn_structured_arguments() -> None:
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:9999", "GET", "/healthz?api_key=super-secret", "1.1", 200),
        None,
    )

    assert SecretRedactionFilter().filter(record)
    rendered = AccessFormatter(
        fmt='%(client_addr)s - "%(request_line)s" %(status_code)s', use_colors=False
    ).format(record)

    assert "super-secret" not in rendered
    assert "[redacted]" in rendered
    assert "GET /healthz?api_key=[redacted] HTTP/1.1" in rendered
