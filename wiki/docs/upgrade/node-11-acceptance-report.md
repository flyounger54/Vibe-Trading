# 节点 11 验收报告：CI/CD、可观测性、性能与发布工程

日期：2026-07-18
分支：`upgrade/node-11-release-engineering`
基线：`upgrade-node-10-accepted`（`d6ee01d`）
状态：实施与本地验证完成，但有发布阻断项；未提交、未打验收标签

## 审计结论

1. 主 CI 硬编码旧测试数（后端 4401、前端 28/227），而节点 10 基线已是后端 4480、前端 32/239，因此现有主 CI 会错误失败。
2. 项目没有 `uv.lock`；旧 `requirements.lock` 是无哈希手工冻结文件，容器和 CI 仍通过 pip 分别安装，无法验证项目元数据与锁文件一致。
3. CI 只有 backend/frontend/sandbox 三组，契约、安全、容器、发布制品、覆盖率与性能没有独立门禁。
4. 旧生产镜像在最终运行层安装 `build-essential`，单次基线构建额外安装 92 个系统包、约 372 MB；虽已有非 root 用户，但健康检查只访问 liveness。
5. `/healthz` 的 liveness 语义正确；旧 `/readyz` 仅检查 API key，没有检查 SQLite 完整性、迁移版本和持久工作队列。
6. 安全审计基线良好：Bandit High、pip-audit、npm High/Critical 均为零已知问题；但主 CI 没有执行这些门禁，也没有镜像 SBOM、签名、来源证明与漏洞阻断。
7. 项目已有 request ID、安全 JSONL 审计、提供方健康统计、缓存统计、LLM token 统计、回测耗时和持久队列状态，但没有统一运维指标出口与结构化应用日志。
8. 覆盖率审计基线为全局 line 71.33%、branch 53.13%；关键安全/交易边界合计 line 84.94%、branch 70.99%。补齐测试后已达到节点目标，详见当前验收证据。

## 分步实施与验收

### 11.1 锁定依赖与分层 CI

- 新增覆盖 Python 3.11/3.12 和所有可选能力解析的 `uv.lock`；PyPI 元数据继续保留合理兼容范围。
- `requirements.lock` 改为 uv 自动生成并带制品哈希的兼容导出；CI、生产镜像和 sandbox 镜像均以 `uv sync --locked` 为准。
- CI 拆分为 Python 3.11/3.12、质量/覆盖率、前端、API 契约、安全、sandbox、生产容器、确定性性能和 Python 制品九类任务。
- Ruff 对变更面与关键运行边界强制执行；mypy 对 observability、契约、安全和状态边界强制执行。

### 11.2 容器、SBOM、签名与供应链

