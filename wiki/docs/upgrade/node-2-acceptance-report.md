# Vibe-Trading 升级节点 2 技术验收报告

生成时间：2026-07-17 11:45（Asia/Shanghai）  
分支：`upgrade/node-2-contract-state-boundaries`  
起点：`c278447` / `upgrade-node-1-accepted`  
状态：**已由用户验收通过（2026-07-17 12:01，Asia/Shanghai）**

## 1. 节点目标与结论

节点 2 的目标是建立稳定契约和可靠状态边界：API、CLI、MCP 不再各自解释同一命令；本地状态不再以分散 JSON/JSONL 作为生产真相源；旧状态可备份、校验、回滚；前端契约从 OpenAPI 生成；旧 API 保持兼容。

结果：技术退出条件全部达到。生产状态现统一到 SQLite WAL，旧会话和 swarm 状态已完成真实迁移与回滚复验；`/api/v1` 与 66 条旧路径并存；Python 3.11/3.12、前端测试和生产构建均为零失败。

| 验收项 | 节点 1 | 节点 2 | 结果 |
|---|---:|---:|---|
| 后端精确收集数 | 4,302 | 4,326 | 新增 24 项契约/状态测试 |
| Python 3.11 | 4,300 pass / 2 skip | 4,324 pass / 2 skip | 通过 |
| Python 3.12 | 4,300 pass / 2 skip | 4,324 pass / 2 skip | 通过 |
| 前端测试 | 227/227 | 227/227 | 通过 |
| OpenAPI | 无固定生成物 | 132 paths / 59 schemas | 通过 |
| 稳定 API | 旧路径 | 66 个 `/api/v1` + 66 个兼容路径 | 通过 |
| 生产状态 | JSON/JSONL 与多库并存 | `state/vibe.db` / WAL / schema v1 | 通过 |

## 2. 分步实现与验收

### 2.1 共享契约与三端一致性

- 新建 `src/contracts`，集中管理 session、goal 请求/响应和稳定错误码。
- 建立 `GoalApplicationService`，API、CLI、MCP 统一执行参数清洗、默认 criterion、风险等级、预算和禁止执行类目标校验。
- `/api/v1` 错误采用 `code/message/request_id/retryable/details` 信封；旧路径继续返回原 FastAPI 结构。
- 跨适配器测试证明：同一有效输入产生相同业务语义；同一执行类输入三端均得到 `invalid_argument`。

### 2.2 `/api/v1`、兼容层与路由边界

- 新增领域路由模块：sessions、runs、data、research、live；原有 alpha/strategy、ML、industry-chain 模块继续作为研究、机器学习和产业链边界。
- 历史 run 端点及其文件读取逻辑已迁出主服务；data/research/live 的路径、依赖和响应模型由领域模块集中注册。
- session 的 canonical `/api/v1` handler 位于独立模块；旧 session handler 保留两版本兼容期。
- 所有业务路径安装 `/api/v1`，同时保留旧路径；当前为 66 + 66，契约测试逐项固定。

主服务仍包含部分 live/session 业务编排和兼容 handler（3,359 行）。节点 2 已完成路由所有权和外部契约隔离；继续抽取业务 service 可在后续维护中进行，不影响本节点的契约稳定性。

### 2.3 SQLite WAL 统一状态

- 新建显式、前向迁移的 `StateDatabase`，启用 WAL、foreign keys、30 秒 busy timeout 和事务写入。
- schema v1 覆盖 session、message、attempt、持久 event cursor，以及 job、swarm run、schedule 通用状态记录。
- SessionStore 的生产真相源改为 SQLite；事件 replay 在进程重启后仍可从 durable cursor 恢复。
- ScheduledResearchJobStore 默认进入统一库；显式 `path=` 仍保留旧 JSON 兼容行为。
- SwarmRun 规范快照写入统一库，任务、事件流和大体积 artifact 继续保留文件系统；`run.json` 丢失时可由数据库恢复。
- GoalStore 默认与其他状态共用 `state/vibe.db`，FTS5 搜索库保留为可重建投影，不再被描述为 canonical store。

