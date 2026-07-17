# Vibe-Trading 升级节点 1 验收报告

生成时间：2026-07-17 10:46（Asia/Shanghai）  
分支：`upgrade/node-1-contract-recovery`  
起点：`de5337a` / `upgrade-node-0-accepted`  
状态：**已由用户验收通过（2026-07-17 10:52，Asia/Shanghai）**

## 1. 节点目标与结论

节点 1 的目标是恢复项目的可收集、可测试契约，消除节点 0 冻结的 18 个后端收集错误和 1 个前端契约失败，并把“不能回退”固化为本地及 CI 门禁。

结果：目标已达到。后端在 Python 3.11、3.12 上均为零失败，前端 227 项测试全部通过，生产构建成功，后端及前端均建立了精确数量门禁。

| 验收项 | 节点 0 | 节点 1 | 结果 |
|---|---:|---:|---|
| 后端收集错误 | 18 | 0 | 通过 |
| 后端收集总数 | 4,211 | 4,302 | 通过，固定数量门禁 |
| 收集恢复后的后端行为失败 | 50 | 0 | 通过 |
| Python 3.11 全量 | 未形成零失败基线 | 4,300 passed / 2 skipped | 通过 |
| Python 3.12 全量 | 未形成零失败基线 | 4,300 passed / 2 skipped | 通过 |
| 前端失败 | 1 | 0 | 通过 |
| 前端测试 | 226/227 | 227/227 | 通过，固定数量门禁 |
| 前端生产构建 | 通过 | 通过 | 通过 |

## 2. 分步修复与验收

### 2.1 收敛市场数据 Loader 契约

- 将公开数据源契约收敛为 `astock`、`global`、`tushare`、`okx`、`ccxt`、`local`；mootdx、腾讯、Yahoo、Sina 作为 provider 内部实现和降级链，不再暴露成互相冲突的顶层 loader。
- 恢复共享 Yahoo provider client，为行情、报价、期权、搜索提供统一限流、cookie/crumb、401 单次刷新和符号映射。
- `GlobalStockLoader` 改用共享 Yahoo client，统一 UTC 时间戳与结束日期语义，并保留 Sina 降级。
- benchmark 不再导入已删除的 `yfinance_loader`，改由注册表和合并后的 A 股/全球股票 loader 解析。
- 更新路由技能说明、registry、preflight、metrics 和 runner 中的旧数据源名称。

验收：数据层及 Yahoo 工具重点回归通过；原 18 个收集错误全部消失。

### 2.2 清理半迁移遗留测试并恢复有效覆盖

节点 0 的 18 个收集错误中，13 个来自已经被删除的 provider 专属 loader 测试，另外 5 个当前 Yahoo 工具测试因共享 client 缺失而无法导入。

- 删除 13 个只验证已删除实现的旧测试模块。
- 新增 `test_astock_loader.py`、`test_global_loader.py`、`test_benchmark_loader_contract.py`，按合并后的公共契约覆盖主路径、降级路径、符号映射和 benchmark 路由。
- 恢复原有 Yahoo 行情、新闻、公司资料、期权和搜索工具测试的正常收集。

这不是通过删测试降低门槛：节点 0 只能收集 4,211 项，节点 1 最终可收集 4,302 项，净增 91 项，并用精确数量门禁防止测试静默缩水。

### 2.3 修复收集恢复后暴露的 50 个行为失败

- 修正策略数量契约（40 → 42）及测试中的固定分母。
- 让未配置的 ML Predictor 满足统一策略构造/信号生成契约，返回中性信号并输出告警，而不是在默认构造阶段崩溃。
- 移除因子模块级窗口常量，恢复 alpha purity 约束。
- 补齐供应链研究团队 `evidence_collector` 的 `get_market_data` 工具。
- 为 loader cache 测试增加 L1 内存缓存隔离，并按“默认启用”的当前契约更新测试。
- 修正 swarm stale threshold 测试与当前 180 秒下限、30 次心跳、120 秒重试宽限策略的不一致。
- 修复 macOS 系统代理污染本地 MCP 连接的问题：仅 loopback SSE/HTTP client 强制 `trust_env=False`，远程 MCP 仍可使用代理。

验收：首次恢复收集后的 `50 failed / 4248 passed / 2 skipped` 收敛为双版本 `0 failed / 4300 passed / 2 skipped`。

### 2.4 修复前端可访问性交互契约

WelcomeScreen 的能力 chips 设计为默认折叠，旧测试却假定初始可见。测试现按真实用户路径验证：初始不可见 → 点击可访问名称为 “15 capabilities” 的按钮 → chips 显示。

验收：27 个测试文件、227 项测试全部通过；TypeScript 与 Vite 生产构建通过。

### 2.5 建立节点 1 防回退门禁

- 新增 `scripts/node1-acceptance`，支持 `--quick` 和 `--full`。
- 后端门禁同时要求：退出码为 0、收集错误为 0、收集总数精确为 4,302。
- 前端门禁同时要求：退出码为 0、失败清单为空、测试文件精确为 27、测试总数精确为 227。
- CI 后端升级为 Python 3.11/3.12 矩阵；前端作为独立 job 执行生产构建和精确测试门禁。
- 节点 0 的失败清单保留为历史审计证据，节点 1 使用独立空清单，不再允许已知失败。

## 3. 最终验收记录

两个隔离 Python 环境均执行：

```text
VIBE_PYTHON=<python> VIBE_NODE=<node20> VIBE_NPM=<npm10> scripts/node1-acceptance --full
```

| 环境 | 收集 | 全量后端 | doctor | 前端构建 | 前端测试 | 总门禁 |
|---|---|---|---|---|---|---|
| Python 3.11.13 / Node 20.20.2 | 4,302 / 0 error | 4,300 pass / 2 skip / 0 fail | 13 pass / 3 warn / 0 fail | pass | 227/227 | exit 0 |
| Python 3.12.11 / Node 20.20.2 | 4,302 / 0 error | 4,300 pass / 2 skip / 0 fail | 13 pass / 3 warn / 0 fail | pass | 227/227 | exit 0 |

附加静态门禁：`compileall`、`pip check`、workflow YAML 解析、shell 语法、`git diff --check`、`tools/ci_grep_gates.sh` 全部通过。

## 4. 已知边界与后续风险

以下不阻断节点 1，但不能被解释为已经完成生产验证：

- doctor 的 3 个告警仍在：核心环境未安装 8 个可选依赖、未配置 `TUSHARE_TOKEN`、未配置本地 Data Bridge。
- 凭证化真实行情源、真实 LLM E2E 被验收命令显式排除；节点 1 验证的是离线、模拟和本地集成契约。
- 当前环境没有 Docker CLI，只验证了 Dockerfile 的锁文件安装规则，未构建或运行镜像。
- FastAPI lifespan、Starlette/httpx 和 NumPy 相关弃用/运行时告警仍存在，应在后续依赖与运行时治理节点消除。
- 前端最大 chunk 为 698.42 KiB，超过 Vite 500 KiB 提示线；功能不受影响，但需要后续性能拆包。
- 节点 0 记录的 npm audit 风险尚未在本节点升级依赖，应进入后续安全节点。

## 5. 验收决定

节点 1 已达到“契约恢复与零失败基线”的技术退出条件，并已由用户确认通过。接下来固化 accepted commit/tag，然后进入节点 2。
