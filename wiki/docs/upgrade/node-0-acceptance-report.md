# 节点 0 验收报告：保护现场并建立可复现基线

- 报告日期：2026-07-17（Asia/Shanghai）
- 仓库：`/Users/flyounger54/Desktop/quant-system/Vibe-Trading`
- 工作分支：`upgrade/node-0-reproducible-baseline`
- 原始 HEAD：`eea2124dff183771ddb2df75c4b61a8e2fd26b19`
- 回滚标签：`upgrade-node-0-rollback-eea2124`
- 验收标签：`upgrade-node-0-accepted`
- 技术验收：**通过**
- 用户验收：**2026-07-17 通过；准许进入节点 1**

节点 0 的定义是“当前加工现场可恢复、运行环境可重建、问题基线可机器复现”。它不要求把现有业务测试全部修绿；后端 18 个迁移期收集错误和前端 1 个旧断言失败已被精确冻结，任何新增、减少或路径漂移都会使节点 0 门禁失败。节点 1 的任务是把这些已知失败归零。

## 1. 现场保护与回滚

仓库外保护目录：

`/Users/flyounger54/.vibe-trading/upgrade-backups/20260716-222122-node0-eea2124`

| 文件 | SHA-256 | 用途 |
| --- | --- | --- |
| `tracked-wip.patch` | `8df68465318d5654863b375a90552ec86056bbb21ac7dd25f2198f204a038132` | 相对原始 HEAD 的二进制补丁 |
| `repository.bundle` | `ad89aeed25daca15ed8038e9b5f3957cfb14b899829abc10d99bfb344e946ddd` | 完整 Git 历史与 refs |
| `worktree-source.tar.gz` | `a5769790c3bc7ccdd79f7c87bf8237c3f3efe3126db92c147935e8eb7010eb1b` | 含未跟踪源码的工作区归档；排除凭据、缓存和构建产物 |

`git bundle verify` 已通过；bundle 包含完整历史。回滚标签和当前 HEAD 均指向 `eea2124`。节点 0 没有执行 reset、checkout 覆盖或清理用户文件。

安全恢复方式是在新目录创建 detached worktree，再恢复快照：

```bash
repo=/Users/flyounger54/Desktop/quant-system/Vibe-Trading
backup=/Users/flyounger54/.vibe-trading/upgrade-backups/20260716-222122-node0-eea2124
rollback=/Users/flyounger54/Desktop/quant-system/Vibe-Trading-node0-rollback

git -C "$repo" worktree add --detach "$rollback" upgrade-node-0-rollback-eea2124
git -C "$rollback" apply "$backup/tracked-wip.patch"
tar -xzf "$backup/worktree-source.tar.gz" -C "$rollback"
git -C "$rollback" rev-parse HEAD
```

## 2. 节点 0 已实现内容

### 2.1 固定并验证运行环境

- `.python-version` 固定项目默认 Python 3.12；项目声明明确限制为 Python 3.11/3.12。
- `.nvmrc` 固定 Node `20.20.2`。
- `requirements.lock` 固定 187 条依赖记录。
- 同一锁文件已分别从空环境重建并验证 Python `3.11.13` 和 `3.12.11`。
- SciPy 使用解释器环境标记：3.11 固定 `1.17.1`，3.12 固定 `1.18.0`。
- 3.11 专属的 `backports.tarfile`、`importlib-metadata`、`zipp` 也已条件锁定。
- Node 20 下发现 `rollup-plugin-visualizer@7.0.1` 要求 Node >=22，已降为兼容 Node >=18、Rollup 4 的 `6.0.5`。
- `npm ci` 在 Node 20 下无 engine mismatch。

### 2.2 防止旧安装覆盖工作区

新增 `vibe-trading doctor [--json]`，只做确定性、无网络诊断：

- Python、Node 和项目版本；
- `cli`、`src`、`backtest` 的实际导入来源；
- 旧 `site-packages` 覆盖当前工作区的硬失败识别；
- 可选依赖及安装提示；
- A 股、全球股票、Tushare、OKX、CCXT、本地 Data Bridge 的配置级可用性；
- runtime/cache 可写性；
- 配置文件权限，且不输出凭据值。

实际 console script 验证结果：13 passed、3 warnings、0 failed。三个警告分别是核心基线未安装可选运行时模块、未配置 Tushare token、未配置本地 Data Bridge；它们不会触发网络请求，也不阻断研究/回测核心基线。

本地脚本、CI 和 Docker 均先安装锁，再以 `--no-deps -e .` 安装当前工作区，避免陈旧 site-packages 被误用。Dockerfile 固定 Node 20 与 Python 3.11；当前主机没有 Docker CLI，因此本节点完成静态门禁，镜像实际构建将在容器/发布节点设为强制验收。

### 2.3 可重复验收门禁

`scripts/node0-baseline` 提供：

- `--preflight`：版本、锁、导入来源、CI/Docker 安装策略和本地工具；
- `--quick`：preflight + compile + doctor + 节点 0 聚焦测试；
- `--full`：quick + 后端收集基线 + 前端构建 + 前端测试收集/失败基线。

两个基线校验器会比较集合，而不是只比较错误数量：

- `scripts/check_node0_collection.py`
- `scripts/check_node0_frontend.py`

已知失败清单：

- `wiki/docs/upgrade/node-0-known-collection-errors.txt`
- `wiki/docs/upgrade/node-0-known-frontend-failures.txt`

