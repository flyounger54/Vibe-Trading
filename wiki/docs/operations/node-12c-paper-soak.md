# Node 12C 模拟盘浸泡操作手册

本流程只把一个精确的 `(broker, account_ref, build_revision, policy_version)` 资格键从
`disabled` 推进到 `paper_soak`，并在证据完整后推进到 `pilot_eligible`。它不会进入
`pilot_active`，不会启用实盘，也不能替代 Node 12D 的人工小额试点。

## 证据边界

- 每个 campaign 绑定一个不可变的交易日历清单、paper profile、账户引用哈希、40 位构建提交和资格策略版本。
- 日历至少包含 30 个不重叠且严格递增的交易会话；每日证据只能在对应收盘后 12 小时内按顺序写入，禁止补录、跳日和重复写入。
- `connector qualify run` 必须在会话开盘前一小时内启动，并持续到收盘；心跳间隔不得超过 300 秒。
- 收盘认证从配置的 connector 直接采集 connection、account、open orders 和 quote，并通过统一 paper 下单路径执行一次 client-order-id 写入及一次幂等重放。
- 原始券商响应不落证据文件，只保存 SHA-256 摘要。每日日志使用 Ed25519 链式签名；可导出的 evidence 目录只含公钥，私钥独立保存在 `qualification-signing-keys` 私有目录。
- 任一拒绝日都会保留在签名链中，并把连续合格天数和故障演练进度同时归零。

## 1. 准备签名日历

日历由操作员从目标市场的权威交易日历生成。时间必须包含时区；清单一旦启动即被签名，不可替换。

```json
{
  "calendar_id": "XNYS-2026Q3-release-1",
  "sessions": [
    {
      "trading_day": "2026-08-03",
      "opens_at": "2026-08-03T13:30:00+00:00",
      "closes_at": "2026-08-03T20:00:00+00:00"
    }
  ]
}
```

实际文件必须至少包含 30 个 session。不得用简单工作日推断替代交易所假日清单。

## 2. 启动独立 campaign

```bash
vibe-trading connector qualify start alpaca-paper-trade \
  --account-ref '<paper-account-ref>' \
  --build-revision '<40-hex-immutable-revision>' \
  --calendar ./xnys-sessions.json \
  --actor operator:<name>
```

命令返回 `campaign_id`，并将精确资格键置为 `paper_soak`。已有 `paper_soak`、
`pilot_eligible` 或 `pilot_active` 记录不能被覆盖。

## 3. 准备每日 paper 探针

探针订单必须进入统一 Node 12B paper 风险与幂等通路。金额仍受有效 mandate v2 的全部硬上限约束。

```json
{
  "side": "buy",
  "quantity": 1,
  "order_type": "market",
  "time_in_force": "day",
  "client_order_id": "vt_soak_20260803_0001"
}
```

## 4. 运行完整交易会话

```bash
vibe-trading connector qualify run <campaign-id> \
  --symbol AAPL \
  --probe-order ./probe-order.json \
  --drills ./drills.json \
  --poll-seconds 60
```

`run` 会在开盘前建立 USD 开盘权益基线，持续记录 connector 心跳，并在收盘后完成账户、挂单、行情和幂等探针认证。进程中断或心跳空洞会让当日失败；不能事后伪造全天覆盖。

故障演练文件中的每项必须包含 `passed: true` 和真实制品的 64 位 SHA-256。七项演练必须都发生在当前连续合格窗口内：

1. `restart_recovery`
2. `disconnect_recovery`
3. `duplicate_order_replay`
4. `ledger_tamper_fail_closed`
5. `unauthorized_order_halt`
6. `kill_switch_block`
7. `cancel_while_halted`

若已有受控 paper runner 生成全天 session proof，可用 `connector qualify collect` 让本进程重新读取券商快照并执行探针。生产 CLI 不提供接受原始 observation JSON 的 `record` 命令，session proof 不能替代账户、挂单、行情或订单结果等券商观测。

## 5. 核验进度

```bash
vibe-trading connector qualify status <campaign-id>
```

每次 status 都会从 manifest 公钥重新验证完整签名链和注册表绑定。输出中的 `accepted_days` 是最后一个拒绝日之后的连续合格天数，不是历史累计天数。

## 6. 只晋级到 pilot eligible

```bash
vibe-trading connector qualify promote <campaign-id> \
  --actor release-gate:<name> \
  --confirm
```

只有最后连续 30 个签名会话全部合格，且七项故障演练都在该连续窗口内通过，命令才会写入 `pilot_eligible`。晋级后 `evaluate_live_qualification` 仍返回 `qualification_state_not_active`；Node 12D 之前不得创建 `pilot_active` 记录。
