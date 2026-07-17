# Vibe-Trading 升级节点 4 技术验收报告

更新时间：2026-07-17 22:24（Asia/Shanghai）  
分支：`upgrade/node-4-multi-market-data-platform`  
起点：`7736b85` / `upgrade-node-3-accepted`  
状态：**实现与技术验收完成；用户已确认节点 4 验收通过**

## 1. 目标与结论

节点 4 的目标是把分散、隐式且语义不稳定的行情加载器升级为可验证的多市场数据平台：统一 Provider 契约、逐 symbol 回退、UTC 事件时间、明确复权语义、数据质量检测、有界多级缓存、健康/熔断状态，以及专项数据工具的来源与失败可追溯性。

实现已完成。离线确定性测试、完整后端回归、前端构建/测试、缓存性能门和一次真实 Provider canary 均通过。真实 canary 验证 A 股腾讯 qfq、Yahoo 美股和 OKX 加密行情均能返回数据。

| 验收面 | 结果 |
|---|---|
| Provider 合约 | runtime-checkable `ProviderProtocol` + capability/version 声明 |
| 回退 | 按 symbol 回退；单 symbol 失败不丢失同批成功结果 |
| 市场黄金样本 | A 股 / 美股 / 港股 / 加密 4 类全部通过 |
| 时间与元数据 | UTC event time；exchange TZ、currency、session、provider、version、adjustment、as-of |
| 复权语义 | `none/qfq/hfq` 显式；不兼容 Provider 跳过并返回结构化原因 |
| 数据质量 | 重复、乱序、非法 OHLC、负 volume、gap、stale 均检测 |
| Provider 韧性 | 分页/重试预算沿用并接入；连续异常或全空批次触发熔断；健康分可查询 |
| L1 缓存 | 有界 LRU；实测 P95 `0.0878 ms`（门限 10 ms） |
| L2 缓存 | DuckDB/Parquet；实测 P95 `25.2776 ms`（门限 150 ms） |
| 缓存治理 | schema/provider/adjustment/version 分区；TTL/容量清理、统计、损坏删除恢复 |
| 专项工具 | 19 类工具统一 `source`、UTC `as_of`、`failure_reason` |
| 后端全量 | 4,399 pass / 2 skip / 0 fail |
| 前端全量 | 28 files / 227 pass / 0 fail；生产构建通过 |
| 真实 canary | A 股 11 rows、美股 10 rows、加密 15 rows，全部成功 |

## 2. 分步实现与验收

### 2.1 Provider 平台与逐标的回退

- 新增 `backtest.loaders.platform`，定义 `BarRequest`、`ProviderProtocol`、`ProviderCapabilities`、`ProviderRegistry`、`FetchReport` 和结构化 attempt/failure。
- `FallbackLoader` 保持既有 `DataLoader.fetch()` 调用兼容，但内部改为逐 symbol 编排；主 Provider 的成功 symbol 立即保留，只把 unresolved symbol 交给下一 Provider。
- Provider 在 interval、adjustment 或 market 上不兼容时不会被调用，也不会把请求静默降级成另一种语义。
- `resolve_loader()`、回测 auto 路由及默认 `get_market_data` 路径共享同一回退编排器；显式 `local` 仍保持不回退网络源的安全边界。
- Provider 构造失败、availability 异常、网络异常、空数据、无效数据、熔断状态均留下可机读 attempt 和最终 failure reason。

### 2.2 统一 bar schema、UTC 与质量门

- 四市场日线标签转换成交易所收盘事件时间后统一为 UTC：A 股 `15:00 Asia/Shanghai`、美股 `16:00 America/New_York`、港股 `16:00 Asia/Hong_Kong`、加密 UTC。
- DataFrame attrs 保存 `bars.v1`、provider/version、symbol、market、interval、adjustment、exchange/event timezone、currency、session、请求区间、as-of、fetched-at。
- 稳定排序后重复时间保留 Provider 最后一条；非法时间直接拒绝；缺失 OHLC、非正价格、high/low 包络错误、负 volume 被剔除；全无效则回退下一 Provider。
- 质量信息包含 duplicate/invalid/out-of-order/gap/stale/bar_count；数据层不再进行两位小数截断。
- 黄金 fixtures 覆盖 A 股、美股、港股和加密，所有测试完全离线。

### 2.3 复权、Provider 依赖与范围能力

- 复权模式统一为 `none/qfq/hfq`。Provider 必须显式声明能力；fallback 不允许改变请求值。
- A 股 raw 使用 TDX TCP，qfq 使用腾讯；不再在 raw 请求失败时静默切换到腾讯前复权数据。
- mootdx 0.11.7 声明 `httpx<0.26`，与项目 `httpx>=0.28` 无法可靠共存。`ashare` extra 改用其底层 TDX 传输 `tdxpy>=0.2.5,<0.3.0`，安装 dry-run 解析成功；availability diagnostics 明确报告 tdxpy 是否安装及 mootdx 不兼容原因。
- Yahoo 行情去除 acquisition 层两位小数 rounding；OKX/CCXT 不再把未知 interval 静默改成 1D。
- OKX/CCXT 既有分页、请求 timeout、重试和 wall-clock budget 保留；TDX/腾讯、Yahoo/新浪保持各自内部来源边界，平台层统一做后续 Provider 回退。