### 2.4 基线稳定性修复

完整 Vitest 首次复跑发现 `Runtime.test.tsx` 偶发失败。根因是测试只等待静态标题，随后立即断言异步 API 数据；并且刷新测试没有等待异步状态结束。已改为等待数据态和刷新完成。修复后：

- Runtime 单文件 3/3 通过；
- 完整前端基线连续两次只出现冻结的 WelcomeScreen 旧断言；
- 节点 0 full 门禁最终稳定退出 0。

同时清理了 `RunDetail.tsx` 文件尾多余空行，`git diff --check` 现已通过。仓库和 agent 的两个 `.env` 权限已收紧为 `0o600`，未读取或输出其中内容。

## 3. 最终验收证据

| 验收项 | 结果 | 证据 |
| --- | --- | --- |
| 外部快照与完整 Git bundle | 通过 | SHA-256 和 `git bundle verify` 通过 |
| Python 3.11 锁环境 | 通过 | quick exit 0；doctor 13/3/0；8 tests passed |
| Python 3.12 锁环境 | 通过 | full exit 0；`pip check` 无破损依赖 |
| Node 20 环境 | 通过 | Node 20.20.2、npm 10.8.2、`npm ci` 成功 |
| 工作区导入来源 | 通过 | `cli/src/backtest` 全部位于当前 `agent/` |
| CLI doctor | 通过 | console script 和 JSON 模式均 exit 0 |
| Python compile | 通过 | 3.11、3.12 均通过 |
| 节点 0 聚焦测试 | 通过 | 8 passed |
| 后端 pytest 收集基线 | 通过 | 4,211 collected；18 个已知错误集合精确匹配 |
| 前端生产构建 | 通过 | 2,753 modules；约 10.13s |
| 前端测试收集 | 通过 | 227 tests |
| 前端失败基线 | 通过 | 226 passed；1 个已知旧断言精确匹配 |
| 全局空白检查 | 通过 | `git diff --check` exit 0 |
| 原有文本安全门禁 | 通过 | `ci_grep_gates: all gates passed` |
| CI/Docker 源码安装策略 | 通过 | 锁安装 + 当前工作区 editable 安装的静态门禁 |
| Docker 镜像实建 | 未执行 | 当前主机无 Docker CLI；在发布节点强制执行 |

最终完整命令：

```bash
VIBE_PYTHON=/tmp/vibe-trading-node0-venv-20260716/bin/python   scripts/node0-baseline --full
```

结果：`Node 0 baseline full exit: 0`。

机器可读基线见 `wiki/docs/upgrade/node-0-baseline.json`。

## 4. 性能、体积与安全基线

- 前端 `dist`：2,100 KiB。
- 最大 chunk：`vendor-charts` 698.42 KiB，gzip 231.81 KiB。
- 其次为 `index` 393.14 KiB、`useFocusTrap` 297.58 KiB、`Agent` 157.61 KiB。
- Vite 对超过 500 KiB 的 chunk 发出警告；后续性能节点处理图表拆包。
- npm audit：1 low、1 moderate、1 high、0 critical。
- 当前直接风险包括 ECharts <6.1.0 的 XSS 公告和 Vite 6.4.2 的开发服务器公告；已冻结为后续依赖/安全节点的阻断输入，真实交易仍保持禁用。

## 5. 尚未修复、但已稳定冻结的问题

这些不是节点 0 漏项，而是后续节点的明确输入：

1. 后端 18 个 collection error：旧 loader/provider 测试仍导入已删除模块。
2. 前端 1 个失败：WelcomeScreen 测试仍期待旧文案 `Finance Skills Library`。
3. Python 核心锁不包含 mootdx、baostock、IBKR、DeepSeek、ML/deep-learning 等可选能力。
4. Tushare 和本地 Data Bridge 未配置。
5. 前端 3 个 npm audit 公告。
6. 图表 chunk 超过 500 KiB。
7. 完整后端测试仍有已审计的 52 个行为失败；节点 1 开始分类归零。

## 6. 节点 0 变更边界

节点 0 新增或收口的主要文件：

- `.python-version`、`.nvmrc`、`requirements.lock`
- `agent/cli/doctor.py`、`agent/tests/test_doctor.py`
- `agent/tests/test_node0_collection_baseline.py`
- `agent/tests/test_node0_frontend_baseline.py`
- `scripts/node0-baseline`
- `scripts/check_node0_collection.py`
- `scripts/check_node0_frontend.py`
- 本报告、机器基线和两份已知失败清单
- CI、Docker、CLI 分派、前端 Node 兼容版本及 Runtime 测试竞态的最小修改

用户确认验收后，现有业务加工改动与节点 0 基线会一并固化为可恢复的检查点提交，并创建 `upgrade-node-0-accepted` 标签。节点 1 从这个精确快照继续，避免 accepted 标签错误地指向加工前 HEAD。

## 7. 用户确认结果与下一步

用户已于 2026-07-17 确认节点 0 验收通过：

1. 固化节点 0 检查点并创建 accepted 标签；
2. 进入节点 1“完成半迁移、恢复全量测试收集与契约一致性”；
3. 优先恢复 18 个 loader/provider 兼容入口或迁移相应测试；
4. 修正 WelcomeScreen 旧断言；
5. 以“后端 collection errors = 0、前端 227/227、无新增回归”为节点 1 第一阶段验收门。
