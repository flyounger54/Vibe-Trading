"""Compatibility-parser and HTTP lifecycle coverage for industry research."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import industry_chain_routes as routes
from src.industry_chain.store import Chain, IndustryChainStore, Segment, Ticker


pytestmark = pytest.mark.unit


def test_markdown_extraction_helpers_and_rich_compatibility_report() -> None:
    assert routes._clean_seg_name(" | **光模块** ") == "光模块"
    assert routes._strip_ptags("约 30% [P2 估算]") == "约 30%"
    assert routes._extract_dim_score("| 供给集中度 | 22 | **18** |", 22) == 18
    assert routes._extract_dim_score("| 供给集中度 | 22 | 15 |", 22) == 15
    assert routes._extract_dim_score("| 供给集中度 | 22 | 0 |", 22) is None
    assert routes._extract_total_score("总分：68/100") == 68
    assert routes._extract_total_score("| Chokepoint总分 | 100 | **81** |") == 81
    assert routes._extract_total_score("| 总分 | **75** | 100 |") == 75
    assert routes._extract_total_score("总分：0/100") is None
    assert routes._extract_barrier_type("barrier_type: 技术壁垒+认证壁垒") == "技术壁垒+认证壁垒"
    assert routes._extract_barrier_type("壁垒类型：🔬 技术壁垒") == "技术壁垒"
    assert routes._extract_barrier_type("nothing") == ""

    chain = Chain(name="AI算力", segments=[Segment(name="光模块"), Segment(name="算力芯片")])
    director = """
行业处于 **Validation** 阶段
执行摘要
高速互联与算力芯片环节同时受高端制造、认证周期和海外供给约束，产业格局仍在快速验证。

| 光模块 | 90 | -8 | 82 | Core | 估值和客户集中风险 |
| 公司 | 代码 | 段 | 调整后评分 | 级别 | 分类 | 关键风险 |
| 中际旭创 | 300308.SZ | 光模块 | 85 | Core | Controller | 估值偏高 |
| 🥈 | 688256.SH | 寒武纪 | 算力芯片 | Build | Integrator | 盈利验证 |
"""
    report = director + """
繁荣度评分 72/100
| 光模块 | 18 | 17 | 12 | 13 | 10 | 8 | 78 | 技术壁垒 |
### 3.1 算力芯片 — 总分：76/100
| 供给集中度 | 22 | **18** |
| 不可替代性 | 22 | **17** |
| 供需缺口 | 16 | **12** |
| 认证壁垒 | 16 | **13** |
barrier_type: 技术壁垒+认证壁垒

| 光模块 | 中游 | 约 25% [P2 估算] | 约 40% [P2 估算] |
## 一、光模块（Optical Module）
**定位**：中游
**价值权重**：约 25% —
**国产化率**：约 40% —
海外厂商主导高端产品。
| 新易盛 | 300502.SZ | Controller | 800G 产品 [P2 估算] |
| 天孚通信 | 300394.SZ | Controller | 光器件 |
| 光迅科技 | 002281.SZ | Controller | 光芯片 |

