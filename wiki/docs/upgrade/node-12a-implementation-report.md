# 节点 12A 实施报告：实盘默认关闭与资格状态机

日期：2026-07-18
分支：`upgrade/node-12-paper-live-qualification`
基线：`upgrade-node-11-accepted`（`e9f54e0`）
状态：实现与全量验证完成；用户已明确验收，随节点 12A 提交归档

## 节点 12 的准确边界

节点 12 是“模拟盘验证与有限实盘资格”，不是单一的下单功能：

1. 所有实盘能力默认关闭；用户只能显式启用一个已认证券商。
2. 统一订单意图、幂等 client order ID、下单前风险、仓位/余额/挂单对账、审计账本与 kill switch 必须形成同一路径。
3. 风险门禁覆盖单笔、总敞口、日内亏损、价格偏离、重复单、行情陈旧、时钟漂移、断线与重启。
4. 每个券商独立认证；无法可靠区分模拟盘与实盘的连接器只能保持只读或模拟。
5. 连续 30 个交易日模拟盘浸泡成功后，才可由人工进入小额实盘试点。
6. 账本不一致、未授权订单、kill switch 失败或陈旧数据任一出现，都必须撤销实盘资格。

本报告只覆盖 12A：默认关闭门禁与资格状态机，不宣称节点 12 整体完成。

## 12A 实现

### 默认关闭

- 新增统一资格判定，缺少任何输入均拒绝，不存在隐式默认开启。
- 操作员必须通过 `VIBE_TRADING_LIVE_BROKER` 精确选择一个券商；空值、多个值或其他券商均拒绝。
- 运行制品必须通过 `VIBE_TRADING_BUILD_REVISION` 声明 40 位不可变提交；缺失或格式错误均拒绝。
- 直连 SDK 下单、远端 MCP 下单和持久化 runner 每次执行都检查同一资格结果；runner 未获资格时在对账和调用 agent 之前停止。
- 停止 runner、kill switch、只读查询、直连 SDK 撤单和 operator flatten 不依赖实盘资格，避免资格撤销妨碍降险。

### 精确资格键与保护存储

- 资格键固定为 `(broker, account_ref, build_revision, policy_version)`，不允许只按券商宽泛放行。
- 注册表固定读取 `<runtime_root>/live/qualification-registry.json`，调用者不能指定路径。
- 注册表必须为当前运行用户所有、非符号链接、大小受限，并禁止 group/other 访问；异常、重复、未知 schema 或任意结构错误都会让整个注册表 fail-closed。
- agent 工具面没有保存、晋级、激活或撤销资格的写接口。

### 状态机与 30 日证据

- 状态固定为 `disabled → paper_soak → pilot_eligible → pilot_active → revoked`；被撤销后只能重新进入 `paper_soak`。
- 禁止从关闭或浸泡状态直接跳到实盘；`pilot_active` 必须由明确的 `operator` 主体写入状态历史。
- `pilot_eligible` / `pilot_active` 必须包含至少 30 个唯一、严格递增、已接受且声明连续的交易日，以及至少一个证据引用。
- 状态历史必须从 `disabled` 开始、时间严格递增、最终状态与声明一致；未来日期、过期资格和无原因撤销均拒绝。

### 可观测性与契约

- `GET /live/status` 为所有 live profile（包括直连 SDK）返回资格状态、原因码、构建提交、浸泡天数和策略版本。
- CLI `connector status` 显示实盘是否关闭、资格状态、30 日进度和构建短提交。
- API runner start 在 mandate、过期与 kill switch 后增加资格门禁；拒绝返回稳定原因码。
- OpenAPI 与生成的 TypeScript 类型已同步新增资格 schema。

## 分步计划与准入条件

### 12A：默认关闭与资格状态机（本阶段）

准入：节点 11 已由用户确认验收，`upgrade-node-11-accepted`、`main` 与默认分支均指向固定候选。

退出：默认拒绝、精确键、状态机、runner/订单双重门禁、状态可观测性和全量回归全部通过；用户明确验收后才提交。

### 12B：统一订单通路与完整风险门禁

依赖 12A 验收。统一 paper/live `OrderIntent`，加入幂等 client order ID、挂单预留、日内亏损、价格偏离、行情时效、时钟漂移、FX 归一化和 fail-closed 对账；审计写失败不得继续实盘写入。

### 12C：模拟盘浸泡与券商独立认证

依赖 12B 验收。生成不可伪造的每日证据，验证 30 个连续交易日、重启/断线/重复单/kill 演练，并为每个 broker/account/build/policy 独立晋级到 `pilot_eligible`。

### 12D：人工小额试点与自动撤销

依赖 12C 验收和用户再次明确启用一个券商。限制资金与单量，执行实时对账和撤销规则；任何账本不一致、未授权订单、kill 失败或陈旧行情立即进入 `revoked`，停止 runner 且保留撤单/flatten。

## 已知风险与未完成项

1. Robinhood 远端 MCP `cancel_order` 仍沿用旧的通用写工具包装，agent 侧撤单路径尚未按“始终允许降险”重构；operator flatten 路径不受影响。此项进入 12B。
2. 现有 live audit 写入仍有 best-effort 路径；12B 必须让实盘放行审计成为事务前置条件。
3. 对账文件损坏仍可能被旧逻辑视为安全冷启动；12B 必须改为明确不安全并要求人工恢复。
4. 日内亏损、价格偏离、陈旧行情、时钟漂移、重复 client order ID、挂单资金预留和 FX 尚未补齐；12A 的资格不能替代这些风险门禁。
5. 30 日证据采集、broker 认证器、operator 资格写入器和自动撤销器尚未实现；当前没有任何资格注册表时，所有实盘保持关闭。
6. `VIBE_TRADING_BUILD_REVISION` 在 12A 由不可变部署环境声明；后续发布阶段需把它与镜像摘要和签名 provenance 绑定，防止本地可变源码冒充已认证构建。
7. 当前只有 Robinhood 提供 managed live runner；其他 live SDK profile 虽已纳入资格状态与下单门禁，但不能因此视为已完成 broker 认证。

## 验收证据

| 项目 | 结果 |
| --- | --- |
| 后端最终全量回归 | `4858 passed, 2 skipped, 0 failed`，`252.43 s` |
| 资格状态机 + runner 专项 | `40 passed` |
| API 安全边界专项 | `52 passed` |
| 前端全量回归 | `32` 个测试文件、`239 passed` |
| 前端生产构建 | TypeScript 与 Vite 构建成功；保留既有 `vendor-charts` 大包提示 |
| API 契约 | `146 paths / 62 schemas`，生成制品 `--check` 通过 |
| Python 静态检查 | 变更面 Ruff、四个核心 live 模块定向 mypy、compileall 均通过 |
| 变更检查 | `git diff --check` 通过 |

既有警告仅包括 FastAPI/Starlette 生命周期弃用、jsdom canvas 和数值策略常量输入提示；没有新增失败。本文件本身不是提交或打标签授权。

## 提交边界

用户已审阅结果并明确继续执行，本次提交归档节点 12A。该验收不代表节点 12 整体完成，因此不创建节点 12 整体验收标签；12B 将在此提交之后独立实施，并继续遵守“全量验证、报告、用户验收后才提交”的边界。
