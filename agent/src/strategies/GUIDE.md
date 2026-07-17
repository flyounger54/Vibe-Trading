# Strategy Zoo 操作指南

## 1. 访问入口

打开 Vibe-Trading 前端后，在左侧边栏点击 **Strategy Zoo**（位于 Alpha Zoo 下方）即可进入策略动物园。

> URL: `http://localhost:5180/strategy-zoo`（开发模式）或 `http://localhost:8888/strategy-zoo`（生产模式）

---

## 2. 浏览页功能

### 2.1 分类卡片

页面顶部展示 **10 个分类卡片**，每个卡片显示：
- 分类中文名（趋势跟踪、均值回归、动量策略 等）
- 该分类下的策略数量
- 包含策略的英文简介

**操作：** 点击卡片可快速筛选该分类的策略。再次点击同一卡片取消筛选。

| 分类 | 策略数 | 代表策略 |
|------|--------|---------|
| 趋势跟踪 | 5 | 双均线交叉、唐奇安通道、海龟交易系统、ADX趋势、SuperTrend |
| 均值回归 | 4 | 布林带回归、RSI超买超卖、Z-Score回归、OU过程 |
| 动量策略 | 4 | 横截面动量、时间序列动量、52周新高、双动量 |
| 多因子 | 4 | 质量+价值组合、基本面选股、因子轮动、Alpha Zoo组合 |
| 统计套利 | 4 | 协整配对、市场中性、AH股溢价、ETF折溢价 |
| 事件驱动 | 4 | 盈余公告漂移、龙虎榜跟踪、北向资金、打板策略 |
| 波动率 | 3 | 波动率突破、GARCH交易、波动率风险溢价 |
| 组合配置 | 4 | 风险平价、全天候、Black-Litterman、Kelly仓位 |
| 加密货币 | 4 | 资金费率套利、网格交易、DeFi收益轮动、链上指标 |
| 期权策略 | 4 | 备兑看涨、铁鹰组合、波动率微笑、卖出看跌 |

### 2.2 筛选栏

四个维度的筛选器，可任意组合：

| 筛选器 | 说明 | 示例 |
|--------|------|------|
| **Search** | 按策略 ID、中文昵称或英文描述模糊搜索 | 输入「海龟」→ 匹配 trend_turtle |
| **Category** | 按分类下拉筛选 | 选择「趋势跟踪」→ 只显示 5 个趋势策略 |
| **Universe** | 按适用市场筛选 | 选择「A股 (China A)」→ 显示所有支持 A 股的策略 |
| **Risk** | 按风险等级筛选 | 选择「低风险 (Low)」→ 显示 10 个低风险策略 |

**操作提示：**
- 搜索支持中英文，实时过滤
- 分类卡片点击与 Category 下拉联动
- 多个筛选器可叠加使用（如：Category=趋势 + Risk=低风险 → 只显示 trend_dual_ma）

### 2.3 策略列表表格

| 列 | 说明 |
|----|------|
| **ID** | 策略唯一标识（可点击进入详情页），右侧显示中文昵称 |
| **Category** | 分类标签（彩色徽章） |
| **Universe** | 适用市场列表 |
| **Risk** | 风险等级徽章（绿色=low, 黄色=medium, 红色=high） |
| **Reference** | 学术/经典来源 |

---

## 3. 详情页功能

点击策略列表中的任意策略 ID 链接，进入策略详情页。

### 3.1 标题区域

- 策略 ID（等宽字体）
- 分类标签（如「趋势跟踪」）
- 风险等级徽章（带图标：🛡 low / ⚠️ medium / 🔴 high）
- 中文昵称
- 英文策略描述

### 3.2 元数据表格

| 字段 | 说明 | 示例 |
|------|------|------|
| Category | 策略分类 | trend |
| Universe | 适用市场 | equity_us, equity_cn, crypto |
| Frequency | 交易频率 | 1D |
| Risk | 风险等级 | low / medium / high |
| Min bars | 策略需要的最小K线数 | 30 |
| Columns required | 需要的数据列 | close, high, low |
| Default params | 默认参数 | {"fast_period": 5, "slow_period": 20} |
| Factors used | 关联的因子库因子 | alpha101_001 |
| Reference | 学术/经典来源 | Curtis Faith, Way of the Turtle, 2007 |
| Module path | Python 模块路径 | src.strategies.zoo.trend.turtle |

### 3.3 源代码查看

点击 **▶ View source (N lines)** 可展开/折叠完整的策略 Python 源码。

