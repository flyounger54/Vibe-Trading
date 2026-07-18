# 节点 12B 实施报告：统一订单通路与完整执行风险门禁

实施日期：2026-07-18
验收日期：2026-07-19
分支：`upgrade/node-12-paper-live-qualification`
基线：节点 12A 已验收提交 `fa994f9`
状态：实现与全量验证完成；用户已明确验收，随节点 12B 提交归档

## 本阶段边界

节点 12B 只覆盖统一 paper/live 订单通路、幂等执行与完整下单前风险门禁：

1. paper 与 live 使用同一个 broker-agnostic `OrderIntent`，包含调用方稳定的 `client_order_id`、订单类型与限价。
2. 下单前统一检查日内亏损、价格偏离、行情时效、时钟漂移、FX、挂单预留、账户权益与既有 mandate 硬上限。
3. 实盘写入前必须先持久化审计；审计不可用时不得调用券商。券商写入后的账本或审计失败会立即触发 halt。
4. 对账状态损坏、权限不安全、未授权券商挂单或无法解释的仓位变化均 fail-closed。
5. 撤单是降险动作，在 mandate 失效、资格关闭或 halt 后仍保持可用；撤单审计采用 best-effort，审计故障不能阻止撤单。

本报告不覆盖 30 个交易日模拟盘浸泡、每券商认证、小额实盘试点或自动撤销资格；这些仍属于 12C/12D。节点 12B 不激活任何实盘资格，也不代表节点 12 整体完成。

## 实现结果

### 统一订单与持久幂等

- `trading_place_order` 现在强制要求 8–64 位安全 `client_order_id`；paper 与 live 都从同一个 `OrderIntent` 进入共享风险逻辑。
- 新增每券商 append-only JSONL 订单账本。每条记录带序号、前序哈希和本条哈希，文件要求当前用户所有、私有权限、非符号链接并受大小限制。
- 券商写入前原子 claim 幂等键；相同键与相同指纹只重放已持久化结果，pending、冲突或 ambiguous 结果绝不自动重发。
- Alpaca、Binance、OKX、Futu、Longbridge、Tiger 以及 Robinhood 远端 MCP 路径均映射各自券商的 client-order-id 字段；Dhan/Shoonya 模拟路径也返回同一 ID。

### mandate schema v2 与可见授权边界

- mandate schema 升级为 v2，强制包含：`max_daily_loss_usd`、`max_price_deviation_bps`、`max_quote_age_seconds`、`max_clock_drift_seconds`。
- 稳健/均衡/激进档分别生成受账户 ceiling 约束的执行风险参数；动态券商账户额度只覆盖可刷新额度，不会抹掉用户在提案中看到的执行控制。
- 前端授权卡在用户提交前显示四项执行控制；`GET /live/status`、Runtime 页面与 runner 状态同时显示生效值。
- `mandate.committed` 回执补齐 `proposal_id`、`selected_ordinal` 和已解析风险值，使前端能把成功提交精确绑定回用户选择的卡片。
- 旧 schema mandate 不会被自动迁移或隐式放宽；必须重新走显式提案/同意流程生成 v2。

### 执行风险门禁

- 行情必须带源时间；超过授权 quote age、未来时间超过 clock drift、无可执行 bid/ask 或无法识别币种都会拒绝。
- 非美元订单与挂单预留必须提供正数 FX 和 FX 源时间；陈旧/未来 FX 拒绝。USD、USDT、USDC 按受支持的美元等价计价处理。
- 限价与 market 可执行价相对中价的偏离不得超过授权 bps；market 无双边可执行价格时拒绝。
- UTC 日开盘权益持久化到私有、原子更新的 risk state；达到或超过每日亏损上限即拒绝，损坏、错误所有者或不安全权限均拒绝。
- 现有挂单剩余名义金额加入总敞口和杠杆计算；任何无法可靠换算成 USD 的挂单都会让新单 fail-closed。

### 对账、审计与降险例外

- `runtime_state.json` 只有真正缺失时才视为冷启动；符号链接、目录、错误所有者/权限、超限、损坏或未知结构均要求人工恢复，不再静默重建。
- 冷启动可以纳入已有仓位并继续执行敞口检查，但券商挂单只有在 tamper-evident 订单账本证明同一 channel 已接受其 client ID 时才可纳入基线；其他挂单分类为 `unauthorized_order` 并 halt。
- 每次 live 订单在券商调用前写 `order_submitted`；前置审计失败时券商调用次数保持为零。后置账本/审计失败会返回 `safety_halt` 并落下 halt sentinel。
- Robinhood 远端撤单改为独立 `LiveCancelGuardTool`，不再经过风险增加型下单门禁；直连 SDK 撤单也在调用前后 best-effort 审计，审计失败仍允许继续撤单。

## 验收证据

| 项目 | 最终结果 |
| --- | --- |
| 后端最终全量回归 | `4882 passed, 2 skipped, 0 failed`，`302.35 s` |
| 12B 核心专项 | `105 passed`（账本、风险、对账、consent、远端/SDK 下单） |
| runtime/撤单/连接器专项 | `139 passed` |
| 前端全量回归 | `33` 个测试文件、`242 passed` |
| 前端生产构建 | TypeScript 与 Vite 构建成功；仅保留既有 `vendor-charts` 大包提示 |
| API 契约 | `146 paths / 62 schemas`，生成制品 `--check` 通过 |
| Python 静态质量 | 全变更面 Ruff、6 个关键模块 mypy、compileall 全部通过 |
| 安全与可复现 | Bandit high-severity 扫描无发现；项目内 `uv lock --check` 通过 |
| 变更完整性 | `git diff --check` 通过 |

既有警告仅包括 FastAPI/Starlette 生命周期弃用、jsdom canvas、数值策略常量输入与前端 chunk 大小提示；没有新增测试失败或高严重度安全发现。

## 12C 准入条件与剩余风险

只有用户明确验收 12B 后，才可提交本阶段并进入 12C。12C 必须继续满足：

1. 为每个 broker/account/build/policy 独立验证 quote/account/open-order 字段与 client-order-id 回显；缺少时间、FX、账户权益或幂等回显的连接器继续 fail-closed。
2. 从交易日开始持续运行 paper runner，验证日内开盘权益基线、UTC/交易所日界、断线、重启、重复 ID、账本损坏、未授权挂单和 kill/cancel 演练。
3. 生成不可伪造的每日证据并累计 30 个唯一、连续、已接受交易日；当前实现只提供证据原料，不进行资格晋级。
4. 非美元多资产账户目前只有在券商提供可验证的 USD 权益或带时间 FX 时才允许新单；这会让未完成适配的券商保持拒绝，是预期安全行为。
5. 日内亏损基线从当天第一次受控账户观测开始持久化；12C 必须用全天连续运行和券商认证验证交易日开始前已建立基线，不能把中途首次启动视为已认证行为。
6. 当前 managed live runner 仍只有 Robinhood；其他 SDK live profile 完成 12B 门禁不等于完成 12C 券商认证。

## 提交边界

用户已于 2026-07-19 明确验收节点 12B。本次提交归档全部节点 12B 变更；不创建 `upgrade-node-12-accepted`，该标签必须等待 12C/12D 与节点 12 整体准入全部完成。12C 仍须在本提交之后按“实现—全量验证—报告—用户验收—提交”的独立流程执行。
