# Vibe-Trading 升级节点 3 技术验收报告

更新时间：2026-07-17 21:27（Asia/Shanghai）  
分支：`upgrade/node-3-security-execution-isolation`  
起点：`80bda92` / `upgrade-node-2-accepted`  
状态：**实现与技术验收完成；用户已确认节点 3 验收通过**

## 1. 目标与当前结论

节点 3 的目标是收口全 API 鉴权、消除 URL 长期密钥、统一 ID/路径/URL/上传边界，并把生成策略从 API 主进程和宿主机中隔离。

代码、攻击回归、全量回归、依赖审计和真实容器逃逸探针均已通过。真实运行发现并修复了 Docker `--mount` 的无效 `,rw` 字段，以及 macOS 默认临时目录无法映射进 Colima VM 的问题。用户已于 2026-07-17 明确确认节点 3 验收通过，准许固化 accepted commit/tag 并进入节点 4。

| 验收面 | 结果 |
|---|---|
| API 权限矩阵 | 151 条 APIRoute：149 Bearer，2 anonymous (`/healthz`, `/readyz`) |
| 本地密钥 | 首次启动生成 256-bit key；父目录 `0700`，文件 `0600` |
| SSE | fetch streaming + Authorization；前端源码无 `api_key=`/`EventSource` 密钥路径 |
| 攻击回归 | traversal、symlink、SSRF、DNS 混合解析、未授权读取、SSE key、duplicate POST、恶意策略均通过 |
| 后端全量 | 4,379 pass / 2 skip / 0 fail |
| 前端全量 | 28 files / 227 pass / 0 fail；生产构建通过 |
| OpenAPI | 132 paths / 59 schemas；生成物无漂移 |
| Bandit | High = 0 |
| pip-audit | 锁文件与当前环境均为 0 已知漏洞 |
| npm audit | 0 vulnerabilities |
| 许可证 | Python 206、npm production 134；GPL/AGPL 阻断项 = 0 |
| 真实容器逃逸 | pass：六项隔离断言全部为 `true` |

## 2. 分步实现与验收

### 2.1 统一鉴权、密钥与权限矩阵

- 删除 loopback 和 Docker gateway 鉴权绕过；IP 只用于 Host/DNS-rebinding 判断。
- 没有 `API_AUTH_KEY` 时，由 `secrets.token_urlsafe(32)` 生成本地 key，并以原子 exclusive create 写入运行目录。
- 全局安全中间件覆盖模块动态注册的路由，避免逐路由依赖遗漏；静态 SPA、CORS preflight、`/healthz`、`/readyz` 之外全部要求 Bearer。
- 自动权限矩阵动态枚举全部 FastAPI APIRoute；结构测试固定匿名集合只能是两个 liveness endpoint。
- `/docs`、`/redoc`、`/openapi.json`、旧 `/health`、行业链、策略、swarm presets 等旧匿名面均已进入鉴权边界。

### 2.2 SSE、请求安全与审计

- 移除 `authQuerySuffix`/`withAuthQuery` 和服务端 query-key 接受逻辑；任何 `api_key` URL 参数直接返回 400。
- 新增 fetch SSE parser，使用 `Authorization` 和标准 `Last-Event-ID` 请求头，支持事件类型、多行 data、abort 和断线重连。
- 每个响应附加规范化 `X-Request-ID`、nosniff、DENY frame、no-referrer、CSP 和 Permissions-Policy。
- 内存滑动窗口限流返回 429/Retry-After；带 `Idempotency-Key` 的重复写请求返回 409，阻止重复 POST side effect。
- HTTP 审计日志只记录白名单字段，私有目录/文件权限为 `0700/0600`；Uvicorn/API 日志 filter 清除 query key 和 Bearer token。

### 2.3 ID、文件根、SSRF 与上传

- 为 run/session/swarm/job/model/chain/artifact/strategy 建立按类型正则，不再使用一个宽松路径字符类解释所有 ID。
- `resolve_within_root` 在 `resolve()` 后执行 `relative_to()`，模型和产业链读写拒绝 traversal 与 symlink escape。
- Provider base URL 只接受 HTTP(S)，拒绝 userinfo/query/fragment、私网、loopback、link-local、metadata address、非全局 IP 和 DNS 返回中的任一私网地址；远程 provider 强制 HTTPS，Ollama 才可显式 loopback。
- 上传采用流式大小门禁，并同时检查 allowlisted extension、客户端 MIME family、文件 magic、UTF-8/NUL 和可执行 magic；伪装文本与 Office/image signature mismatch 会被删除并拒绝。
- shell/background tool 移除 `shell=True`，命令通过 `shlex.split` 后以 argv 执行；API shell 工具仍需显式 opt-in。

