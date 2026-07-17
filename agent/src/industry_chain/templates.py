"""Pre-built industry-chain templates (the reusable skeletons from Simon's
tutorial — EP03 humanoid robot, EP05 AI computing — plus two more 2026 A-share
mainlines).

A template defines a chain's *segment structure* (环节骨架). Seed tickers are a
convenience starting point pulled from the pre-built supply-chain universe where
a clean A-share match exists; everything else is left for the swarm's
``discover`` step to fill during analysis. Custom chains skip templates entirely
and hand a free-text segment list to the same discovery path.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from src.industry_chain.store import Chain, ChainOverview, Segment, Ticker
from src.tools._supply_chain_universe import get_universe

# Each template: human name + ordered segment definitions. A segment def is
# (name, positioning, seed sub_sectors). Seed sub_sectors are looked up in the
# universe to pre-populate tickers; an empty tuple means "discover at analysis".
_TEMPLATES: Dict[str, Dict[str, object]] = {
    "humanoid_robot": {
        "name": "人形机器人",
        "name_en": "Humanoid Robot",
        "description": "人形机器人产业链：从核心零部件到本体集成的卡脖子环节梳理。",
        "segments": [
            ("谐波减速器", "中游", ("robotics",)),
            ("行星滚柱丝杠", "中游", ()),
            ("无框力矩电机", "中游", ("robotics",)),
            ("六维力传感器", "中游", ()),
            ("灵巧手", "下游", ()),
            ("滚珠丝杠", "中游", ()),
        ],
    },
    "ai_computing": {
        "name": "AI算力",
        "name_en": "AI Computing",
        "description": "AI算力产业链：算力芯片、光互联、存储、散热等卡脖子环节。",
        "segments": [
            ("算力芯片", "上游", ("gpu", "ai_chip")),
            ("HBM", "上游", ("memory",)),
            ("光模块", "中游", ("optical_module",)),
            ("PCB", "中游", ("pcb",)),
            ("交换芯片", "中游", ("switch",)),
            ("液冷散热", "中游", ("power_cooling", "thermal")),
            ("先进封装", "上游", ("advanced_packaging",)),
            ("AI服务器", "下游", ("ai_server",)),
        ],
    },
    "nev": {
        "name": "新能源车",
        "name_en": "New Energy Vehicle",
        "description": "新能源车产业链：三电系统、热管理、车规芯片到智能驾驶。",
        "segments": [
            ("动力电池", "中游", ()),
            ("驱动电机", "中游", ()),
            ("电控系统", "中游", ()),
            ("热管理", "中游", ()),
            ("车规芯片", "上游", ("power_semi",)),
            ("智能驾驶", "下游", ()),
            ("充电桩", "下游", ()),
        ],
    },
    "semiconductor": {
        "name": "半导体",
        "name_en": "Semiconductor",
        "description": "半导体产业链：从EDA、设计、制造到封测与设备材料的国产替代。",
        "segments": [
            ("芯片设计", "上游", ("ai_chip", "gpu")),
            ("晶圆制造", "中游", ()),
            ("封装测试", "中游", ("advanced_packaging",)),
            ("半导体设备", "上游", ("equipment", "test_equipment")),
            ("半导体材料", "上游", ("materials",)),
            ("EDA工具", "上游", ()),
        ],
    },
}


def list_templates() -> List[Dict[str, str]]:
    """Return template metadata for the create-chain picker.

    Returns:
        One dict per template with ``key``, ``name``, ``name_en``,
        ``description`` and ``segment_count``.
    """
    result: List[Dict[str, str]] = []
    for key, tpl in _TEMPLATES.items():
        segments = tpl["segments"]  # type: ignore[index]
        result.append(
            {
                "key": key,
                "name": str(tpl["name"]),
                "name_en": str(tpl["name_en"]),
                "description": str(tpl["description"]),
                "segment_count": len(segments),  # type: ignore[arg-type]
            }
        )
    return result


def _seed_tickers(sub_sectors: tuple, market: str, limit: int = 4) -> List[Ticker]:
    """Pull seed tickers from the universe for the given sub-sectors.

    Args:
        sub_sectors: Universe sub_sector keys to draw from.
        market: Market filter (``"A"``/``"US"``/``"HK"``).
        limit: Max tickers per segment to avoid bloating the skeleton.

    Returns:
        Deduplicated Ticker list (may be empty when no universe match exists).
    """
    seen: set = set()
    tickers: List[Ticker] = []
    for sub in sub_sectors:
        for row in get_universe(sub_sector=sub, market=market):
            code = row["code"]
            if code in seen:
                continue
            seen.add(code)
            tickers.append(
                Ticker(
                    code=code,
                    name=row["name"],
                    market=row["market"],
                    key_products=row["key_products"],
                )
            )
            if len(tickers) >= limit:
                return tickers
    return tickers


def build_chain_from_template(key: str, market: str = "A") -> Chain:
    """Build a draft Chain from a named template.

    Args:
        key: Template key (e.g. ``"humanoid_robot"``).
        market: Target market for seed tickers.

    Returns:
        A draft Chain with segment skeletons and seed tickers.

    Raises:
        KeyError: When the template key is unknown.
    """
    if key not in _TEMPLATES:
        raise KeyError(f"Unknown template: {key}. Available: {list(_TEMPLATES)}")
    tpl = _TEMPLATES[key]
    segment_defs = tpl["segments"]  # type: ignore[index]

    segments: List[Segment] = []
    for order, (name, positioning, sub_sectors) in enumerate(segment_defs, start=1):  # type: ignore[misc]
        seeds = _seed_tickers(sub_sectors, market)
        segments.append(
            Segment(
                name=name,
                order=order,
                positioning=positioning,
                tickers=seeds,
                status="partial" if seeds else "empty",
            )
        )

    return Chain(
        name=str(tpl["name"]),
        name_en=str(tpl["name_en"]),
        description=str(tpl["description"]),
        market=market,
        template_key=key,
        overview=ChainOverview(),
        segments=segments,
    )


def build_custom_chain(
    name: str,
    segment_names: List[str],
    *,
    description: str = "",
    market: str = "A",
) -> Chain:
    """Build a draft Chain from a custom name + segment list.

    No seed tickers are attached; the swarm's discover step populates them at
    analysis time.

    Args:
        name: Chain display name (e.g. ``"低空经济"``).
        segment_names: Ordered segment names. May be empty (the swarm can then
            discover the whole segment structure from the chain name alone).
        description: Optional description.
        market: Target market.

    Returns:
        A draft custom Chain (``template_key`` is empty).
    """
    segments = [
        Segment(name=seg_name.strip(), order=order)
        for order, seg_name in enumerate(segment_names, start=1)
        if seg_name.strip()
    ]
    return Chain(
        name=name.strip(),
        description=description,
        market=market,
        template_key="",
        overview=ChainOverview(),
        segments=segments,
    )
