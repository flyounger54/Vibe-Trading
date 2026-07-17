"""Industry-chain dashboard HTTP routes for the Web UI.

Mounted by ``agent/api_server.py`` via ``register_industry_chain_routes(app)``.

This is the orchestration glue described in the dashboard plan: it owns chain
CRUD over :class:`~src.industry_chain.store.IndustryChainStore`, and delegates
the actual analysis to the existing ``industry_chain_dashboard`` swarm preset
through the shared :class:`SwarmRuntime`. When a run completes, the director's
structured JSON is parsed back into the chain's segments (``_ingest``).

Routes:
- ``GET    /industry-chain/templates``      — list pre-built chain templates
- ``GET    /industry-chain/list``           — list all chains (summaries)
- ``GET    /industry-chain/{id}``           — full chain detail
- ``POST   /industry-chain``                — create (from template or custom)
- ``PUT    /industry-chain/{id}``           — update metadata / segments
- ``DELETE /industry-chain/{id}``           — delete a chain
- ``POST   /industry-chain/{id}/analyze``   — launch the analysis swarm
- ``GET    /industry-chain/{id}/status``    — run status; auto-ingests on done
- ``GET    /industry-chain/{id}/history``   — prosperity snapshots
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Awaitable, Callable, List, Optional

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

from src.industry_chain.store import (
    STATUS_ANALYZING,
    STATUS_ERROR,
    STATUS_READY,
    Chain,
    IndustryChainStore,
    Segment,
    Ticker,
)
from src.hypotheses.registry import HypothesisRegistry
from src.industry_chain.templates import (
    build_chain_from_template,
    build_custom_chain,
    list_templates,
)

logger = logging.getLogger(__name__)

_PRESET_NAME = "industry_chain_dashboard"
_store = IndustryChainStore()
_hyp_registry = HypothesisRegistry()

AuthDep = Callable[..., Awaitable[None]]


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------


class CreateChainRequest(BaseModel):
    """Create a chain from a template, or as a custom chain.

    Provide ``template_key`` for a template-seeded chain, or ``name`` +
    ``segment_names`` for a custom one.
    """

    template_key: Optional[str] = None
    name: Optional[str] = None
    segment_names: Optional[List[str]] = None
    description: str = ""
    market: str = "A"


class UpdateChainRequest(BaseModel):
    """Partial update of chain metadata and/or its full segment list."""

    name: Optional[str] = None
    description: Optional[str] = None
    market: Optional[str] = None
    segments: Optional[List[dict]] = None


class AnalyzeRequest(BaseModel):
    """Optional overrides when launching analysis."""

    market: Optional[str] = None


class ScheduleRequest(BaseModel):
    """Set or clear the periodic refresh schedule."""

    schedule: str = ""  # "" | "weekly" | "monthly"


class CreateHypothesisRequest(BaseModel):
    """Create a hypothesis linked to a chain."""

    title: str
    thesis: str
    status: str = "exploring"
    invalidation_notes: str = ""


# ---------------------------------------------------------------------------
# Swarm result ingestion
# ---------------------------------------------------------------------------

_JSON_BLOCK = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)


def _extract_chain_json(report: str) -> Optional[dict]:
    """Extract the director's structured ``{"chain": {...}}`` block.

    The director is instructed to emit exactly one fenced ``json`` block; if it
    emits several, the last valid one wins (the contract block comes last).

    Args:
        report: The swarm ``final_report`` text.

    Returns:
        The parsed ``chain`` dict, or ``None`` when no valid block is found.
    """
    if not report:
        return None
    candidates = _JSON_BLOCK.findall(report)
    for raw in reversed(candidates):
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            continue
        if isinstance(parsed, dict) and "chain" in parsed:
            return parsed["chain"]
        if isinstance(parsed, dict) and "segments" in parsed:
            return parsed
    return None


def _clean_seg_name(raw: str) -> str:
    """Strip Markdown bold, leading pipes, and whitespace from a segment name."""
    s = raw.strip().lstrip("|").strip()
    s = s.replace("**", "").strip()
    return s


def _strip_ptags(text: str) -> str:
    """Remove evidence-governance P-tags like ``[P2 估算]``."""
    return re.sub(r"\s*\[P\d[^]]*\]", "", text).strip()


_DIM_PATTERNS: List[tuple] = [
    ("supply_concentration", 22, re.compile(r"供[给应]集中度")),
    ("irreplaceability", 22, re.compile(r"不可替代性")),
    ("supply_demand_gap", 16, re.compile(r"供需缺口")),
    ("certification_barrier", 16, re.compile(r"认证壁垒")),
    ("information_asymmetry", 14, re.compile(r"信息不对称")),
    ("catalyst_optionality", 10, re.compile(r"催化")),
]


def _extract_dim_score(line: str, dim_max: int) -> Optional[float]:
    """Extract a dimension score from a table row containing the dimension keyword.

    Handles multiple output formats: ``| dim | max | **score** |``,
    ``| dim | **score** | max |``, and NEV-style with P2-discount columns.
    Prefers bold numbers; falls back to the last plausible candidate.
    """
    bold_nums = [float(n) for n in re.findall(r"\*\*(\d+(?:\.\d+)?)\*\*", line)]
    all_nums = [float(n) for n in re.findall(r"(\d+(?:\.\d+)?)", line)]
    bold_ok = [n for n in bold_nums if 0 < n <= dim_max]
    if bold_ok:
        return bold_ok[-1]
    candidates = [n for n in all_nums if 0 < n <= dim_max and n != dim_max]
    return candidates[-1] if candidates else None


def _extract_total_score(text: str) -> Optional[float]:
    """Extract a chokepoint total score from a section of text.

    Recognises header patterns (``总分 68/100``, ``总分：60/100``) and
    body-row patterns (``Chokepoint总分 | 100 | 81``).
    """
    for pat in [
        r"(?:CHOKEPOINT\s*)?总分[：:\s]*(\d{1,3})\s*/\s*100",
        r"总分[：:\s]+(\d{1,3})\b",
    ]:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            v = float(m.group(1))
            if 0 < v <= 100:
                return v
    for pat in [
        r"\|\s*\*{0,2}(?:Chokepoint)?总分\*{0,2}\s*\|\s*\*{0,2}100\*{0,2}\s*\|\s*\*{0,2}(\d{1,3}(?:\.\d+)?)\*{0,2}",
        r"\|\s*\*{0,2}(?:Chokepoint)?总分\*{0,2}\s*\|\s*\*{0,2}(\d{1,3}(?:\.\d+)?)\*{0,2}\s*\|\s*\*{0,2}100\*{0,2}",
        r"\|\s*\*{0,2}合计\*{0,2}\s*\|.*?\*{0,2}(\d{1,3}(?:\.\d+)?)\s*(?:→\s*\d+)?\*{0,2}\s*\|",
    ]:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            raw = m.group(1)
            v = float(raw)
            if 0 < v <= 100:
                return v
    return None


def _extract_barrier_type(text: str) -> str:
    """Extract barrier_type from body text or score-row description."""
    m = re.search(r"barrier_type\*{0,2}[：:]\s*(.+?)$", text, re.MULTILINE)
    if m:
        raw = re.sub(r"[🔬🏭📋\s]+", "", m.group(1)).strip()
        raw = raw.replace("+", "+").replace("＋", "+")
        parts = [p.strip() for p in raw.split("+") if "壁垒" in p]
        return "+".join(parts) if parts else ""
    _barrier_skip = {"总分", "合计", "Chokepoint总分", "加权总分"}
    for line in text.split("\n"):
        if not ("|" in line and re.search(r"(?:Chokepoint)?总分|合计", line)):
            continue
        cols = [c.strip() for c in line.split("|") if c.strip()]
        for col in reversed(cols):
            clean = _clean_seg_name(col)
            clean = re.sub(r"[🔬🏭📋🔴🟡🟢⚪⚫\s]*", "", clean).strip()
            if not clean or clean.isdigit() or clean == "100" or clean in _barrier_skip:
                continue
            if re.match(r"^D?\d", clean):
                continue
            if "壁垒" in clean and len(clean) < 40:
                return clean
            if len(clean) > 5 and len(clean) < 40 and "—" in clean:
                return clean
    m = re.search(r"壁垒类型[：:]\s*(.+?壁垒[^\n]{0,30})", text)
    if m:
        raw = re.sub(r"[🔬🏭📋\s]*", "", _clean_seg_name(m.group(1))).strip()
        if "壁垒" in raw:
            return raw
    return ""


def _extract_from_markdown(
    report: str,
    chain: Chain,
    score_source: str = "",
) -> Optional[dict]:
    """Fallback: parse Markdown tables from all swarm tasks into a chain dict.

    Scores and tickers are extracted from ``score_source`` (the director's
    final report only) to avoid matching dimension sub-tables from the scorer
    task.  Positioning data, 6-dimension breakdowns, barrier types, company
    details, and English names are extracted from the full ``report`` (all
    tasks combined).
    """
    if not report:
        return None

    director = score_source or report
    result: dict = {"segments": []}

    # Lifecycle stage (from director, then full report)
    for source in (director, report):
        for pattern in [
            r"处于\s*\*{0,2}(Discovery|Validation|Mainstream|Exhaustion)",
            r"lifecycle.*?(Discovery|Validation|Mainstream|Exhaustion)",
            r"生命周期.*?(Discovery|Validation|Mainstream|Exhaustion)",
            r"生命周期阶段.*?\*{0,2}(Discovery|Validation|Mainstream|Exhaustion)",
        ]:
            m = re.search(pattern, source, re.IGNORECASE)
            if m:
                result["lifecycle_stage"] = m.group(1)
                break
        if result.get("lifecycle_stage"):
            break

    # Prosperity score (from full report — scorer task has it)
    for ps_pat in [
        r"繁荣度评分.*?(\d{1,3})\s*/\s*100",
        r"景气度评分.*?\*{0,2}(\d{1,3})\s*/\s*100\*{0,2}",
        r"prosperity_score.*?(\d{1,3})",
    ]:
        m = re.search(ps_pat, report, re.IGNORECASE)
        if m:
            v = int(m.group(1))
            if 0 < v <= 100:
                result["prosperity_score"] = v
                break

    # Structure summary (from director, then scorer)
    for src in (director, report):
        for pat in [
            r"执行摘要\s*\n+(.+?)(?:\n\n|\n---|\n##)",
            r"##\s*(?:[一二三四五六七八九十\d]*[、.]?\s*)?(?:执行摘要|综合摘要|产业链格局|总结)\s*\n+(.+?)(?:\n\n|\n---|\n##)",
        ]:
            m = re.search(pat, src, re.DOTALL)
            if m and len(m.group(1).strip()) > 20:
                result["structure_summary"] = m.group(1).strip()
                break
        if result.get("structure_summary"):
            break

    known_names = {s.name for s in chain.segments}
    seg_map: dict = {}

    def _resolve_name(raw: str) -> str:
        """Resolve a segment name to a known name via substring matching."""
        if raw in known_names or raw in seg_map:
            return raw
        for known in known_names:
            if raw in known or known in raw:
                return known
        return raw

    def _get_or_create(name: str) -> dict:
        clean = _clean_seg_name(name)
        if not clean or clean in ("段", "环节", "---", ":---", "公司", "维度", "排名"):
            return {}
        clean = _resolve_name(clean)
        if clean not in seg_map:
            seg_map[clean] = {"name": clean}
        return seg_map[clean]

    # --- Director tables (red-team-adjusted scores + final tickers) ---------

    # Segment score table FROM DIRECTOR ONLY (multiple format variants):
    # Format A: | 段 | 原评分 | 红队调整 | 调整后 | 级别 | 核心风险 |
    # Format B: | 段 | 原始Chokepoint | 红队调整 | 调整后 | 评级变动 |  (bold scores)
    for seg_pat in [
        re.compile(r"\|\s*(.+?)\s*\|\s*(\d+)\s*\|\s*-\d+\s*\|\s*(\d+)\s*\|\s*(\w+)\s*\|\s*(.+?)\s*\|"),
        re.compile(r"\|\s*(.+?)\s*\|\s*(\d+)\s*\|\s*-\d+\s*\|\s*\*{0,2}(\d+)\*{0,2}\s*\|\s*(.+?)\s*\|"),
    ]:
        seg_table = seg_pat.findall(director)
        for row in seg_table:
            if len(row) >= 4:
                name = row[0]
                adj_score = row[2]
                entry = _get_or_create(name)
                if not entry:
                    continue
                try:
                    entry["chokepoint_total"] = int(adj_score)
                except ValueError:
                    continue
                if len(row) >= 5:
                    entry["barrier_description"] = row[-1].strip()

    # Company table FROM DIRECTOR ONLY (multiple format variants):
    # Format A: | 公司 | 代码 | 段 | 调整后评分 | 级别 | 分类 | 关键风险 |
    # Format B: | 排名 | 代码 | 名称 | 段 | Tier | 分类 | 描述 |
    seen_tickers: set = set()

    company_table_a = re.findall(
        r"\|\s*(.+?)\s*\|\s*(\d{6}\.\w{2})\s*\|\s*(.+?)\s*\|\s*(\d+)\s*\|\s*(\w+)\s*\|\s*(\w+)\s*\|\s*(.+?)\s*\|",
        director,
    )
    for row in company_table_a:
        co_name, code, seg_name, score, tier, classification, risk = row
        entry = _get_or_create(seg_name)
        if not entry:
            continue
        if "tickers" not in entry:
            entry["tickers"] = []
        dedup_key = code.strip()
        if dedup_key in seen_tickers:
            continue
        seen_tickers.add(dedup_key)
        entry["tickers"].append({
            "code": dedup_key,
            "name": _clean_seg_name(co_name),
            "score": int(score),
            "tier": tier.strip(),
            "classification": classification.strip(),
            "red_team_note": risk.strip(),
        })

    # Format B: | rank_emoji | code | name | segment | tier | classification | note |
    company_table_b = re.findall(
        r"\|\s*.+?\s*\|\s*(\d{6}\.\w{2})\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|[^|]*(Core|Build|Watch|Skip)[^|]*\|[^|]*(Controller|Integrator|Beneficiary)[^|]*\|\s*(.+?)\s*\|",
        director,
    )
    for row in company_table_b:
        code, co_name, seg_name, tier, classification, note = row
        entry = _get_or_create(seg_name)
        if not entry:
            continue
        if "tickers" not in entry:
            entry["tickers"] = []
        dedup_key = code.strip()
        if dedup_key in seen_tickers:
            continue
        seen_tickers.add(dedup_key)
        entry["tickers"].append({
            "code": dedup_key,
            "name": _clean_seg_name(co_name),
            "tier": tier.strip(),
            "classification": classification.strip(),
            "red_team_note": _clean_seg_name(note.strip())[:120],
        })

    # --- Scorer tables (6-dimension breakdown + barrier_type) ---------------

    # Scorer overview table (all segments in one table):
    # | 环节 | 供给集中度 (22) | 不可替代性 (22) | 供需缺口 (16) | 认证壁垒 (16) | 信息不对称 (14) | 催化剂 (10) | 总分 (100) | 壁垒类型 |
    _DIM_KEYS = [
        "supply_concentration", "irreplaceability", "supply_demand_gap",
        "certification_barrier", "information_asymmetry", "catalyst_optionality",
    ]
    scorer_overview = re.findall(
        r"\|\s*\*{0,2}(.+?)\*{0,2}\s*\|"
        r"\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|"
        r"\s*\*{0,2}(\d+)\*{0,2}\s*\|\s*(.+?)\s*\|",
        report,
    )
    for row in scorer_overview:
        name = row[0]
        dims = [int(row[i]) for i in range(1, 7)]
        total = int(row[7])
        barrier_type = _clean_seg_name(row[8])
        entry = _get_or_create(name)
        if not entry:
            continue
        if not entry.get("chokepoint_score"):
            entry["chokepoint_score"] = dict(zip(_DIM_KEYS, dims))
        if not entry.get("chokepoint_total"):
            entry["chokepoint_total"] = total
        if barrier_type and barrier_type not in ("壁垒类型", "---"):
            entry["barrier_type"] = barrier_type

    # Scorer per-segment headers: ### 3.1 谐波减速器 — 总分：60/100
    # Extract scores for segments not in director's table.
    scorer_seg_scores = re.findall(
        r"###?\s*[\d.]+\s*(.+?)\s*—\s*总分[：:]\s*(\d+)\s*/\s*100",
        report,
    )
    for name, score in scorer_seg_scores:
        entry = _get_or_create(name)
        if not entry:
            continue
        if not entry.get("chokepoint_total"):
            entry["chokepoint_total"] = int(score)

    # --- Section-based scorer fallback (per-segment tables) ----------------
    # Handles formats where each segment has its own dimension table:
    #   ### 🔴 第5段：车规芯片 —— CHOKEPOINT 总分 68/100  (NEV)
    #   ### 🔴 第一梯队：晶圆制造 — 总分: 80/100          (Semiconductor)
    #   ### 2.1 算力芯片（上游核心）— 权重~25%             (AI Computing)
    _scorer_sec_pat = re.compile(
        r"^###?\s+.+$", re.MULTILINE,
    )
    scorer_sections = list(_scorer_sec_pat.finditer(report))
    for idx, hdr_match in enumerate(scorer_sections):
        hdr_text = hdr_match.group(0)
        sec_start = hdr_match.end()
        sec_end = scorer_sections[idx + 1].start() if idx + 1 < len(scorer_sections) else min(sec_start + 3000, len(report))
        sec_body = report[sec_start:sec_end]

        matched_name = ""
        for seg_name in known_names:
            if seg_name in hdr_text:
                matched_name = seg_name
                break
        if not matched_name:
            continue

        entry = _get_or_create(matched_name)
        if not entry:
            continue

        if not entry.get("chokepoint_total"):
            total = _extract_total_score(hdr_text + "\n" + sec_body)
            if total is not None:
                entry["chokepoint_total"] = total

        if not entry.get("chokepoint_score"):
            dims: dict = {}
            for dim_key, dim_max, dim_re in _DIM_PATTERNS:
                for line in sec_body.split("\n"):
                    if dim_re.search(line) and "|" in line:
                        score = _extract_dim_score(line, dim_max)
                        if score is not None:
                            dims[dim_key] = score
                        break
            if len(dims) >= 3:
                entry["chokepoint_score"] = dims

        if not entry.get("barrier_type"):
            bt = _extract_barrier_type(hdr_text + "\n" + sec_body)
            if bt:
                entry["barrier_type"] = bt

    # --- Mapper tables (positioning, companies, English names) --------------

    # Positioning table FROM FULL REPORT (chain_mapper task):
    # | 环节 | 定位 | 价值权重 | 国产化进展 |
    pos_table = re.findall(
        r"\|\s*(.+?)\s*\|\s*(上游|中游|下游|上中游)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|",
        report,
    )
    for row in pos_table:
        name, positioning, value_weight, localization = row
        entry = _get_or_create(name)
        if not entry:
            continue
        entry["positioning"] = positioning.strip()
        vw = re.sub(r"\s*\[P\d.*?\]", "", value_weight.strip())
        if vw and "%" in vw:
            entry["value_weight"] = vw
        loc = localization.strip()
        if loc and loc not in ("国产化进展", "---"):
            entry["localization_rate"] = re.sub(r"\s*\[P\d.*?\]", "", loc)

    # --- Bullet-point extraction (handles mapper outputs that use bullets) ----
    # Re-uses sec_starts logic below; split into sections by numbered ## headers,
    # then scan each section for structured bullet lines.
    _bullet_sec_hdr = re.compile(
        r"^##\s*[一二三四五六七八九十\d]+[、.]\s*(.+?)(?:[（(]|$)",
        re.MULTILINE,
    )
    _any_h2_bullet = re.compile(r"^##\s", re.MULTILINE)
    for bm in _bullet_sec_hdr.finditer(report):
        seg_name = _clean_seg_name(bm.group(1))
        entry = _get_or_create(seg_name)
        if not entry:
            continue
        bm_hdr = bm.group(0)
        body_start = bm.end()
        next_h2 = _any_h2_bullet.search(report, body_start)
        body_end = next_h2.start() if next_h2 else min(body_start + 3000, len(report))
        body = report[body_start:body_end]

        if not entry.get("positioning"):
            for ppat in [
                r"\*{0,2}定位\*{0,2}[：:⬆️➡️⬇️|*\s]*\*{0,2}(上游|中游|下游)",
                r"[（(]\s*(上游|中游|下游)\s*[—\-]",
            ]:
                m = re.search(ppat, body) or re.search(ppat, bm_hdr)
                if m:
                    entry["positioning"] = m.group(1)
                    break
            if not entry.get("positioning"):
                m = re.search(r"(上游|中游|下游)", bm_hdr)
                if m:
                    entry["positioning"] = m.group(1)
        if not entry.get("value_weight"):
            m = re.search(
                r"\*{0,2}价值权重\*{0,2}\s*[：:|]\s*\*{0,2}([^|\n]+?)\*{0,2}\s*(?:[—|]|$)",
                body,
            )
            if m:
                vw = _strip_ptags(m.group(1).strip())
                if "%" in vw:
                    entry["value_weight"] = vw
        if not entry.get("localization_rate"):
            m = re.search(
                r"\*{0,2}国产化(?:率|进展)?\*{0,2}\s*[：:|]\s*\*{0,2}([^|\n]+?)\*{0,2}\s*(?:[—|]|$)",
                body,
            )
            if m:
                entry["localization_rate"] = _strip_ptags(m.group(1).strip())

    # Segment English names from mapper section headers:
    # ## 一、谐波减速器（Harmonic Reducer）
    en_name_matches = re.findall(
        r"##\s*[一二三四五六七八九十\d]+[、.]\s*(.+?)[（(](.+?)[）)]",
        report,
    )
    for cn_name, en_name in en_name_matches:
        entry = _get_or_create(cn_name)
        if entry and en_name.strip():
            entry["name_en"] = en_name.strip()

    # Mapper company tables (richer than director — 5 companies per segment):
    # | 公司 | 代码 | 分类 | 依据 |
    # We attribute companies to the nearest preceding segment header.
    _seg_header_pat = re.compile(
        r"^##\s*[一二三四五六七八九十\d]+[、.]\s*(.+?)(?:[（(]|$)", re.MULTILINE,
    )
    _company_row_pat = re.compile(
        r"\|\s*\*{0,2}(.+?)\*{0,2}\s*\|\s*(\d{6}\.\w{2})\s*\|[^|]*(Controller|Integrator|Beneficiary)[^|]*\|\s*(.+?)\s*\|"
    )
    current_seg_name = ""
    for line in report.split("\n"):
        hdr = _seg_header_pat.match(line)
        if hdr:
            current_seg_name = _clean_seg_name(hdr.group(1))
        if not current_seg_name:
            continue
        cm = _company_row_pat.search(line)
        if not cm:
            continue
        co_name, code, classification, rationale = cm.groups()
        dedup_key = code.strip()
        if dedup_key in seen_tickers:
            continue
        entry = _get_or_create(current_seg_name)
        if not entry:
            continue
        if "tickers" not in entry:
            entry["tickers"] = []
        seen_tickers.add(dedup_key)
        entry["tickers"].append({
            "code": dedup_key,
            "name": _clean_seg_name(co_name),
            "classification": classification.strip(),
            "key_products": re.sub(r"\s*\[P\d.*?\]", "", rationale.strip())[:120],
        })

    # Mapper per-segment competition/description blocks.
    # Find each numbered ## section header and extract the body until the next
    # ## header (numbered or not) to avoid bleeding into summary tables.
    _sec_named_hdr = re.compile(
        r"^##\s*[一二三四五六七八九十\d]+[、.]\s*(.+?)(?:[（(]|$)",
        re.MULTILINE,
    )
    _any_h2 = re.compile(r"^##\s", re.MULTILINE)
    sec_starts = [(m.group(1).strip(), m.end()) for m in _sec_named_hdr.finditer(report)]
    for idx, (raw_name, start) in enumerate(sec_starts):
        next_h2 = _any_h2.search(report, start)
        end = next_h2.start() if next_h2 else min(start + 2000, len(report))
        body = report[start:end]
        seg_name = _clean_seg_name(raw_name)
        entry = _get_or_create(seg_name)
        if not entry:
            continue
        # International competition
        m = re.search(
            r"(?:对标|[严仍]重?依赖|依赖进口|海外.*?主导|国外|国际竞争).{0,100}?(?=[。\n|])",
            body,
        )
        if m and not entry.get("international_competition"):
            val = re.sub(r"\s*\[P\d.*?\]", "", m.group(0).strip()).rstrip("|").strip()
            if val:
                entry["international_competition"] = val
        # Domestic competition from Controller tickers
        co_count = len(entry.get("tickers", []))
        if co_count >= 3 and not entry.get("domestic_competition"):
            controllers = [
                t["name"] for t in entry.get("tickers", [])
                if t.get("classification") == "Controller"
            ]
            if controllers:
                entry["domestic_competition"] = "国内龙头: " + "/".join(controllers[:3])

    final_segs = []
    for name, data in seg_map.items():
        has_data = (
            data.get("chokepoint_total") or data.get("tickers")
            or data.get("positioning") or data.get("chokepoint_score")
        )
        is_known = name in known_names
        if has_data or is_known:
            final_segs.append(data)

    if final_segs:
        result["segments"] = final_segs

    return result if (result.get("segments") or result.get("lifecycle_stage")) else None


def _ingest(chain: Chain, report: str, final_report: str = "") -> bool:
    """Merge the director's structured JSON back into a chain in place.

    Tries the structured JSON block first; falls back to parsing Markdown
    tables when the director outputs prose+tables instead of a fenced JSON
    contract.

    Args:
        chain: The chain to update (mutated in place).
        report: Combined text of all task summaries + final_report.
        final_report: The director's final_report only (for score extraction).

    Returns:
        ``True`` when data was found and applied.
    """
    data = _extract_chain_json(report)
    if not data:
        data = _extract_from_markdown(
            report, chain,
            score_source=final_report or report,
        )
    if not data:
        return False

    chain.overview.structure_summary = str(data.get("structure_summary", "")) or chain.overview.structure_summary
    chain.overview.lifecycle_stage = str(data.get("lifecycle_stage", "")) or chain.overview.lifecycle_stage
    if data.get("prosperity_score") is not None:
        chain.overview.prosperity_score = _num(data.get("prosperity_score"))
    if data.get("sector_score") is not None:
        chain.overview.sector_score = _num(data.get("sector_score"))

    by_name = {s.name: s for s in chain.segments}
    core_targets: List[Ticker] = []
    for seg_data in data.get("segments", []):
        if not isinstance(seg_data, dict):
            continue
        name = str(seg_data.get("name", "")).strip()
        if not name:
            continue
        seg = by_name.get(name)
        if seg is None:
            seg = Segment(name=name, order=len(chain.segments) + 1)
            chain.segments.append(seg)
            by_name[name] = seg
        _apply_segment(seg, seg_data)
        # Collect Core/Build tickers into the chain-level core targets pool.
        core_targets.extend(t for t in seg.tickers if t.tier in ("Core", "Build"))

    if core_targets:
        core_targets.sort(key=lambda t: (t.score or 0), reverse=True)
        chain.overview.core_targets = core_targets[:12]
    return True


def _apply_segment(seg: Segment, data: dict) -> None:
    """Apply analyzed fields onto a segment (mutates in place)."""
    for field in (
        "name_en",
        "positioning",
        "value_weight",
        "localization_rate",
        "international_competition",
        "domestic_competition",
        "barrier_type",
        "barrier_description",
    ):
        value = data.get(field)
        if value and (not getattr(seg, field, None) or field != "barrier_description"):
            setattr(seg, field, str(value))
    if isinstance(data.get("chokepoint_score"), dict) and not seg.chokepoint_score:
        seg.chokepoint_score = {k: _num(v) for k, v in data["chokepoint_score"].items() if _num(v) is not None}
    if data.get("chokepoint_total") is not None:
        seg.chokepoint_total = _num(data.get("chokepoint_total"))
    tickers_raw = data.get("tickers")
    if isinstance(tickers_raw, list) and tickers_raw:
        if seg.tickers:
            existing_by_code = {t.code: t for t in seg.tickers}
            for t_data in tickers_raw:
                if not isinstance(t_data, dict):
                    continue
                code = t_data.get("code", "")
                if code in existing_by_code:
                    old = existing_by_code[code]
                    for attr in ("score", "tier", "classification", "confidence",
                                 "key_products", "red_team_note"):
                        new_val = t_data.get(attr)
                        if new_val and not getattr(old, attr, None):
                            setattr(old, attr, new_val if attr != "score" else _num(new_val))
                    if not old.name and t_data.get("name"):
                        old.name = str(t_data["name"])
                else:
                    seg.tickers.append(Ticker.from_dict(t_data))
                    existing_by_code[code] = seg.tickers[-1]
        else:
            seg.tickers = [Ticker.from_dict(t) for t in tickers_raw if isinstance(t, dict)]
    seg.status = "complete"


def _num(value: Any) -> Optional[float]:
    """Coerce to float, or None when non-numeric."""
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _snapshot(chain: Chain) -> dict:
    """Build a prosperity time-series snapshot from the current chain state."""
    return {
        "lifecycle_stage": chain.overview.lifecycle_stage,
        "prosperity_score": chain.overview.prosperity_score,
        "sector_score": chain.overview.sector_score,
        "segment_scores": {
            s.name: s.chokepoint_total for s in chain.segments if s.chokepoint_total is not None
        },
    }


def _render_markdown(chain: Chain) -> str:
    """Render a chain as a structured Markdown research report."""
    lines: List[str] = []
    lines.append(f"# {chain.name} 产业链分析报告\n")
    if chain.overview.lifecycle_stage:
        lines.append(f"**生命周期**: {chain.overview.lifecycle_stage} | "
                      f"**景气度**: {chain.overview.prosperity_score or '—'} | "
                      f"**板块评分**: {chain.overview.sector_score or '—'}\n")
    if chain.overview.structure_summary:
        lines.append(f"## 产业链格局\n\n{chain.overview.structure_summary}\n")

    lines.append("## 各环节分析\n")
    for seg in chain.segments:
        score_str = f" (卡脖子 {seg.chokepoint_total})" if seg.chokepoint_total is not None else ""
        lines.append(f"### {seg.name}{score_str}\n")
        if seg.positioning:
            lines.append(f"- **定位**: {seg.positioning}")
        if seg.value_weight:
            lines.append(f"- **价值量占比**: {seg.value_weight}")
        if seg.localization_rate:
            lines.append(f"- **国产化进展**: {seg.localization_rate}")
        if seg.barrier_type:
            lines.append(f"- **壁垒类型**: {seg.barrier_type}")
        if seg.barrier_description:
            lines.append(f"- **壁垒说明**: {seg.barrier_description}")
        if seg.international_competition:
            lines.append(f"\n**国际竞争格局**: {seg.international_competition}")
        if seg.domestic_competition:
            lines.append(f"\n**国内竞争格局**: {seg.domestic_competition}")
        if seg.tickers:
            lines.append("\n| 代码 | 名称 | 评分 | 分级 | 分类 | 红队结论 |")
            lines.append("|------|------|------|------|------|---------|")
            for t in seg.tickers:
                lines.append(f"| {t.code} | {t.name} | {t.score or '—'} | {t.tier or '—'} | "
                              f"{t.classification or '—'} | {t.red_team_note or '—'} |")
        lines.append("")

    if chain.overview.core_targets:
        lines.append("## 核心标的池\n")
        lines.append("| 代码 | 名称 | 评分 | 分级 | 置信度 |")
        lines.append("|------|------|------|------|--------|")
        for t in chain.overview.core_targets:
            lines.append(f"| {t.code} | {t.name} | {t.score or '—'} | {t.tier or '—'} | {t.confidence or '—'} |")
        lines.append("")

    lines.append("---\n")
    lines.append(f"*免责声明: 本报告由 AI 多Agent 协作生成，仅供研究参考，不构成投资建议。"
                  f" 生成时间: {chain.updated_at}*\n")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register_industry_chain_routes(
    app: FastAPI,
    require_auth: Optional[AuthDep] = None,
    get_swarm_runtime: Optional[Callable[[], Any]] = None,
) -> None:
    """Attach industry-chain dashboard endpoints to the FastAPI app.

    Auth and the swarm runtime accessor are resolved from the ``api_server``
    module when not supplied, mirroring ``register_ml_routes``.

    Args:
        app: The FastAPI application.
        require_auth: Auth dependency; resolved from api_server when ``None``.
        get_swarm_runtime: Swarm runtime accessor; resolved from api_server
            when ``None``.
    """
    if require_auth is None or get_swarm_runtime is None:
        import sys

        host = sys.modules.get("api_server") or sys.modules.get("agent.api_server")
        if host is None:
            raise RuntimeError("register_industry_chain_routes: api_server not in sys.modules")
        if require_auth is None:
            require_auth = host.require_auth
        if get_swarm_runtime is None:
            get_swarm_runtime = host._get_swarm_runtime

    @app.get("/industry-chain/templates")
    async def industry_chain_templates() -> dict:
        return {"templates": list_templates()}

    @app.get("/industry-chain/list")
    async def industry_chain_list() -> dict:
        chains = _store.list_chains()
        return {"chains": [c.summary() for c in chains]}

    @app.get("/industry-chain/{chain_id}")
    async def industry_chain_detail(chain_id: str) -> dict:
        chain = _store.get_chain(chain_id)
        if chain is None:
            raise HTTPException(status_code=404, detail=f"Chain {chain_id} not found")
        return chain.to_dict()

    @app.post("/industry-chain", dependencies=[Depends(require_auth)])
    async def industry_chain_create(req: CreateChainRequest) -> dict:
        if req.template_key:
            try:
                chain = build_chain_from_template(req.template_key, market=req.market)
            except KeyError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
        elif req.name:
            chain = build_custom_chain(
                req.name,
                req.segment_names or [],
                description=req.description,
                market=req.market,
            )
        else:
            raise HTTPException(
                status_code=400,
                detail="Provide either template_key or name + segment_names.",
            )
        _store.save_chain(chain)
        return {"status": "created", "chain_id": chain.chain_id, "chain": chain.to_dict()}

    @app.put("/industry-chain/{chain_id}", dependencies=[Depends(require_auth)])
    async def industry_chain_update(chain_id: str, req: UpdateChainRequest) -> dict:
        chain = _store.get_chain(chain_id)
        if chain is None:
            raise HTTPException(status_code=404, detail=f"Chain {chain_id} not found")
        if req.name is not None:
            chain.name = req.name
        if req.description is not None:
            chain.description = req.description
        if req.market is not None:
            chain.market = req.market
        if req.segments is not None:
            chain.segments = [Segment.from_dict(s) for s in req.segments]
        _store.save_chain(chain)
        return {"status": "updated", "chain": chain.to_dict()}

    @app.delete("/industry-chain/{chain_id}", dependencies=[Depends(require_auth)])
    async def industry_chain_delete(chain_id: str) -> dict:
        if not _store.delete_chain(chain_id):
            raise HTTPException(status_code=404, detail=f"Chain {chain_id} not found")
        return {"status": "deleted", "chain_id": chain_id}

    @app.post("/industry-chain/{chain_id}/analyze", dependencies=[Depends(require_auth)])
    async def industry_chain_analyze(chain_id: str, req: AnalyzeRequest) -> dict:
        chain = _store.get_chain(chain_id)
        if chain is None:
            raise HTTPException(status_code=404, detail=f"Chain {chain_id} not found")

        market = req.market or chain.market
        segments_csv = ",".join(s.name for s in chain.segments)
        user_vars = {"topic": chain.name, "market": market, "segments": segments_csv}

        runtime = get_swarm_runtime()
        try:
            run = runtime.start_run(_PRESET_NAME, user_vars)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        chain.swarm_run_id = run.id
        chain.status = STATUS_ANALYZING
        _store.save_chain(chain)
        return {"status": "analyzing", "chain_id": chain_id, "run_id": run.id}

    @app.get("/industry-chain/{chain_id}/status", dependencies=[Depends(require_auth)])
    async def industry_chain_status(chain_id: str) -> dict:
        chain = _store.get_chain(chain_id)
        if chain is None:
            raise HTTPException(status_code=404, detail=f"Chain {chain_id} not found")
        if not chain.swarm_run_id:
            return {"chain_id": chain_id, "status": chain.status, "run_id": ""}

        runtime = get_swarm_runtime()
        loaded = runtime._store.load_run(chain.swarm_run_id)
        if loaded is None:
            return {"chain_id": chain_id, "status": chain.status, "run_id": chain.swarm_run_id}
        run = runtime._store.reconcile_run(loaded, write=True)
        run_status = run.status.value

        ingested = False
        # On first completion, parse the director output back into segments.
        # Combine all task summaries so the markdown parser has full context.
        if run_status == "completed" and chain.status != STATUS_READY:
            all_text = "\n\n".join(t.summary or "" for t in run.tasks if t.summary)
            combined = (run.final_report or "") + "\n\n" + all_text
            if _ingest(chain, combined, final_report=run.final_report or ""):
                chain.status = STATUS_READY
                _store.save_chain(chain)
                _store.append_history(chain_id, _snapshot(chain))
                ingested = True
            else:
                logger.warning("Chain %s: run completed but no structured data found", chain_id)
        elif run_status in ("failed", "cancelled") and chain.status == STATUS_ANALYZING:
            chain.status = STATUS_ERROR
            _store.save_chain(chain)

        return {
            "chain_id": chain_id,
            "status": chain.status,
            "run_id": chain.swarm_run_id,
            "run_status": run_status,
            "ingested": ingested,
            "task_count": len(run.tasks),
            "completed_count": sum(1 for t in run.tasks if t.status.value == "completed"),
        }

    @app.post("/industry-chain/{chain_id}/reingest", dependencies=[Depends(require_auth)])
    async def industry_chain_reingest(chain_id: str) -> dict:
        """Re-parse swarm output into chain segments using the improved parser."""
        chain = _store.get_chain(chain_id)
        if chain is None:
            raise HTTPException(status_code=404, detail=f"Chain {chain_id} not found")
        if not chain.swarm_run_id:
            raise HTTPException(status_code=400, detail="No swarm run to re-ingest")
        runtime = get_swarm_runtime()
        loaded = runtime._store.load_run(chain.swarm_run_id)
        if loaded is None:
            raise HTTPException(status_code=404, detail="Swarm run not found")
        run = runtime._store.reconcile_run(loaded, write=False)
        if run.status.value != "completed":
            raise HTTPException(status_code=400, detail=f"Run status is {run.status.value}, not completed")
        # Reset segment fields so stale data from prior ingests is cleared.
        for seg in chain.segments:
            seg.name_en = ""
            seg.international_competition = ""
            seg.domestic_competition = ""
            seg.barrier_type = ""
            seg.barrier_description = ""
            seg.chokepoint_score = {}
            seg.chokepoint_total = None
            seg.tickers = []
            seg.status = "pending"
        chain.overview.core_targets = []
        all_text = "\n\n".join(t.summary or "" for t in run.tasks if t.summary)
        combined = (run.final_report or "") + "\n\n" + all_text
        ok = _ingest(chain, combined, final_report=run.final_report or "")
        if ok:
            chain.status = STATUS_READY
            _store.save_chain(chain)
        return {"chain_id": chain_id, "ingested": ok}

    @app.get("/industry-chain/{chain_id}/history", dependencies=[Depends(require_auth)])
    async def industry_chain_history(chain_id: str) -> dict:
        chain = _store.get_chain(chain_id)
        if chain is None:
            raise HTTPException(status_code=404, detail=f"Chain {chain_id} not found")
        return {"chain_id": chain_id, "snapshots": _store.load_history(chain_id)}

    @app.get("/industry-chain/{chain_id}/swarm-detail", dependencies=[Depends(require_auth)])
    async def industry_chain_swarm_detail(chain_id: str) -> dict:
        """Return structured swarm analysis detail: per-task summaries,
        quality grades, and cross-validation results extracted from events."""
        chain = _store.get_chain(chain_id)
        if chain is None:
            raise HTTPException(status_code=404, detail=f"Chain {chain_id} not found")
        if not chain.swarm_run_id:
            return {"chain_id": chain_id, "run_id": "", "tasks": [], "quality": [], "cross_validation": []}

        runtime = get_swarm_runtime()
        loaded = runtime._store.load_run(chain.swarm_run_id)
        if loaded is None:
            return {"chain_id": chain_id, "run_id": chain.swarm_run_id, "tasks": [], "quality": [], "cross_validation": []}

        run = runtime._store.reconcile_run(loaded, write=False)
        events = runtime._store.read_events(run.id)

        agent_map = {a.id: a.role for a in run.agents}
        tasks_out = []
        for t in run.tasks:
            tasks_out.append({
                "task_id": t.id,
                "agent_id": t.agent_id,
                "agent_role": agent_map.get(t.agent_id, ""),
                "status": t.status.value,
                "summary_preview": (t.summary or "")[:500],
                "started_at": t.started_at,
                "completed_at": t.completed_at,
            })

        quality_events = []
        xval_events = []
        for ev in events:
            if ev.type == "quality_scored":
                quality_events.append({
                    "task_id": ev.task_id,
                    "agent_id": ev.agent_id,
                    "agent_role": agent_map.get(ev.agent_id or "", ""),
                    "grade": (ev.data or {}).get("grade", ""),
                    "issues": (ev.data or {}).get("issues", []),
                    "timestamp": ev.timestamp,
                })
            elif ev.type == "cross_validation_result":
                quality_events.append({
                    "task_id": ev.task_id,
                    "agent_id": ev.agent_id,
                    "type": "cross_validation",
                    "contradictions_count": (ev.data or {}).get("contradictions_count", 0),
                    "consensus_count": (ev.data or {}).get("consensus_count", 0),
                    "blind_spots_count": (ev.data or {}).get("blind_spots_count", 0),
                    "timestamp": ev.timestamp,
                })
                xval_events.append({
                    "contradictions_count": (ev.data or {}).get("contradictions_count", 0),
                    "consensus_count": (ev.data or {}).get("consensus_count", 0),
                    "blind_spots_count": (ev.data or {}).get("blind_spots_count", 0),
                    "high_severity": (ev.data or {}).get("high_severity_count", 0),
                    "timestamp": ev.timestamp,
                })

        return {
            "chain_id": chain_id,
            "run_id": chain.swarm_run_id,
            "run_status": run.status.value,
            "tasks": tasks_out,
            "quality": quality_events,
            "cross_validation": xval_events,
            "final_report_length": len(run.final_report or ""),
            "total_tokens": {
                "input": run.total_input_tokens,
                "output": run.total_output_tokens,
            },
        }

    @app.put("/industry-chain/{chain_id}/schedule", dependencies=[Depends(require_auth)])
    async def industry_chain_schedule(chain_id: str, req: ScheduleRequest) -> dict:
        """Set or clear the periodic refresh schedule for a chain."""
        chain = _store.get_chain(chain_id)
        if chain is None:
            raise HTTPException(status_code=404, detail=f"Chain {chain_id} not found")
        if req.schedule and req.schedule not in ("weekly", "monthly"):
            raise HTTPException(status_code=400, detail="schedule must be '', 'weekly', or 'monthly'")
        chain.refresh_schedule = req.schedule
        _store.save_chain(chain)
        return {"status": "updated", "chain_id": chain_id, "refresh_schedule": chain.refresh_schedule}

    @app.get("/industry-chain/compare", dependencies=[Depends(require_auth)])
    async def industry_chain_compare(ids: str = "") -> dict:
        """Compare multiple chains side-by-side.

        Query param ``ids`` is a comma-separated list of chain_ids. Returns
        each chain's overview + per-segment chokepoint totals + shared tickers.
        """
        chain_ids = [cid.strip() for cid in ids.split(",") if cid.strip()]
        if len(chain_ids) < 2:
            raise HTTPException(status_code=400, detail="Provide at least 2 chain ids (comma-separated)")

        chains_data = []
        all_tickers: dict = {}  # code -> set of chain names
        for cid in chain_ids:
            chain = _store.get_chain(cid)
            if chain is None:
                continue
            segment_scores = {s.name: s.chokepoint_total for s in chain.segments if s.chokepoint_total is not None}
            chains_data.append({
                "chain_id": chain.chain_id,
                "name": chain.name,
                "status": chain.status,
                "lifecycle_stage": chain.overview.lifecycle_stage,
                "prosperity_score": chain.overview.prosperity_score,
                "sector_score": chain.overview.sector_score,
                "segment_scores": segment_scores,
                "segment_names": [s.name for s in chain.segments],
            })
            for seg in chain.segments:
                for tk in seg.tickers:
                    if tk.code not in all_tickers:
                        all_tickers[tk.code] = {"code": tk.code, "name": tk.name, "chains": []}
                    all_tickers[tk.code]["chains"].append(chain.name)

        shared_tickers = [v for v in all_tickers.values() if len(v["chains"]) >= 2]
        shared_tickers.sort(key=lambda x: len(x["chains"]), reverse=True)

        return {
            "chains": chains_data,
            "shared_tickers": shared_tickers[:20],
        }

    # -- Hypothesis management (tags hypotheses by chain name in universe) --

    def _chain_tag(chain: Chain) -> str:
        """Universe tag used to associate hypotheses with a chain."""
        return f"chain:{chain.chain_id}:{chain.name}"

    @app.get("/industry-chain/{chain_id}/hypotheses", dependencies=[Depends(require_auth)])
    async def industry_chain_hypotheses(chain_id: str) -> dict:
        """List hypotheses linked to this chain."""
        chain = _store.get_chain(chain_id)
        if chain is None:
            raise HTTPException(status_code=404, detail=f"Chain {chain_id} not found")
        tag = _chain_tag(chain)
        all_hyps = _hyp_registry.search(query="", status=None)
        linked = [h.to_dict() for h in all_hyps if tag in (h.universe or "")]
        return {"chain_id": chain_id, "hypotheses": linked}

    @app.post("/industry-chain/{chain_id}/hypotheses", dependencies=[Depends(require_auth)])
    async def industry_chain_create_hypothesis(chain_id: str, req: CreateHypothesisRequest) -> dict:
        """Create a hypothesis linked to this chain."""
        chain = _store.get_chain(chain_id)
        if chain is None:
            raise HTTPException(status_code=404, detail=f"Chain {chain_id} not found")
        hyp = _hyp_registry.create(
            title=req.title,
            thesis=req.thesis,
            status=req.status,
            universe=_chain_tag(chain),
            invalidation_notes=req.invalidation_notes,
        )
        return {"status": "created", "hypothesis": hyp.to_dict()}

    @app.get("/industry-chain/{chain_id}/export", dependencies=[Depends(require_auth)])
    async def industry_chain_export(chain_id: str) -> dict:
        """Export a chain as a structured Markdown research report."""
        chain = _store.get_chain(chain_id)
        if chain is None:
            raise HTTPException(status_code=404, detail=f"Chain {chain_id} not found")
        return {"chain_id": chain_id, "filename": f"{chain.name}_研报.md", "markdown": _render_markdown(chain)}