### 2.4 生成策略执行隔离

- `Runner` 默认 `container`；Docker 不可用时返回 exit 126，不会隐式退回宿主机 Python。
- worker 命令固定 `--network=none`、`--read-only`、`--cap-drop=ALL`、`no-new-privileges`、非 root UID、PID/CPU/memory/swap/nofile/nproc 限制、noexec tmpfs。
- 只把当前 run 目录以读写方式挂载到 `/workspace/run`；宿主项目、其他 run、home、凭据目录均不挂载。
- stdout/stderr 由父进程持续 drain 并分别限为 1 MiB；超时后 kill，返回 124。
- `dangerous-local` 是唯一宿主执行开关，控制台会显式警告；默认/未知值均回到 container fail-closed。
- 新增独立 `Dockerfile.sandbox` 和 `scripts/test-sandbox-runtime`。真实探针验证 non-root、无网络、只读 `/app`、不可读 `/etc/shadow`、不可访问宿主 secret 和其他 run。
- 真实运行验证了 `--mount` 参数兼容性；读写 bind mount 使用 Docker 的默认读写模式，不再传入无效的 `,rw` 字段。
- 探针临时目录固定创建在仓库根目录下，使 macOS/Colima 与 Linux CI 使用同一条可见 bind-mount 路径；退出时自动清理。

### 2.5 供应链与质量门禁

- 修复 npm High：Vite `6.4.3`；修复 ECharts moderate：`6.1.0`。
- 修复 Python advisories：LangChain `1.3.9`、LangGraph `1.2.9`、MCP `1.28.1`、Pillow `12.3.0`、setuptools `83.0.0`（build environment）。
- 缓存/去重用 MD5/SHA1 明确声明 `usedforsecurity=False`；Bandit High 从 11 降至 0。
- 新增 `scripts/node3-acceptance --quick|--full`；真实容器探针无法执行时门禁故意失败，防止把静态命令检查误报为完整 sandbox 验收。

## 3. 已执行验收记录

| 命令/门禁 | 结果 |
|---|---|
| `.venv/bin/python -m pytest -q` | 4,379 pass / 2 skip / 0 fail，221.09s |
| `npm run build` | pass，Vite 6.4.3 / 2,764 modules |
| frontend exact gate | 28 files / 227 pass |
| `scripts/generate-api-contracts --check` | pass，132 paths / 59 schemas |
| Ruff changed surface | pass |
| `bandit -lll` | pass，High = 0 |
| `pip-audit -r requirements.lock` | no known vulnerabilities |
| `pip-audit --local` | no known vulnerabilities |
| `npm audit --audit-level=high` | 0 vulnerabilities |
| Python/npm license scans | blocked = 0 |
| `git diff --check` / shell syntax | pass |
| `scripts/node3-acceptance --quick` | 全部步骤 pass，包含真实容器探针，最终 exit 0 |
| `scripts/test-sandbox-runtime` | pass：non-root、network、host secret、other run、shadow、read-only 六项隔离断言全部通过 |

## 4. 非阻断警告与边界

- 2 个 skip 和 13 个 warning 沿用既有环境：FastAPI lifespan、Starlette/httpx 和 NumPy 合成信号数据 warning。
- Vite 仍提示 chart chunk 大于 500 KiB；这是性能节点事项，不是节点 3 安全退出条件。
- 本地额外安装的 `mootdx 0.11.7` 声明 `httpx<0.26`，而项目使用 `httpx 0.28`；它不在 `requirements.lock`，全量测试与锁文件审计不受影响，但应在数据兼容节点隔离处理。
- 当前机器 Node 为 24，项目正式 engine 为 Node 20；前端构建/测试已通过，但正式 CI 仍以 Node 20 为准。

## 5. 下一验收动作

节点 3 已满足技术退出条件，并已由用户确认通过。接下来固化 accepted commit/tag，然后进入节点 4。复验命令：

```bash
cd /Users/flyounger54/Desktop/quant-system/Vibe-Trading
scripts/test-sandbox-runtime
scripts/node3-acceptance --quick
```
