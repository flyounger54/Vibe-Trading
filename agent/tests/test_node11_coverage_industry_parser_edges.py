"""Dense compatibility-parser branches for legacy industry research reports."""

from __future__ import annotations

import json

import pytest

from src.api import industry_chain_routes as routes
from src.industry_chain.store import Chain, Segment


pytestmark = pytest.mark.unit


def test_chain_result_size_wrapper_and_score_barrier_edge_formats() -> None:
    assert routes._extract_chain_result("x" * 2_000_001) is None
    assert routes._extract_chain_result(json.dumps({"research_result": {}})) is None
    assert routes._extract_chain_json("invalid") is None
    assert routes._extract_total_score(
        "总分 150/100\n| Chokepoint总分 | 100 | 81 |"
    ) == 81
    assert routes._extract_total_score("| 合计 | 调整 | **65 → 60** |") == 65

    table = "| Chokepoint总分 | 100 | D1 | 总分 | 技术壁垒 |"
    assert routes._extract_barrier_type(table) == "技术壁垒"
    assert routes._extract_barrier_type(
        "| 合计 | 100 | 80 | 高端制造—海外垄断 |"
    ) == "高端制造—海外垄断"
    assert routes._extract_barrier_type("壁垒类型：没有有效标签") == ""


def test_markdown_parser_rejection_duplicate_and_section_fallback_matrix() -> None:
    chain = Chain(
        name="AI链",
        segments=[Segment(name="光模块"), Segment(name="算力芯片"), Segment(name="设备")],
    )
    director = """
lifecycle: Exhaustion
景气度评分 **150/100**
## 综合摘要
短摘要

| 环节 | 90 | -1 | **80** | 风险 |
| 光模块扩展 | 90 | -2 | **88** | 估值风险 |
| 无效公司 | 600000.SH | 公司 | 50 | Watch | Beneficiary | 无效段 |
| 中际旭创 | 300308.SZ | 光模块 | 85 | Core | Controller | 风险一 |
| 重复公司 | 300308.SZ | 光模块 | 80 | Build | Controller | 重复 |
| 🥇 | 300308.SZ | 重复 | 光模块 | Core | Controller | 重复 |
| 🥈 | 688256.SH | 寒武纪 | 算力芯片 | Build | Integrator | 盈利验证 |
"""
    report = director + """
prosperity_score: 77
## 一、环节
**定位**：上游

| 环节 | 1 | 2 | 3 | 4 | 5 | 6 | 21 | 壁垒类型 |
| 光模块 | 18 | 17 | 12 | 13 | 10 | 8 | 78 | --- |
| 设备 | 12 | 13 | 10 | 11 | 9 | 7 | 62 | 制造壁垒 |
### 1.1 环节 — 总分：60/100
### 1.2 算力芯片 — 总分：76/100
### 未知环节 — 总分 50/100
| 供给集中度 | 22 | **18** |
### 算力芯片详细 —— CHOKEPOINT 总分 74/100
| 供给集中度 | 22 | **18** |
| 不可替代性 | 22 | **17** |
| 供需缺口 | 16 | **12** |
| 认证壁垒 | 16 | **13** |
| 合计 | 100 | 74 | 技术壁垒 |

| 公司 | 上游 | 无权重 | 国产化进展 |
| 光模块 | 中游 | 25% [P2 估算] | --- |
| 设备 | 下游 | 无权重 | 30% [P2 估算] |

## 二、光模块扩展（Optical Module）
（中游—核心）
**价值权重**：无百分比 —
**国产化率**：40% —
海外厂商主导高端器件。
| 中际旭创 | 300308.SZ | Controller | 重复公司 |
| 新易盛 | 300502.SZ | Controller | 800G 产品 |
| 天孚通信 | 300394.SZ | Controller | 光器件 |
| 光迅科技 | 002281.SZ | Controller | 光芯片 |

## 三、算力芯片（Compute Chip）
定位：上游
**价值权重**：30% —
**国产化进展**：15% —
依赖进口先进制程。
| 无效 | 600001.SH | Controller | 无效 |

## 四、设备（Equipment）
国产设备快速追赶。
"""
    parsed = routes._extract_from_markdown(report, chain, director)
    assert parsed is not None
    assert parsed["lifecycle_stage"] == "Exhaustion"
    assert parsed["prosperity_score"] == 77
    segments = {item["name"]: item for item in parsed["segments"]}
    assert segments["光模块"]["chokepoint_total"] == 88
    assert len(segments["光模块"]["tickers"]) == 4
    assert segments["算力芯片"]["chokepoint_total"] == 76
    assert segments["算力芯片"]["barrier_type"] == "技术壁垒"
    assert segments["设备"]["positioning"] == "下游"
    assert segments["设备"]["localization_rate"] == "30%"


def test_markdown_parser_lifecycle_only_and_unknown_segment_filter() -> None:
    chain = Chain(name="空链")
    parsed = routes._extract_from_markdown(
        "生命周期阶段 **Discovery**\n| 未知 | 中游 | 无权重 | --- |",
        chain,
    )
    assert parsed == {
        "segments": [{"name": "未知", "positioning": "中游"}],
        "lifecycle_stage": "Discovery",
    }