并发验收覆盖：并发创建只有一个 winner；并发 update/cancel 使用版本号只有一个成功；100 条并发消息无丢失；stale session update 被拒绝。

### 2.4 旧状态迁移、备份、校验与回滚

- 新增 `scripts/migrate-state import|rollback`。
- 导入前严格解析 session JSON、message JSONL、attempt JSON、scheduled-research JSON 和 swarm `run.json`；任何损坏或归属不一致都会中止。
- 导入前复制旧状态，若目标库已存在则使用 SQLite backup API 生成一致性备份。
- 导入后按规范顺序比较数量与 SHA-256；任何不一致自动恢复原数据库。
- 真实迁移第一次因 swarm 目标读取顺序未规范化而被哈希门禁拦截并自动回滚。修复排序后增加双记录回归测试，真实迁移成功。
- 随后对成功清单执行真实 rollback，确认数据库恢复为不存在状态，再次完整导入成功。

最终真实迁移结果：17 sessions、19 messages、13 attempts、0 schedules、17 swarm runs。最终可回滚清单：`~/.vibe-trading/upgrade-backups/state-migration-20260717T033728Z/manifest.json`。

### 2.5 OpenAPI、前端类型与客户端生成

- `scripts/generate-api-contracts` 生成并检查：`openapi.json`、`api-types.ts`、`api-client.ts`。
- `--check` 模式在文件过期时失败，CI 已接入漂移门禁。
- 前端 session、message 和完整 goal contract 已改为 generated type alias，删除对应手写重复类型。
- 前端 API 基路径切换到 `/api/v1`；生成客户端包含所有 v1 operation、method、path 参数和 query 构建逻辑。

验收：59 个 schema、全部 v1 operation 均在生成客户端；TypeScript 和 Vite 生产构建通过。

### 2.6 防测试污染与 CI 门禁

- 发现旧 API 测试在默认路径切换后会触碰真实状态库；测试生成的数据库已备份到 `/tmp/vibe-node2-test-pollution-20260717.db` 并从生产路径移走。
- 全局 pytest fixture 将 `VIBE_TRADING_STATE_DB_PATH` 指向 session 临时目录。
- 完整节点 2 门禁前后比较真实库 SHA-256，结果完全不变。
- 新增 `scripts/node2-acceptance --quick|--full`，固定后端 4,326 项、前端 27 文件/227 项和生成契约漂移。
- CI Python 3.11/3.12 矩阵更新到节点 2 精确收集数，并增加生成契约检查。

## 3. 最终验收记录

| 环境 | 后端全量 | 前端构建 | 前端测试 | 结果 |
|---|---|---|---|---|
| Python 3.11.13 | 4,324 pass / 2 skip / 0 fail | 已由同一工作树验证 | 227/227 | 通过 |
| Python 3.12.11 / Node 20.20.2 | 4,324 pass / 2 skip / 0 fail | 2,753 modules / pass | 227/227 | 通过 |

节点 2 快速专项门：32 pass；doctor：13 pass / 3 warn / 0 fail；`compileall`、生成契约 `--check`、`git diff --check`、shell syntax 均通过。

## 4. 已知边界

- 两个 skip 和运行时警告沿用节点 1 基线：FastAPI lifespan、Starlette/httpx、NumPy 测试数据及 Python 3.11 OpenTelemetry 弃用提示。
- doctor 仍有 3 个非阻断告警：8 个可选依赖未安装、Tushare token 未配置、本地 Data Bridge 未配置。
- 凭证化真实行情源、真实 LLM E2E 和 Docker runtime 不属于节点 2 离线契约/状态验收。
- 前端 698.42 KiB 大 chunk 提示仍存在，应在后续性能节点处理。
- 旧 JSON/JSONL 被保留作为迁移备份和回滚依据；生产写入不再依赖它们。待用户验收并经过观察期后，才能制定归档/清理策略。

## 5. 验收决定

节点 2 已达到技术退出条件，并已由用户确认通过。接下来固化 accepted commit/tag，然后进入节点 3“安全收口与执行隔离”。