## 二、算力芯片（Compute Chip）
**定位**：上游
**价值权重**：约 30% —
**国产化进展**：约 15% —
严重依赖进口先进制程。
| 海光信息 | 688041.SH | Controller | CPU |
"""
    parsed = routes._extract_from_markdown(report, chain, director)
    assert parsed is not None
    assert parsed["lifecycle_stage"] == "Validation"
    assert parsed["prosperity_score"] == 72
    assert len(parsed["segments"]) == 2
    by_name = {row["name"]: row for row in parsed["segments"]}
    assert by_name["光模块"]["chokepoint_total"] == 82
    assert by_name["光模块"]["name_en"] == "Optical Module"
    assert by_name["光模块"]["domestic_competition"].startswith("国内龙头")
    assert by_name["算力芯片"]["chokepoint_total"] == 76
    assert routes._extract_from_markdown("", chain) is None
    assert routes._extract_from_markdown("plain text", Chain(name="empty")) is None


def test_segment_merge_numeric_snapshot_difference_and_markdown_export() -> None:
    segment = Segment(
        name="光模块",
        barrier_description="verified",
        tickers=[Ticker(code="300308.SZ", name="", evidence_state="supported")],
    )
    routes._apply_segment(segment, {
        "name_en": "Optical Module",
        "positioning": "中游",
        "barrier_description": "new",
        "chokepoint_score": {"supply": "18", "bad": "x"},
        "chokepoint_total": "82",
        "tickers": [
            {"code": "300308.SZ", "name": "中际旭创", "score": "85", "tier": "Core"},
            {"code": "300502.SZ", "name": "新易盛", "score": 80, "tier": "Build"},
            "invalid",
        ],
    })
    assert segment.barrier_description == "verified"
    assert segment.chokepoint_score == {"supply": 18.0}
    assert segment.tickers[0].name == "中际旭创"
    assert len(segment.tickers) == 2
    assert routes._num("bad") is None
    assert routes._difference(10, 15) == 5
    assert routes._difference(None, 15) is None

    chain = Chain(name="AI算力", segments=[segment])
    chain.overview.lifecycle_stage = "Validation"
    chain.overview.prosperity_score = 72
    chain.overview.sector_score = 70
    chain.overview.structure_summary = "结构摘要"
    chain.overview.evidence_state = "supported"
    chain.overview.core_targets = list(segment.tickers)
    chain.as_of = "2026-07-18"
    chain.evidence = [{
        "evidence_id": "ev-1", "claim": "供给格局", "source_name": "行业来源",
        "as_of": "2026-07-18", "status": "active", "source_url": "https://example.com",
    }]
    chain.conflicts = [{"status": "open", "description": "来源冲突", "evidence_ids": ["a", "b"]}]
    segment.evidence_state = "supported"
    segment.value_weight = "25%"
    segment.localization_rate = "40%"
    segment.barrier_type = "技术壁垒"
    segment.international_competition = "海外主导"
    segment.domestic_competition = "本土追赶"
    rendered = routes._render_markdown(chain)
    assert "AI算力 产业链分析报告" in rendered
    assert "中际旭创" in rendered
    assert "证据与时点" in rendered and "未解决冲突证据" in rendered
    snapshot = routes._snapshot(chain)
    assert snapshot["segment_scores"] == {"光模块": 82.0}


class _RefreshService:
    instance: "_RefreshService | None" = None

    def __init__(self, store, runtime_getter) -> None:
        self.store = store
        self.runtime_getter = runtime_getter
        self.jobs: dict[str, object] = {}
        self.started = False
        self.stopped = False
        type(self).instance = self

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def request_analysis(self, chain_id, **kwargs):
        if kwargs.get("idempotency_key") == "invalid":
            raise ValueError("invalid request")
        job = SimpleNamespace(
            job_id="job-1", payload={"run_id": ""}, result={"run_id": "run-1"},
            status=SimpleNamespace(value="completed"), attempts=1, max_attempts=3, error="",
        )
        self.jobs[job.job_id] = job
        return job

    def get_job(self, job_id):
        return self.jobs.get(job_id)

    def retry(self, job_id):
        if job_id == "missing":
            raise KeyError(job_id)
        if job_id == "active":
            raise ValueError("already active")
        return SimpleNamespace(job_id=job_id, status=SimpleNamespace(value="queued"))

    def cancel(self, job_id):
        return job_id == "job-1"

    def configure_schedule(self, chain, schedule, *, expected_version):
        if schedule == "invalid":
            raise ValueError("invalid schedule")
        chain.refresh_schedule = schedule
        self.store.save_chain(chain, expected_version=expected_version)
        return {"schedule": schedule, "next_due_at": "2026-07-25" if schedule else None}


class _RunStore:
    def __init__(self) -> None:
        self.runs: dict[str, object] = {}
        self.events: list[object] = []

    def load_run(self, run_id):
        return self.runs.get(run_id)

    def reconcile_run(self, run, *, write):
        return run

    def read_events(self, run_id):
        return self.events


class _Runtime:
    def __init__(self) -> None:
        self._store = _RunStore()
        self.cancelled: list[str] = []

    def cancel_run(self, run_id):
        self.cancelled.append(run_id)
        return True


class _HypRegistry:
    def __init__(self) -> None:
        self.items: list[object] = []

    def search(self, **kwargs):
        return self.items

    def create(self, **kwargs):
        item = SimpleNamespace(universe=kwargs["universe"], to_dict=lambda: {**kwargs, "id": "hyp-1"})
        self.items.append(item)
        return item


def _registered_client(monkeypatch: pytest.MonkeyPatch, tmp_path) -> tuple[TestClient, IndustryChainStore, _Runtime]:
    store = IndustryChainStore(root=tmp_path / "legacy")
    runtime = _Runtime()
    monkeypatch.setattr(routes, "_store", store)
    monkeypatch.setattr(routes, "_hyp_registry", _HypRegistry())
    monkeypatch.setattr(routes, "IndustryChainRefreshService", _RefreshService)
    app = FastAPI()

    async def require_auth() -> None:
        return None

    routes.register_industry_chain_routes(app, require_auth, lambda: runtime)
    return TestClient(app), store, runtime


def test_industry_api_crud_compare_jobs_schedule_hypotheses_and_export(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    client, store, runtime = _registered_client(monkeypatch, tmp_path)
    with client:
        assert _RefreshService.instance is not None and _RefreshService.instance.started
        assert client.get("/industry-chain/templates").status_code == 200
        assert client.post("/industry-chain", json={}).status_code == 400
        assert client.post("/industry-chain", json={"template_key": "absent"}).status_code == 400
        first = client.post("/industry-chain", json={"name": "AI算力", "segment_names": ["光模块"]}).json()
        second = client.post("/industry-chain", json={"name": "半导体", "segment_names": ["设备"]}).json()
        first_id, second_id = first["chain_id"], second["chain_id"]
        assert len(client.get("/industry-chain/list").json()["chains"]) == 2
        assert client.get(f"/industry-chain/{first_id}").json()["name"] == "AI算力"
        assert client.get("/industry-chain/missing").status_code == 400
        assert client.get("/industry-chain/deadbeefcafe").status_code == 404
        assert client.get("/industry-chain/compare?ids=one").status_code == 400
        assert client.get(f"/industry-chain/compare?ids={first_id},deadbeefcafe").status_code == 404

        updated = client.put(f"/industry-chain/{first_id}", json={
            "name": "AI基础设施", "description": "updated", "market": "US",
            "segments": [{"name": "GPU"}], "row_version": first["chain"]["row_version"],
        })
        assert updated.status_code == 200 and updated.json()["chain"]["segments"][0]["name"] == "GPU"

        analyzed = client.post(
            f"/industry-chain/{first_id}/analyze", json={"market": "US"},
            headers={"Idempotency-Key": "request-1"},
        )
        assert analyzed.json()["status"] == "analyzing"
        assert client.post(
            f"/industry-chain/{first_id}/analyze", json={}, headers={"Idempotency-Key": "invalid"},
        ).status_code == 400
        assert client.get(f"/industry-chain/{first_id}/status").json()["run_id"] == ""
        assert client.get(f"/industry-chain/{first_id}/history").status_code == 200
        assert client.get(f"/industry-chain/{first_id}/history/compare?from_snapshot=x&to_snapshot=y").status_code == 404

        scheduled = client.put(f"/industry-chain/{first_id}/schedule", json={
            "schedule": "weekly", "row_version": updated.json()["chain"]["row_version"],
        })
        assert scheduled.status_code == 200
        current = store.get_chain(first_id)
        assert current is not None
        assert client.put(f"/industry-chain/{first_id}/schedule", json={
            "schedule": "invalid", "row_version": current.row_version,
        }).status_code == 400

        created_hyp = client.post(f"/industry-chain/{first_id}/hypotheses", json={
            "title": "光模块景气", "thesis": "需求增长", "invalidation_notes": "价格下跌",
        })
        assert created_hyp.status_code == 200
        assert len(client.get(f"/industry-chain/{first_id}/hypotheses").json()["hypotheses"]) == 1
        exported = client.get(f"/industry-chain/{first_id}/export").json()
        assert exported["filename"].endswith("_研报.md")
        assert client.get("/industry-chain/deadbeefcafe/export").status_code == 404

        assert client.get("/industry-chain/refresh-jobs/missing").status_code == 404
        service = _RefreshService.instance
        assert service is not None
        service.jobs["job-1"] = SimpleNamespace(
            job_id="job-1", status=SimpleNamespace(value="completed"), attempts=1,
            max_attempts=3, error="", result={"run_id": "run-1"},
        )
        assert client.get("/industry-chain/refresh-jobs/job-1").json()["run_id"] == "run-1"
        assert client.post("/industry-chain/refresh-jobs/missing/retry").status_code == 404
        assert client.post("/industry-chain/refresh-jobs/active/retry").status_code == 409
        assert client.post("/industry-chain/refresh-jobs/job-1/retry").status_code == 200
        assert client.post("/industry-chain/refresh-jobs/missing/cancel").status_code == 404
        assert client.post("/industry-chain/refresh-jobs/job-1/cancel").status_code == 200

        assert client.delete(f"/industry-chain/{second_id}").status_code == 200
        assert client.delete(f"/industry-chain/{second_id}").status_code == 404
    assert _RefreshService.instance is not None and _RefreshService.instance.stopped


def _run(status: str, *, report: str = "", tasks=None, agents=None):
    return SimpleNamespace(
        id="run-1",
        status=SimpleNamespace(value=status),
        final_report=report,
        tasks=tasks or [],
        agents=agents or [],
        total_input_tokens=10,
        total_output_tokens=20,
    )


def test_industry_api_run_status_cancel_reingest_and_swarm_detail(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    client, store, runtime = _registered_client(monkeypatch, tmp_path)
    chain = store.save_chain(Chain(name="机器人", status="analyzing", swarm_run_id="run-1"))
    task = SimpleNamespace(
        id="task-1", agent_id="agent-1", status=SimpleNamespace(value="completed"),
        summary="done", started_at="start", completed_at="end",
    )
    runtime._store.events = [
        SimpleNamespace(type="quality_scored", task_id="task-1", agent_id="agent-1", data={"grade": "A", "issues": []}, timestamp="now"),
        SimpleNamespace(type="cross_validation_result", task_id="task-1", agent_id="agent-1", data={"consensus_count": 2}, timestamp="now"),
        SimpleNamespace(type="ignored", task_id="", agent_id="", data={}, timestamp="now"),
    ]
    runtime._store.runs["run-1"] = _run("completed", report="structured", tasks=[task], agents=[SimpleNamespace(id="agent-1", role="analyst")])
    monkeypatch.setattr(routes, "_ingest", lambda *args: True)
    with client:
        status = client.get(f"/industry-chain/{chain.chain_id}/status").json()
        assert status["status"] == "ready" and status["ingested"]
        detail = client.get(f"/industry-chain/{chain.chain_id}/swarm-detail").json()
        assert detail["quality"][0]["grade"] == "A"
        assert detail["cross_validation"][0]["consensus_count"] == 2

        assert client.post(f"/industry-chain/{chain.chain_id}/cancel").json()["cancelled"]
        assert runtime.cancelled == ["run-1"]

        current = store.get_chain(chain.chain_id)
        assert current is not None
        current.status = "error"
        store.save_chain(current)
        assert client.post(f"/industry-chain/{chain.chain_id}/reingest").json()["ingested"]

        current = store.get_chain(chain.chain_id)
        assert current is not None
        current.status = "analyzing"
        store.save_chain(current)
        assert client.post(f"/industry-chain/{chain.chain_id}/retry").status_code == 409

        no_run = store.save_chain(Chain(name="空链"))
        assert client.post(f"/industry-chain/{no_run.chain_id}/reingest").status_code == 400
        assert client.get(f"/industry-chain/{no_run.chain_id}/swarm-detail").json()["tasks"] == []

        missing_run = store.save_chain(Chain(name="丢失", swarm_run_id="absent"))
        assert client.post(f"/industry-chain/{missing_run.chain_id}/reingest").status_code == 404
        assert client.get(f"/industry-chain/{missing_run.chain_id}/swarm-detail").json()["tasks"] == []

        runtime._store.runs["active"] = _run("running")
        active = store.save_chain(Chain(name="运行中", swarm_run_id="active"))
        assert client.post(f"/industry-chain/{active.chain_id}/reingest").status_code == 400


def test_industry_status_rejects_invalid_completed_result_and_marks_failed(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    client, store, runtime = _registered_client(monkeypatch, tmp_path)
    chain = store.save_chain(Chain(name="无证据", status="analyzing", swarm_run_id="bad"))
    runtime._store.runs["bad"] = _run("completed", report="prose")
    monkeypatch.setattr(routes, "_ingest", lambda *args: False)
    with client:
        rejected = client.get(f"/industry-chain/{chain.chain_id}/status").json()
        assert rejected["status"] == "error"

        current = store.get_chain(chain.chain_id)
        assert current is not None
        current.status = "analyzing"
        current.swarm_run_id = "failed"
        store.save_chain(current)
        runtime._store.runs["failed"] = _run("failed")
        failed = client.get(f"/industry-chain/{chain.chain_id}/status").json()
        assert failed["status"] == "error"
