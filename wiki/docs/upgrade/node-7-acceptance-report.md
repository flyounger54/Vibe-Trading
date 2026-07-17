# 节点 7 验收报告：策略库与 Alpha Zoo 整理

## 结果

节点 7 已完成，策略、Alpha、skills 与 swarm presets 的发布库存改由同一套运行时发现机制生成。当前清单快照：42 个策略、457 个 Alpha、85 个 skills、31 个 presets；四类加载失败均为 0。

权威可再生清单：[`wiki/docs/catalog/runtime-catalog.md`](../catalog/runtime-catalog.md) 与同目录 JSON manifest。执行 `python agent/scripts/build_catalog.py` 可重建；测试会阻止已提交文档与运行时注册表漂移。

## 已实现

- 新增确定性统一 manifest：策略注册表、Alpha 注册表、内置 skills 与 YAML presets 都纳入一个带 SHA-256 内容指纹的 JSON/Markdown 清单。
- 策略元数据补齐 `required_params`、`required_any_of`、`configuration_requirements` 与 `directly_runnable`；保留原有市场、输入字段、风险与默认参数元数据。
- `mf_ml_predictor` 明确标记为不可默认运行，未提供 `model_id` 或 `schedule_name` 时在注册表/运行器入口返回可操作的配置错误，不再进入默认策略回归集合或推荐结果。
- 修正三项策略的 `min_bars` 使其与实际暖机期一致；移除因子轮动中的未使用前向收益计算。
- 对所有默认可运行策略增加确定性、未来数据扰动（look-ahead）、正常/短历史、索引形状、NaN/Inf 与 [-1, 1] 信号范围测试。
- sentiment Chokepoint Alpha 仅依赖声明的 `volume` 面板；Alpha 纯度门禁继续禁止模块级可执行状态、网络/I/O 与反射逃逸。
- 策略和 Alpha Zoo 前端展示、帮助文案与系统提示移除失真的静态库存数字，改用 API/运行时发现结果。

## 验证证据

| 检查 | 结果 |
| --- | --- |
| 全仓 Python 回归：`.venv/bin/pytest -q` | 4,454 passed、2 skipped、23 warnings，240.55s |
| 节点 7 清单一致性测试 | 5 passed |
| API 合约、策略 API/Runner、清单专项 | 30 passed |
| Alpha purity + look-ahead 全量覆盖 | 已包含于全仓回归 |
| Ruff（节点涉及 Python 模块） | All checks passed |
| 前端生产构建：`npm --prefix frontend run build` | 通过 |

已知警告均为现有 FastAPI 生命周期弃用提示与部分策略交叉相关计算在恒定窗口上的 NumPy runtime warning；未出现测试失败或信号契约违例。