- Python 编译工具链隔离到 builder；最终镜像只保留 WeasyPrint 所需运行库。
- 生产与 sandbox 的构建/运行 ABI 统一为固定 Ubuntu 24.04 + Python 3.12，uv 固定为 0.11.28，并使用非 root UID 10001 和锁定依赖；Compose 开发前端改用 `npm ci`。
- release workflow 构建 amd64/arm64 镜像，生成 SBOM、最大 provenance 与 GitHub attestation，使用 OIDC/Cosign keyless 签名，并以 Trivy 阻断所有 Critical/High（包括暂时没有修复版本的发现）。
- release workflow 在签名前扫描不可变摘要，签名后反向验证 Cosign 与 GitHub attestation，并上传机器可读发布证据；独立 RC workflow 按天采集 readiness、性能、队列、恢复演练和漏洞证据。
- 生产容器门禁覆盖非 root、冷启动、readiness、受保护 metrics 和 SIGTERM 优雅退出。
- Trivy action 固定到 0.35.0 的完整不可变提交；该版本在 [2026 年供应链事件的官方通告](https://github.com/aquasecurity/trivy/security/advisories/GHSA-69fq-xp46-6x23)中列为安全版本。CI 不启用 `ignore-unfixed`，任何例外都必须逐 CVE、有时限并由用户明确接受。

### 11.3 结构化日志与指标

- 服务模式默认 JSON 日志，复用 secret redaction；HTTP 日志带 request ID，持久任务日志带 session/job ID。
- `/metrics` 受 Bearer 保护，导出 HTTP 延迟/状态、队列深度、工作器、提供方延迟/失败、缓存命中/未命中、LLM token 和回测耗时。
- 指标标签使用路由模板而非原始用户路径，避免高基数；指标失败与安全审计隔离，不能阻断请求或吞掉审计记录。

### 11.4 健康、备份、迁移与恢复

- `/healthz` 保持纯 liveness；`/readyz` 新增 SQLite quick-check、当前/预期迁移版本、队列与 on-demand worker 配置，失败返回 503。
- `scripts/state-admin` 支持在线一致性备份、只读完整性/哈希验证、带确认的原子恢复和默认 dry-run 的 loader cache 清理。
- 恢复前自动保留 pre-restore 数据库；自动化测试已完成写入—备份—变更—恢复—数据复核演练。
- `wiki/docs/operations/release-engineering.md` 记录 RPO/RTO 边界、告警建议、月度恢复演练、签名验证和七日 RC 证据格式。

### 11.5 性能与覆盖率门禁

- 确定性性能门禁覆盖 20 并发非 LLM API、500 个持久队列任务、1 万 SSE 事件、100×250 数据批量和 10 万点长周期回测准备。
- 最终实测 API P95 120.89 ms（目标 ≤300 ms），队列 500/500 完成且 lost=0。
- 后端硬门禁为全局 line 80% / branch 70%、关键边界 line 95% / branch 90%，当前均已通过；前端保留全局不可回退门禁，并对 API transport、SSE、agent store 等关键边界执行独立硬门禁。

## 当前验收证据

| 项目 | 结果 |
| --- | --- |
| 后端锁定环境全量回归 | `4836 passed, 1 skipped`，`383.49 s` |
| Node 11 发布工程专项 | `14 passed` |
| 前端全量与覆盖率 | `32` 文件、`239 passed`；line `25.69%`、branch `24.27%` |
| 后端覆盖率 | 全局 line `82.30%`、branch `70.03%`；关键边界 line `99.81%`、branch `95.06%`；节点硬门禁通过 |
| 依赖/代码安全 | Bandit High、pip-audit、npm High/Critical 均为 0 |
| 严格镜像扫描 | Ubuntu 24.04 OS 层与 Python 包层均为 `0 Critical / 0 High`；`ignore-unfixed=false` 门禁通过，无豁免 |
| 非 LLM API | 20 并发、200 请求，P50 `29.85 ms`、P95 `120.89 ms` |
| 持久队列 | 500 jobs / 8 workers，`lost=0`，约 `366.31 jobs/s` |
| SSE | 10,000 events，`lost=0`，约 `131,156 events/s` |
| 数据批量 | 100 symbols × 250 bars，`lost=0`，`0.598 s` |
| 长周期回测准备 | 20 symbols × 5,000 bars，100,000 points，`0.265 s` |
| 生产容器 | 非 root UID 10001、cold start/readiness/metrics/SIGTERM 全通过，`20 s`；镜像内容约 `339 MB`，较旧基线约缩小 `38%` |
| Sandbox | host secret、网络、其他 run、root、shadow account 均阻断，非 root 通过 |
| RC 自动化预检 | 本地短时 readiness soak `5/5`、uptime `100%`、SIGTERM exit `0`；仅作预检，不计入 Day 1 |
| API 契约/制品 | `146` paths、`61` schemas 无漂移；wheel、sdist、CycloneDX SBOM 均通过校验 |
| mypy 关键边界 | 5 个模块通过 |
| Ruff 变更/关键面 | 通过 |

## 尚未满足的验收门槛

1. 当前 GitHub 身份对上游 `HKUDS/Vibe-Trading` 只有只读权限，上游没有 Node 11 分支或新 Release workflow，也尚无 `flyounger54/Vibe-Trading` fork。多架构推送、远程 attestation 和 keyless 签名必须先建立可写 fork 与不可变 RC 检查点后才能执行。
2. 七日 RC 自动化已实现，但 Day 1 必须绑定远程候选 commit 与 GHCR digest；当前脏工作区和本地镜像不能冒充正式观察日。

## 提交边界

本报告不是验收提交授权。节点 11 只有在全量验证完成、剩余门槛被满足或由用户明确接受处理方式、且用户确认验收后，才会提交并创建 `upgrade-node-11-accepted` 标签。
