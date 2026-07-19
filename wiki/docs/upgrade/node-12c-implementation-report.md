# 节点 12C 实施报告：签名模拟盘浸泡与券商独立认证

实施日期：2026-07-19
分支：`upgrade/node-12-paper-live-qualification`
基线：节点 12B 已验收提交 `6d60fe0afdd5d1f537aefd797f0e8a0a8fa7653d`
状态：代码实现与本地全量验证完成；真实 30 个连续交易日尚未开始，节点 12C **未验收**

## 本阶段准确边界

节点 12C 负责为一个精确的 `(broker, account, build, policy)` 资格键完成独立模拟盘认证：

1. 用权威交易日历约束不少于 30 个有序、互不重叠的交易时段。
2. 每个交易日从开盘前建立权益基线，覆盖完整时段，并从当前进程重新读取连接、账户、挂单和行情。
3. 用共享 paper 订单通路验证 client order ID 回显和重复提交幂等，不建立绕过节点 12B 风险门禁的第二套下单路径。
4. 执行重启、断线、重复订单、账本篡改、未授权订单、kill switch 和 halt 后撤单演练。
5. 只有最后 30 个连续交易日全部接受、且七项演练都在当前连续窗口内完成，才允许人工晋级到 `pilot_eligible`。

本阶段不启用 `pilot_active`，不发送实盘订单，不执行小额实盘试点，也不进入节点 12D。任何拒绝日都会留在签名链中并把连续计数清零。

## 实现结果

### 签名日历与每日证据链

- 新增 Node 12C evidence schema/policy，campaign 精确绑定 broker、账户引用 SHA-256、40 位构建提交、资格策略、paper profile 与签名交易日历。
- campaign 使用 Ed25519：manifest 和每日日证形成前序签名链；可导出证据只包含公钥，私钥单独保存在私有 keystore。
- 每日日证只保存原始券商 payload 的 SHA-256，不把账户、挂单、行情或订单响应明文写入证据账本。
- 只允许签署日历中的下一个交易日；收盘前拒绝，收盘 12 小时后拒绝回填；坏日仍签名并重置连续窗口。
- evidence、keystore、注册表和锁文件要求当前用户所有、私有权限、非符号链接；账本与锁使用 `O_NOFOLLOW`，目录重定向和锁文件重定向均 fail-closed。

### 全时段 runner 与券商采集

- `connector qualify run` 最早只能在开盘前一小时启动，以 1–300 秒心跳覆盖到收盘，并在开盘前记录 USD 权益基线。
- `connector qualify collect` 只接受 runner 自己的心跳、基线和演练摘要；账户、挂单、行情、连接与订单结果始终由当前进程通过 connector service 获取。
- 生产 CLI 不提供接受完整 observation JSON 的 `record` 命令，外部 JSON 不能替代券商快照。
- 收盘探针使用固定 client order ID 两次进入节点 12B 的共享 paper gate；必须得到同一 broker order ID、client ID 回显和 `idempotency_replayed=true`。
- 连接历史必须逐条绑定心跳；断线后未恢复会拒绝当日，恢复证据会自动计入 `disconnect_recovery`。

### 资格注册表与人工晋级

- 资格注册表升级到 schema v2；非空交易日进度必须带唯一的签名 evidence ref，解析时从公钥重新验证完整 manifest/日证链和精确资格键。
- status 的天数来自最后一个拒绝日之后的连续接受窗口，不是历史累计。
- 七项必需演练固定为 `restart_recovery`、`disconnect_recovery`、`duplicate_order_replay`、`ledger_tamper_fail_closed`、`unauthorized_order_halt`、`kill_switch_block`、`cancel_while_halted`。
- `connector qualify promote --confirm` 只允许 `paper_soak → pilot_eligible`；代码不存在从此入口激活实盘的转换。

## 验证结果

| 项目 | 最终结果 |
| --- | --- |
| CI 等价后端回归与覆盖率 | `4978 passed, 1 skipped, 0 failed`，`440.73 s` |
| 后端覆盖率门禁 | 全局 line `82.32%` / branch `70.07%`；关键交易面 line `95.10%` / branch `92.80%`，全部达标 |
| 12C/共享订单/CLI 专项 | `210 passed`；覆盖签名、时间窗、日历、身份、幂等、对账、账本、审计、符号链接和 CLI 绕行 |
| 前端回归与覆盖率 | `33` 个测试文件、`242 passed`；既有覆盖率门禁通过 |
| 前端生产构建 | TypeScript 与 Vite 构建成功；仅保留既有大 chunk 提示 |
| API 契约 | `146 paths / 62 schemas`，生成制品 `--check` 通过 |
| Python 质量 | 变更面 Ruff、核心模块 mypy、compileall、`uv lock --check`、`git diff --check` 全部通过 |
| 安全门禁 | Bandit high-severity 无发现；Python 与 npm 锁定依赖均为 0 已知漏洞 |
| 隔离 worker | 网络、root、跨 run、shadow 与 host secret 隔离全部通过 |
| Python 制品 | wheel/sdist 构建成功，`twine check` 全部通过 |
| 生产容器 | 本地镜像构建成功；非 root、冷启动、readiness、metrics、SIGTERM 冒烟 18 秒通过 |

既有告警仍是 FastAPI/Starlette 生命周期弃用、数值策略常量输入、jsdom canvas 与前端 chunk 大小，没有新增测试失败或高严重度安全发现。

### 清偿的继承 CI 债务

节点 12B 的远端基线运行 `29651958354` 中，测试和其他 job 均成功，但覆盖率 job 曾以全局 branch `69.98%`、关键 line/branch `85.90%/78.80%` 失败；节点 12B 报告未记录该失败。本阶段没有改变节点 12B 生产语义，而是为 12C 实际依赖的共享 paper/live 订单通路补齐异常分支测试，最终把四项覆盖率门禁全部恢复到合格状态。

## 尚未满足的运行验收条件

当前所有 30 日结果均为临时目录、模拟交易日和 mock connector 的确定性测试。没有创建真实 campaign，没有使用真实 paper 账户，也没有产生可用于资格晋级的运行证据。因此：

1. 还没有 30 个唯一、连续、已接受的真实交易日。
2. 还没有针对任何真实 broker/account/build/policy 完成字段认证。
3. 七项故障演练尚未在真实 paper 环境完成；签名链证明证据未被事后修改，但不是第三方真实性背书。除断线恢复和重复 ID 探针由 runner 直接验证外，其余演练的原始制品必须由操作员保留并在最终验收时人工核对 SHA-256。
4. 还没有真实 `pilot_eligible` 注册表记录；`pilot_active` 继续保持不可达。

## 下一步与提交边界

真实浸泡必须绑定不可变构建，但现有流程又禁止在用户审阅前提交。因此下一步应由用户明确批准一个“Node 12C 非验收 soak candidate checkpoint”：

1. 批准后才提交并推送本报告及实现，固定新的 40 位构建提交；不打任何 Node 12 验收标签。
2. 为选定的 paper broker/account 准备权威市场日历、真实凭据和对应构建制品，再启动 campaign。
3. 每个交易日执行完整 session 与计划演练；任何拒绝日从下一交易日重新累计 30 日。
4. 30 日完成后重新核验证据链、原始演练制品和资格注册表，再向用户提交运行验收报告。
5. 只有用户再次明确验收 Node 12C 后，才可归档运行证据并讨论节点 12D；当前不得进入 12D。

本文件不是提交、推送、标签、真实 paper 下单或资格晋级授权。
