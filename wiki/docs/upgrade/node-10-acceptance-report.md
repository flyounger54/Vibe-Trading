# 节点 10 验收报告：前端与 API 使用体验升级

日期：2026-07-18
分支：`upgrade/node-10-frontend-api-experience`
基线：`upgrade-node-9-accepted`（`9ba0f8e`）
状态：用户已确认验收；随节点 10 提交归档

## 审计结论

1. Vite 开发代理原先没有覆盖 `/api/v1`，版本化 API 请求会落入 SPA HTML，前端最终显示原始 JSON 解析错误。
2. 后端普通异常处理已有错误信封，但认证、Host/Origin、幂等和限流中间件的提前返回仍是旧 `{detail}`；未捕获的 500 也没有稳定机器语义。
3. 前端 JSON、上传、SSE 和少量直接 `fetch` 各自处理鉴权、超时和错误；旧客户端丢失 `code`、`request_id`、`retryable` 和 `details`，并会自动重试非幂等 POST。
4. 会话列表和多个研究/训练小部件会吞掉加载失败；路由缺少统一错误页和 404，部分关键页只显示 toast，用户无法原地恢复。
5. 375px 下固定 256px 侧栏把主内容压缩到约 119px，多条核心路径发生裁切和横向溢出；没有移动导航。
6. 正向基础是已有 skip link 和可见焦点样式；主要缺口是当前路由语义、对话框焦点/角色、状态播报、表单标签、图表文本替代、触控尺寸和 reduced-motion。

## 分步实施与验收

### 10.1 开发/生产 API 一致性

- Vite 优先代理 `/api/v1`，不再回落为 `index.html`。
- 所有 v1 路由在 OpenAPI 中声明统一 `ErrorEnvelope`。
- 认证、跨站、Host、URL key、限流、幂等、校验、HTTP 异常和未捕获 500 全部返回 `code/message/request_id/retryable/details`；旧路由继续保留 `{detail}` 兼容形状。
- 502/504 映射为稳定 `unavailable` 错误码；500 不向客户端泄漏异常细节，完整异常只写服务端日志。

### 10.2 前端 API 客户端收敛

- 新增单一传输层，统一 `/api/v1`、Bearer key、同源凭据、超时、取消、网络/解析错误和 `Retry-After`。
- 只有 GET、HEAD 或显式 `Idempotency-Key` 请求可自动重试；普通 POST 不再隐式重放。
- JSON、上传和 SSE 共用稳定错误解析；XHR 上传保留进度，同时获得超时、取消和错误信封语义。
- 前端 `ApiError` 保留 `kind/status/code/requestId/retryable/details/retryAfterMs`；生成客户端接入同一传输层，Correlation 已通过生成的 operation id 调用。
- 生产代码中的网络入口已收敛为传输层和 SSE 实现，不再存在页面级直接 `fetch`。

### 10.3 加载、空、失败与恢复状态

- 新增本地化页面加载状态、路由错误页和 404 返回入口。
- 会话加载、重命名、删除不再静默失败；列表失败可原地重试。
- Reports、Runtime、Correlation、Compare、Industry Chain、Strategy Zoo、ML Features/Compare 和产业链研究小部件补齐失败提示与重试。
- ErrorBoundary 新增可操作恢复按钮；空数据与失败状态不再混用。

### 10.4 响应式与可访问性

- 侧栏改为移动抽屉；关闭时从 DOM/可访问性树移除，打开后焦点进入关闭按钮，路由切换后焦点进入主内容。
- 375px 核心路由主区恢复为 375px，页面宽度与视口一致；1440px 下保持 256px 侧栏和 1184px 主区。
- 导航加入真实 `<nav>` 标签和 `aria-current`；状态加入 `role=status/alert` 与 `aria-live`。
- 表单标签、字段组、按钮状态、对话框角色/焦点陷阱、图表文本替代和主要触控目标已补齐。
- 新增 reduced-motion 降级，避免加载和交互动效强制播放。

### 10.5 自动化门禁与真实界面烟测

- 新增 API 传输、生成客户端、稳定错误信封、移动 Layout、可恢复会话错误和 404 可访问性回归。
- 使用临时状态数据库、临时安全文件和测试 API key 启动隔离后端；没有写入用户状态。
- 真实 Vite 代理验证：无 key 的 `/api/v1/sessions` 返回稳定 401；有效测试 key 返回 `200 []`。
- 真实错误验证：不存在 run 返回稳定 404；无效 ML 请求返回稳定 422 且保留字段级 `details`。
- 真实浏览器验证覆盖 `/`、`/runtime`、`/reports`、`/correlation`、`/industry-chain`、404 和 `/settings` 鉴权恢复。

## 验收证据

| 项目 | 结果 |
| --- | --- |
| 前端全量测试 | `32` 个测试文件、`239 passed` |
| 后端 API/安全专项 | `20 passed` |
| 后端全量回归 | `4480 passed, 2 skipped, 0 failed`（266.46s） |
| API 契约 | `scripts/generate-api-contracts --check` 成功，`146 paths / 61 schemas` |
| 前端生产构建 | 成功；无 TypeScript 错误 |
| Python 静态门禁 | `compileall`、变更面 Ruff 均通过 |
| 变更检查 | `git diff --check` 通过 |
| 移动端真实界面 | 375×812，六条核心路由 `scrollWidth = clientWidth = 375`，主区 `x=0, width=375` |
| 桌面端真实界面 | 1440×900，侧栏 `x=0, width=256`，主区 `x=256, width=1184` |
| 导航与焦点 | 移动抽屉关闭时不在可访问性树；打开后聚焦关闭按钮；路由切换后聚焦 `#main-content` |

## 已知非阻塞项

- `vendor-charts` 压缩前约 711 kB，Vite 继续给出大包体提示；本节点已按路由懒加载页面，但 ECharts 自身仍适合在后续性能节点继续拆分。
- 后端全量测试继续显示既有 FastAPI 生命周期/Starlette TestClient 弃用提示，以及数值策略常量输入警告；断言结果不受影响。
- 设置页读取项目现有 LLM/Data Source 配置；本节点浏览器烟测只保存隔离服务的测试 API key 到测试浏览器 session storage，没有提交或修改模型、数据源凭据。

## 验收边界

用户已明确确认节点 10 验收通过。节点 10 变更将按既定流程提交，并创建 `upgrade-node-10-accepted` 验收标签。
