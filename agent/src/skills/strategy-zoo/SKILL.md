---
name: strategy-zoo
description: Browse, select, and run pre-built quantitative trading strategies from the strategy zoo. Covers trend-following, mean-reversion, momentum, multi-factor, stat-arb, event-driven, volatility, allocation, crypto, and options strategies.
category: strategy
---

## When to Use

Use this skill when the user wants to:
- Browse available pre-built strategies ("有哪些策略"/"show me strategies")
- Find strategies matching specific criteria ("适合A股的低风险策略")
- Run a pre-built strategy backtest ("用双均线策略回测000001.SZ")
- Compare multiple pre-built strategies on the same instruments
- Get strategy recommendations based on market and risk preference

**Distinction from `strategy-generate`:**
- `strategy-zoo` = select and run a **pre-built** strategy from the library
- `strategy-generate` = write a **new custom** strategy from scratch

## Available Categories

| Category | ID | Count | Description |
|----------|----|-------|-------------|
| 趋势跟踪 | `trend` | 5 | 双均线、唐奇安、海龟、ADX、SuperTrend |
| 均值回归 | `mean_reversion` | 4 | 布林带、RSI、Z-Score、OU过程 |
| 动量 | `momentum` | 4 | 横截面动量、TSMOM、52周高、双动量 |
| 多因子 | `multi_factor` | 4 | 质量价值、因子轮动、Alpha组合、基本面选股 |
| 统计套利 | `stat_arb` | 4 | 协整配对、市场中性、ETF套利、AH溢价 |
| 事件驱动 | `event_driven` | 4 | PEAD、龙虎榜、北向资金、打板 |
| 波动率 | `volatility` | 3 | 波动突破、GARCH、VRP |
| 组合配置 | `allocation` | 4 | 风险平价、全天候、Black-Litterman、Kelly |
| 加密货币 | `crypto` | 4 | 资金费率、网格、DeFi轮动、链上指标 |
| 期权 | `options` | 4 | 备兑看涨、铁鹰、波动微笑、卖出看跌 |

## Workflow

### 1. Browse / Filter

```python
from src.strategies.registry import get_default_registry

reg = get_default_registry()

# List all
all_ids = reg.list()

# Filter by category
trend_ids = reg.list(category="trend")

# Filter by market + risk
cn_low = reg.list(universe="equity_cn", risk="low")

# Get strategy detail
meta = reg.get("trend_dual_ma").meta
```

### 2. Run Single Strategy

```python
from src.strategies.runner import run

result = run(
    "trend_dual_ma",
    codes=["000001.SZ", "600036.SH"],
    start_date="2020-01-01",
    end_date="2025-01-01",
    params={"fast_period": 10, "slow_period": 30},  # override defaults
)
# result["run_dir"] contains config.json + signal_engine.py
# Then call the backtest tool on that run_dir
```

After `run()` creates the run_dir, execute the backtest:
```bash
cd <run_dir> && python -m backtest
```

### 3. Compare Multiple Strategies

```python
from src.strategies.runner import compare

results = compare(
    ["trend_dual_ma", "mr_bollinger", "mom_tsmom"],
    codes=["000001.SZ"],
    start_date="2020-01-01",
    end_date="2025-01-01",
)
# Each result has its own run_dir; run backtest on each, then compare metrics
```

### 4. Get Recommendations

```python
from src.strategies.runner import recommend

recs = recommend(universe="equity_cn", risk="low")
# Returns list of strategy metadata dicts
```

## CLI Usage

```bash
# List strategies
strategy list
strategy list --category trend
strategy list --universe equity_cn --risk low

# Show strategy details
strategy info trend_dual_ma

# Run backtest
strategy run trend_dual_ma --codes 000001.SZ 600036.SH --params fast_period=10

# Compare strategies
strategy compare trend_dual_ma,mr_bollinger,mom_tsmom --codes 000001.SZ
```

## Integration Points

- **Factor Zoo**: Multi-factor strategies can reference factors via `factors_used` field
- **Hypothesis Registry**: `run()` output links to hypothesis tracking
- **Backtest Engine**: All strategies produce `SignalEngine` compatible with the existing backtest engine
- **Data Skills**: Event-driven strategies leverage tushare/eastmoney data skills

## Adding New Strategies

Create a new `.py` file in the appropriate `zoo/<category>/` directory with:
1. `__strategy_meta__` dict literal (AST-parseable)
2. `SignalEngine` class with `generate(data_map) -> Dict[str, pd.Series]`
3. Signals in [-1.0, 1.0], NaN-safe, no lookahead bias

The registry auto-discovers new files on startup.
