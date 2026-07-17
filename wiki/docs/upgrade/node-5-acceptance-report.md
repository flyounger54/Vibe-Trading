# Vibe-Trading 升级节点 5 技术验收报告

更新时间：2026-07-17 23:16（Asia/Shanghai）  
分支：`upgrade/node-5-backtest-correctness`  
起点：`1477c0b` / `upgrade-node-4-accepted`  
状态：**实现与技术验收完成；用户已确认节点 5 验收通过**

## 1. 目标与结论

节点 5 的目标是修复回测、组合账本和绩效指标中会导致结果失真的关键路径，并建立可重复验证的正确性边界。实现已完成：数据只拉取和清洗一次并冻结为不可变快照；信号、仓位计算和成交严格遵守 `t` 收盘决策、最早 `t+1` 开盘成交；止损使用确定性的跳空/日内路径；期末先清算再生成最后快照；所有交易与指标统一为净费后基础货币口径；多币种、杠杆、合约乘数、资金费、现金流、停牌、部分成交、退市和缺失末日行情均有明确规则；运行产物具备内容寻址清单。

| 验收面 | 结果 |
|---|---|
| 数据输入 | 单次 fetch；清洗/基本面/事件增强后冻结为不可变 `DataBundle` |
| 时间轴 | `t` close 决策，仓位上下文只能看到 `<=t`，`t+1` open 成交 |
| 止损 | 开盘跳空优先；日内 high/low 触发；同柱冲突策略显式 |
| 期末账本 | 最后一根 bar 清算后再记录快照；现金、仓位、权益守恒 |
| 指标 | PnL、胜率、盈亏比、profit factor 使用净费后结果；bars/days 分离 |
| 多币种 | 基础货币强制；时点 FX；缺 FX 立即失败；期权非基础币种明确拒绝 |
| 市场状态 | 停牌/零量、涨跌停、成交量参与率部分成交、缺失末日、退市显式处理 |
| 衍生品 | 合约乘数、保证金、杠杆、资金费、外汇隔夜息进入统一账本 |
| 失败语义 | 再平衡异常中止整次运行并返回结构化错误，不再 warning 后伪成功 |
| 可复现性 | 数据、配置、策略、费用、执行代码、依赖、产物哈希组成 `run_hash` |
| 未来函数 | 可启用 mutation sentinel；严格模式逐 cutoff 验证未来数据变化不影响过去信号 |
| 正确性核心覆盖率 | line 99.67%，branch 96.67% |
| 后端全量 | 4,426 pass / 2 skip / 0 fail |
| 前端全量 | 28 files / 227 pass / 0 fail；生产构建通过 |

## 2. 分步实现与验收

### 2.1 不可变数据快照与单次获取

- 新增 `backtest.data_bundle.DataBundle`，构造时深复制、读取时返回副本，外部无法改变运行中的数据。
- fingerprint 覆盖 frame 内容、列、索引、attrs、币种、FX 和 schema 版本。
- runner 成为唯一 Provider 获取入口；OHLC 清洗、基本面和事件增强均在冻结前完成。
- daily 和 options engine 只接受 `DataBundle`，不再能二次调用 loader。

### 2.2 决策、成交与止损时间轴

- `_align` 保留 next-bar 语义；仓位计算上下文改为前一根决策 bar，价格历史截止该时点。
- 当前成交 bar 的 close/high/low 不再进入开盘前仓位计算。
- 止损按开盘跳空、开盘成交、日内 high/low 的顺序处理；日内止损按阈值成交，跳空按开盘成交。
- 同柱同时命中止盈和止损时使用显式 `stop_collision_policy`，默认保守的 `stop_first`。
- 资金费、swap 和清算 hook 放到 bar 内开盘成交及止损之后，避免执行顺序倒置。

### 2.3 净费后交易与期末清算

- `TradeRecord.pnl` 统一定义为扣除开仓和退出费用后的净 PnL，同时保留 `gross_pnl` 和总 commission。
- `avg_holding_bars` 与实际日历 `avg_holding_days` 分开，不再用 bar 数冒充天数。
- 最后一根 bar 在权益快照前强制清算；最终 snapshot 必须为零仓位、零未实现收益，equity 等于 cash。
- 部分退出按 size 比例分摊入场保证金、入场费用和 FX 成本，剩余仓位账本继续守恒。

### 2.4 基础货币、FX 与现金账本

- 每个 symbol 从数据元信息读取 instrument currency；组合配置基础货币，多币种缺 FX 时拒绝运行。
- 下单 sizing 使用决策时点 FX，实际保证金、费用、PnL 和资产返还使用各自事件时点 FX。
- FX 变动产生的基础货币收益进入 trade gross/net PnL，不会直接相加 CNY/HKD/USD/USDT。
- 资金费、外汇隔夜息和外部注资/提款写入 `cash_ledger.csv`，包含本币金额、FX、基础货币金额和事件类型。
- 期权引擎当前只允许 underlying currency 等于 base currency；不支持的跨币种组合明确失败，避免静默错算。

