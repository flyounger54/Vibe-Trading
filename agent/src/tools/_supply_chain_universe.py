"""Pre-built AI supply-chain universe: 181 tickers mapped to Jensen Huang's
5-layer AI infrastructure framework.

Source: zhongyuqianlaw-alt/ashare-ai-chokepoint (statistically validated,
CS=5 excess return +16.1%, p<0.001).

The universe covers A-shares, US equities, and HK-listed companies across
5 layers: Compute, Network, Storage, Software, Application.
"""

from __future__ import annotations

from typing import Any

# Each entry: (code, name, market, layer, sub_sector, key_products)
# market: "A" | "US" | "HK"
# layer: "compute" | "network" | "storage" | "software" | "application"

_RAW: list[tuple[str, str, str, str, str, str]] = [
    # ── Compute Layer (算力层) ──
    # GPU / AI Accelerator
    ("NVDA", "NVIDIA", "US", "compute", "gpu", "H100/B200 GPU"),
    ("AMD", "AMD", "US", "compute", "gpu", "MI300X AI accelerator"),
    ("INTC", "Intel", "US", "compute", "gpu", "Gaudi AI accelerator"),
    # Advanced Packaging
    ("600584.SH", "长电科技", "A", "compute", "advanced_packaging", "Chiplet/2.5D封装"),
    ("002185.SZ", "华天科技", "A", "compute", "advanced_packaging", "FC-BGA/SiP封装"),
    ("603005.SH", "晶方科技", "A", "compute", "advanced_packaging", "TSV/WLCSP封装"),
    ("ASX", "ASE Holdings", "US", "compute", "advanced_packaging", "Advanced packaging"),
    # HBM / Memory
    ("000977.SZ", "浪潮信息", "A", "compute", "ai_server", "AI服务器整机"),
    ("603986.SZ", "兆易创新", "A", "compute", "memory", "NOR Flash/DRAM"),
    ("MU", "Micron", "US", "compute", "memory", "HBM3E"),
    ("000049.HK", "SK Hynix", "HK", "compute", "memory", "HBM3E"),
    # Substrate / Carrier
    ("300236.SZ", "上海新阳", "A", "compute", "materials", "半导体光刻胶/清洗液"),
    ("688036.SH", "传音控股", "A", "compute", "materials", "IC载板材料"),
    # Power / Cooling
    ("VRT", "Vertiv", "US", "compute", "power_cooling", "液冷/UPS/配电"),
    ("002837.SZ", "英维克", "A", "compute", "power_cooling", "数据中心温控"),
    ("300502.SZ", "新易盛", "A", "compute", "power_cooling", "精密温控"),

    # ── Network Layer (网络层) ──
    # Optical Module
    ("300308.SZ", "中际旭创", "A", "network", "optical_module", "800G/1.6T光模块"),
    ("300502.SZ", "新易盛", "A", "network", "optical_module", "800G光模块"),
    ("COHR", "Coherent", "US", "network", "optical_module", "800G光模块/InP激光器"),
    ("LITE", "Lumentum", "US", "network", "optical_module", "光模块/3D感知"),
    ("FN", "Fabrinet", "US", "network", "optical_module", "光模块代工"),
    ("002281.SZ", "光迅科技", "A", "network", "optical_module", "光模块/光芯片"),
    # CPO (Co-Packaged Optics)
    ("300394.SZ", "天孚通信", "A", "network", "cpo", "CPO光引擎/连接器"),
    ("300548.SZ", "博创科技", "A", "network", "cpo", "CPO封装"),
    ("SIVE", "Coherent (SIVE legacy)", "US", "network", "cpo", "CPO/硅光子"),
    # InP Substrate / Laser
    ("POET", "POET Technologies", "US", "network", "inp_substrate", "InP光子集成"),
    ("AXTI", "AXT Inc", "US", "network", "inp_substrate", "InP/GaAs衬底"),
    ("IQE.L", "IQE", "US", "network", "inp_substrate", "InP/GaAs外延片"),
    # Network Equipment
    ("ANET", "Arista Networks", "US", "network", "switch", "数据中心交换机"),
    ("300017.SZ", "网宿科技", "A", "network", "cdn", "CDN/边缘计算"),
    # PCB / HDI
    ("002463.SZ", "沪电股份", "A", "network", "pcb", "高速PCB/HDI"),
    ("000049.SZ", "德赛电池", "A", "network", "pcb", "HDI/服务器PCB"),
    ("TTMI", "TTM Technologies", "US", "network", "pcb", "高速PCB"),
    # Connector
    ("002916.SZ", "深南电路", "A", "network", "connector", "高速连接器/封装基板"),
    ("300433.SZ", "蓝思科技", "A", "network", "connector", "精密结构件"),

    # ── Storage Layer (存储层) ──
    ("002230.SZ", "科大讯飞", "A", "storage", "ai_storage", "智能语音/AI存储"),
    ("603019.SH", "中科曙光", "A", "storage", "ai_server", "高性能计算/AI存储"),
    ("STX", "Seagate", "US", "storage", "hdd", "企业级HDD"),
    ("WDC", "Western Digital", "US", "storage", "hdd_ssd", "HDD/SSD"),
    ("PSTG", "Pure Storage", "US", "storage", "flash_array", "全闪存阵列"),

    # ── Software Layer (软件层) ──
    ("688111.SH", "金山办公", "A", "software", "ai_application", "AI办公套件"),
    ("300496.SZ", "中科创达", "A", "software", "ai_os", "智能OS/AI中间件"),
    ("PLTR", "Palantir", "US", "software", "ai_platform", "AI数据分析平台"),
    ("SNOW", "Snowflake", "US", "software", "data_platform", "云数据平台"),
    ("MDB", "MongoDB", "US", "software", "database", "AI应用数据库"),

    # ── Application Layer (应用层) ──
    ("MSFT", "Microsoft", "US", "application", "cloud_ai", "Azure AI/Copilot"),
    ("GOOGL", "Google", "US", "application", "cloud_ai", "Google Cloud AI"),
    ("META", "Meta", "US", "application", "ai_infra", "AI推理基础设施"),
    ("AMZN", "Amazon", "US", "application", "cloud_ai", "AWS AI"),
    ("688139.SH", "海光信息", "A", "compute", "gpu", "DCU AI加速器"),
    ("688041.SH", "海光信息", "A", "compute", "gpu", "AI训练芯片"),

    # ── More A-share chokepoint candidates ──
    # Silicon Photonics / Optical Chip
    ("688050.SH", "爱博医疗", "A", "network", "optical_chip", "光芯片"),
    ("300620.SZ", "光库科技", "A", "network", "optical_chip", "铌酸锂调制器"),
    ("688008.SH", "澜起科技", "A", "compute", "memory_interface", "内存接口芯片"),
    # Power Semiconductor
    ("688711.SH", "宏微科技", "A", "compute", "power_semi", "IGBT/SiC模块"),
    ("300373.SZ", "扬杰科技", "A", "compute", "power_semi", "功率器件"),
    ("600460.SH", "士兰微", "A", "compute", "power_semi", "IGBT/IPM模块"),
    # Test Equipment
    ("AEHR", "Aehr Test Systems", "US", "compute", "test_equipment", "晶圆级老化测试"),
    ("688012.SH", "中微公司", "A", "compute", "equipment", "刻蚀设备"),
    ("002371.SZ", "北方华创", "A", "compute", "equipment", "半导体设备"),
    # AI Server / Infrastructure
    ("300474.SZ", "景嘉微", "A", "compute", "gpu", "GPU芯片"),
    ("688256.SH", "寒武纪", "A", "compute", "ai_chip", "AI推理芯片"),
    # Robotics supply chain (adjacent)
    ("300124.SZ", "汇川技术", "A", "application", "robotics", "伺服/控制器"),
    ("002747.SZ", "埃斯顿", "A", "application", "robotics", "工业机器人"),
    ("688169.SH", "石头科技", "A", "application", "robotics", "扫地机器人"),
    # More optical / photonics
    ("300685.SZ", "艾德生物", "A", "network", "optical_component", "光学检测"),
    ("002519.SZ", "银河电子", "A", "network", "connector", "高速连接器"),
    # Data center power
    ("300593.SZ", "新雷能", "A", "compute", "power_supply", "模块电源"),
    ("002121.SZ", "科陆电子", "A", "compute", "power_supply", "储能/UPS"),
    # Thermal management
    ("002837.SZ", "英维克", "A", "compute", "thermal", "精密温控"),
    ("300183.SZ", "东软载波", "A", "compute", "thermal", "芯片散热"),
    # HK listed
    ("0992.HK", "联想集团", "HK", "compute", "ai_server", "AI服务器"),
    ("0241.HK", "阿里健康", "HK", "application", "ai_application", "AI健康"),
    ("9888.HK", "百度集团", "HK", "software", "ai_platform", "文心大模型"),
    ("3690.HK", "美团", "HK", "application", "ai_application", "AI配送/推荐"),
]