源码包括：
- `__strategy_meta__` 元数据字典
- `SignalEngine` 类的完整实现
- `generate()` 方法的信号生成逻辑

### 3.4 导航

- 点击页面顶部 **← Back to Strategy Zoo** 返回浏览页

---

## 4. 按市场查找策略

### A股策略（equity_cn）

```
筛选: Universe = A股 (China A)
```

推荐组合：
- 低风险：alloc_risk_parity + mf_quality_value + mr_bollinger
- 中风险：mom_cross_section + sa_coint_pair + ev_northbound
- 高风险：ev_dragon_tiger + ev_limit_board

### 美股策略（equity_us）

```
筛选: Universe = 美股 (US Equity)
```

推荐组合：
- 低风险：alloc_all_weather + opt_covered_call + sa_market_neutral
- 中风险：mom_tsmom + alloc_black_litterman + opt_iron_condor

### 加密货币策略（crypto）

```
筛选: Universe = 加密货币 (Crypto)
```

推荐：crypto_funding_arb + crypto_grid + trend_turtle

---

## 5. 按风险偏好选择

| 风险等级 | 数量 | 代表策略 |
|---------|------|---------|
| 低风险 (low) | 10 | alloc_risk_parity, mr_bollinger, mf_quality_value, trend_dual_ma, opt_covered_call |
| 中风险 (medium) | 20 | mom_tsmom, sa_coint_pair, alloc_black_litterman, crypto_grid, trend_donchian |
| 高风险 (high) | 10 | trend_turtle, ev_dragon_tiger, ev_limit_board, crypto_onchain, vol_garch |

---

## 6. 后端 API

Strategy Zoo 提供两个 REST API 端点：

### GET /strategy/list

列出所有策略，支持筛选。

```bash
# 全部策略
curl http://localhost:8888/strategy/list

# 按分类筛选
curl "http://localhost:8888/strategy/list?category=trend"

# 按市场筛选
curl "http://localhost:8888/strategy/list?universe=equity_cn"

# 按风险筛选
curl "http://localhost:8888/strategy/list?risk=low"

# 组合筛选
curl "http://localhost:8888/strategy/list?category=trend&risk=low"

# 限制数量
curl "http://localhost:8888/strategy/list?limit=5"
```

### GET /strategy/{strategy_id}

获取单个策略的详细信息和源码。

```bash
curl http://localhost:8888/strategy/trend_dual_ma
```

---

## 7. Python API

### 浏览策略

```python
from src.strategies.registry import get_default_registry

reg = get_default_registry()

# 列出全部
all_ids = reg.list()  # 40 个策略 ID

# 按分类筛选
trend_ids = reg.list(category="trend")

# 按市场 + 风险筛选
cn_low = reg.list(universe="equity_cn", risk="low")

# 获取策略详情
s = reg.get("trend_dual_ma")
print(s.meta)  # 完整元数据
```

### 运行回测

```python
from src.strategies.runner import run

result = run(
    "trend_dual_ma",
    codes=["000001.SZ", "600036.SH"],
    start_date="2020-01-01",
    end_date="2025-01-01",
    params={"fast_period": 10, "slow_period": 30},  # 可覆盖默认参数
)
# result["run_dir"] 包含 config.json + signal_engine.py
# 然后调用 backtest 工具执行回测
```

### 策略对比

```python
from src.strategies.runner import compare

results = compare(
    ["trend_dual_ma", "mr_bollinger", "mom_tsmom"],
    codes=["000001.SZ"],
    start_date="2020-01-01",
    end_date="2025-01-01",
)
# 每个策略各自生成一个 run_dir，分别执行回测后对比 metrics
```

### 策略推荐

```python
from src.strategies.runner import recommend

recs = recommend(universe="equity_cn", risk="low")
# 返回所有匹配策略的 meta 列表
```

---

## 8. 策略参数说明

每个策略都有可调参数（`default_params`）。查看详情页的 **Default params** 行了解默认值。

常见参数类型：

| 参数类型 | 说明 | 示例 |
|---------|------|------|
| 周期类 | 均线/回看窗口长度 | fast_period=5, lookback=60 |
| 阈值类 | 触发信号的阈值 | entry_z=2.0, adx_threshold=25 |
| 仓位类 | 信号强度/仓位比例 | signal_strength=0.5, kelly_fraction=0.5 |
| 再平衡类 | 调仓频率 | rebalance_days=20 |
| 比例类 | 选股比例 | top_pct=0.2, bottom_pct=0.2 |

运行回测时通过 `params` 参数覆盖默认值即可。