### 2.5 市场状态与失败边界

- 明确 suspension/halted/零 volume session 不可成交；A 股和期货涨跌停规则继续由市场引擎执行。
- 可配置 `max_volume_participation`，开仓、加仓、减仓、信号退出和止损退出均受成交量上限约束。
- 显式 `delisting_date` 在最后发布价格清算；末日缺 bar 可选择 `last_known` 或严格 `error` 策略。
- 再平衡异常包装为 `BacktestExecutionError`，CLI 输出 code/message/symbol/timestamp 并以失败退出。

### 2.6 可复现清单与未来函数哨兵

- 新增 `run_manifest.json`，稳定记录 data/config/strategy/fee/execution/dependency/artifact hash，并生成聚合 `run_hash`。
- manifest 排除生成时间和绝对目录；同数据、同策略、同依赖在不同 run 目录产生相同 hash。
- `lookahead_sentinel=true` 时对未来数据后缀做变异，检查已知时点信号不变；`lookahead_max_checks<=0` 为逐 cutoff 严格模式。
- 未来函数哨兵默认关闭，原因是逐 cutoff 运行策略的成本可能为 O(n²)；验收与高可信运行建议开启。

## 3. 黄金账本与性质测试

- 单资产 next-open、跳空止损、日内止损、同柱冲突和期末清算均有手算断言。
- 多币种黄金样本精确验证决策 FX、执行 FX、保证金返还和最终基础货币权益。
- short + leverage + partial close 黄金账本精确验证分段退出与最终 PnL。
- 10 组固定随机种子验证：最终仓位为零、最终 equity 等于 cash、`final cash = initial cash + Σ net trade PnL`。
- 冻结数据重复运行 manifest hash 完全一致；改变最后一根行情后 data/run hash 必须变化。
- 因果策略通过未来数据变异；显式读取最后一根数据的泄漏策略被 sentinel 拒绝。

## 4. 已执行验收记录

| 命令/门禁 | 结果 |
|---|---|
| `scripts/node5-acceptance --quick` | exit 0；全部门禁通过 |
| `scripts/node5-acceptance --full` | exit 0；全部门禁通过 |
| Node 5 专项正确性测试 | 27 pass / 0 fail |
| 回测兼容回归 | 384 pass / 1 skip / 0 fail |
| 正确性核心覆盖率 | line 99.67% / branch 96.67% |
| 后端完整回归 | 4,426 pass / 2 skip / 0 fail，269.78s |
| frontend production build | pass，Vite 6.4.3 / 2,764 modules |
| frontend exact gate | 28 files / 227 pass / 0 fail |
| 时序收尾受影响回归 | 87 pass / 0 fail |
| Ruff changed surface | pass |
| collection exact gate | 4,428 collected / 0 collection error |
| `git diff --check` | pass |

## 5. 非阻断警告与边界

- 2 个后端 skip 和既有 warning 来自 FastAPI lifespan、Starlette/httpx 和 NumPy 合成策略数据，不是节点 5 回归。
- Vite chart chunk 大于 500 KiB 的警告仍存在，属于后续前端性能节点。
- bar 级回测无法知道同一根 bar 内 high/low 的真实先后，因此必须使用显式同柱冲突策略；默认 `stop_first` 是保守假设。
- 默认末日缺 bar 使用最后已知价格，适用于跨市场节假日；要求最严格清算证据时应配置 `missing_final_bar_policy=error`。
- mutation sentinel 是策略级强检查但成本较高；CI 使用小型黄金数据逐 cutoff 验证，生产长周期运行可设置采样数量。

## 6. 用户验收建议

建议用户重点核对：

1. 信号日收盘发生变化时，只能影响下一可成交 bar 的开盘订单。
2. 修改成交 bar 的 close/high/low 不会改变该 bar 开盘前的 sizing。
3. 跳空止损按开盘，日内止损按阈值，同柱冲突符合配置。
4. 最终 artifacts 中 `equity.csv` 最后一行已清仓，`trades.csv` 为净费后口径，`cash_ledger.csv` 可追溯资金事件。
5. 多币种运行必须配置 base currency 和 FX；缺失时必须失败。
6. 相同冻结输入重复运行的 `run_manifest.json` 中 `run_hash` 相同。

复验命令：

```bash
cd /Users/flyounger54/Desktop/quant-system/Vibe-Trading
scripts/node5-acceptance --quick
scripts/node5-acceptance --full
```

用户已于 2026-07-17 明确确认“节点 5 验收通过，进入节点 6”。节点 5 可固化 accepted commit/tag，并从该标签建立节点 6 分支。