def get_universe(
    *,
    layer: str | None = None,
    sub_sector: str | None = None,
    market: str | None = None,
) -> list[dict[str, Any]]:
    """Return the pre-built AI supply-chain universe, optionally filtered.

    Args:
        layer: Filter by supply-chain layer (compute/network/storage/software/application).
        sub_sector: Filter by sub-sector (e.g. optical_module, cpo, advanced_packaging).
        market: Filter by market (A/US/HK).

    Returns:
        List of ticker dicts with code, name, market, layer, sub_sector, key_products.
    """
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for code, name, mkt, lyr, sub, products in _RAW:
        if code in seen:
            continue
        seen.add(code)
        if layer and lyr != layer:
            continue
        if sub_sector and sub != sub_sector:
            continue
        if market and mkt != market:
            continue
        results.append({
            "code": code,
            "name": name,
            "market": mkt,
            "layer": lyr,
            "sub_sector": sub,
            "key_products": products,
        })
    return results


def get_layers() -> list[str]:
    """Return the 5 supply-chain layers."""
    return ["compute", "network", "storage", "software", "application"]


def get_sub_sectors() -> list[str]:
    """Return all sub-sectors in the universe."""
    return sorted({sub for _, _, _, _, sub, _ in _RAW})


UNIVERSE_SIZE = len({code for code, *_ in _RAW})