### 2.4 有界三级缓存

- L1 从无界 dict 改为有界 `OrderedDict` LRU，默认最多 256 项，可用 `VIBE_TRADING_DATA_MEMORY_MAX_ENTRIES` 调整；命中、miss、eviction 可观测。
- L2 继续使用 DuckDB + Parquet，历史缓存默认开启；key 加入 bar schema、Provider version 和 adjustment，防止不同语义碰撞。
- 元数据 sidecar 保存 stored-at、index/column 信息和 JSON-safe DataFrame attrs；读取时恢复 provenance/quality。
- 元数据版本错误、sidecar 损坏、Parquet 损坏或 index 缺失会删除损坏数据并回源，不把缓存故障传播给业务。
- `loader_cache_prune()` 同时执行 TTL 与 entry/byte 容量治理，Parquet 和 sidecar 都计入容量；提供 clear/stats/prune 运维接口。
- 25 次 L2 与 500 次 L1 本机验收：L1 P95 0.0878 ms，L2 P95 25.2776 ms。

### 2.5 专项数据工具可追溯性

- 基础工具层对 19 类数据能力统一补齐 `source` 和 UTC `as_of`。
- 失败响应保留既有 `error`，并增加稳定的 `failure_reason`，便于 Agent、MCP 客户端和监控解析。
- OHLCV `get_market_data` 额外返回 `_meta`：response schema、as-of、每个 symbol 的 Provider/质量元数据和 unresolved symbol 的结构化失败。
- `get_market_data` 新增 adjustment 参数，工具、MCP 和 loader 使用同一语义。

### 2.6 离线 CI 与真实 canary

- `scripts/node4-acceptance --quick|--full` 固化 Provider/fixture/quality/cache/performance/兼容/全量验收。
- CI 精确 collection gate 更新为 4,401（4,399 pass + 2 skip），新增测试均不访问真实网络。
- `.github/workflows/market-data-canary.yml` 只在工作日夜间或手动触发，真实探测腾讯、Yahoo 和 OKX；`--alert-only` 以 GitHub warning 告警，不影响确定性 CI。

## 3. 已执行验收记录

| 命令/门禁 | 结果 |
|---|---|
| `scripts/node4-acceptance --quick` | pass（20 核心 + 116 兼容；collection 4,401） |
| `scripts/node4-acceptance --full` | exit 0；全部门禁通过 |
| `.venv/bin/python -m pytest -q` | 4,399 pass / 2 skip / 0 fail，270.49s |
| 专项数据工具回归 | 172 pass / 0 fail |
| frontend production build | pass，Vite 6.4.3 / 2,764 modules |
| frontend exact gate | 28 files / 227 pass |
| `scripts/generate-api-contracts --check` | pass，132 paths / 59 schemas |
| Ruff changed surface | pass |
| optional `ashare` install dry-run | pass，tdxpy 0.2.7 可解析 |
| collection exact gate | 4,401 collected / 0 collection error |
| `git diff --check` | pass |
| real Provider canary | pass：A 股 / US / crypto 三组全部成功 |

## 4. 非阻断警告与边界

- 2 个 skip 和既有 warning 来自 FastAPI lifespan、Starlette/httpx 与 NumPy 合成信号数据，不是节点 4 回归。
- Vite chart chunk 仍大于 500 KiB；属于后续前端性能节点，不是数据平台退出条件。
- A 股 raw TDX 需要安装 `.[ashare]` 且运行环境能访问 TCP 7709；海外网络不可达时会按相同 raw 语义尝试 Tushare/local，不会改用 qfq。
- `hfq` 目前没有已注册 Provider 宣告支持，系统会返回 `no_compatible_provider`，而不是伪造或改用其他复权模式。
- 夜间 canary 是告警面，不进入 PR/push 的确定性 CI；真实数据源短时波动不会阻断提交。

## 5. 用户验收建议

节点 4 已满足技术退出条件，建议用户核对以下行为后确认：

1. 混合 symbol 批次中，一个 Provider 缺失单个 symbol 时，其余成功数据仍保留。
2. `none` 请求不会返回 qfq 数据；不支持的 `hfq` 有明确失败原因。
3. 输出事件时间为 UTC，且 `_meta` 能看到交易所时区、币种、Provider 和质量指标。
4. 缓存关闭/损坏时仍能回源，缓存容量受限并可清理。

复验命令：

```bash
cd /Users/flyounger54/Desktop/quant-system/Vibe-Trading
scripts/node4-acceptance --quick
scripts/node4-acceptance --full
```

用户已于 2026-07-17 明确确认“节点 4 验收通过，进入节点 5”。节点 4 可固化 accepted commit/tag，并从该标签建立节点 5 分支。
