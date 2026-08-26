# ADR-0004：Phase 3 不采用 LangGraph，保持确定性工作流服务

- 状态：已批准（P3.6 工作流 Spike 结论）
- 日期：2026-08-25
- 关联：`docs/PLAN.md` P3.6 与未决项 `workflow-engine-spike`；`docs/ARCHITECTURE.md` "Stateful Agent workflow"（`CONDITIONAL`，仅在 interruption/approval/resume/reconciliation 实测需要成立时采用）；`docs/SPEC.md` §8.1（确定性 Run 状态机）、§11（幂等与对账）

## 背景（Context）

P3.6 要求验证 interruption、approval、resume、reconciliation 的实测需要，只有成立时才采用 LangGraph；无明确收益则保持确定性服务。未决项 `workflow-engine-spike` 最迟在进入 Phase 4 前解决。

Phase 3 的实际实现证据（2026-08-25）：

- **P3.2**：Run 生命周期由 Frappe 侧确定性状态机（`agent/state_machine.py`，SPEC §8.1 全转换表，非法转换 fail-closed）驱动；取消即终态，capability 同步失效。
- **P3.3/P3.5**：分析（CREATED→ANALYZING→PROPOSED）与计划（PROPOSED→SUCCEEDED）都是**同步的确定性步骤**（Frappe 进程内调用 typed 只读工具 + 纯函数计算），无长时间运行、无跨请求状态需要保存/恢复。
- **Phase 3 无写操作**：持久 interruption/resume 在 Phase 5 验证，approval/reconciliation 等写入语义在 Phase 6 才启用；Phase 3 的只读链路中不存在"执行到一半需要恢复"的状态。

## 决策（Decision）

1. **Phase 3 不采用 LangGraph**，保持确定性服务（Frappe 状态机 + Runtime 同步编排）。
2. `workflow-engine-spike` 未决项以本 ADR 为 Phase 3 出口证据：**无明确收益则不引入**（符合 PLAN P3.6 默认）。
3. Phase 5 进行持久工作流实验时，以独立 Spike 对比手写工作流、LangGraph 或 Frappe 后台任务方案；Phase 6 启用写入前必须完成中断恢复、审批编排和响应丢失恢复的安全门禁。

## 备选方案（Alternatives）

1. **采用 LangGraph checkpoint/resume**：Phase 3 无多步模型编排、无跨请求持久状态（检查点只存在于 Runtime 存储，且 SPEC §6 禁止把 checkpoint 当业务事实）；引入框架增加依赖与复杂度而无实测收益。否决。
2. **引入通用工作流引擎（如 Frappe Workflow 用于业务、Temporal 用于编排）**：ERP Workflow 属企业配置未决项（`approval-workflow-mapping`），Phase 6 启用写入前完成取证；Temporal 类基础设施在无实测需要时违反"无测量需求不引入复杂基础设施"（PLAN §7）。否决。

## 后果（Consequences）

- 正向：Phase 3 保持最小依赖（仅 SQLite 已用于 FTS5 检索与开发 checkpoint）；状态转换可审计、可复跑；为 Phase 4 保留确定性状态机作为唯一状态权威。
- 代价：Phase 4 若出现中断/恢复需要，需在那一刻补 Spike（已列入决策 3 的门禁）；本 ADR 不授权任何编排框架的预引入。
- 不变项：Run 状态机（SPEC §8.1）、幂等与对账（SPEC §11）语义不变；`approval-workflow-mapping` 未决项不受影响。

## 证据（Evidence）

- `synora_agentic_erp/agent/state_machine.py`：确定性状态机（SPEC §8.1 全转换表），已定义 Phase 4 中断/对账场景转换（AWAITING_APPROVAL→EXPIRED/EXECUTING、RECONCILIATION_REQUIRED→SUCCEEDED/FAILED）。
- `tests/test_run_state_machine.py`：上述 Phase 4 场景转换被单测覆盖（_LEGAL/_ILLEGAL 全表），"中断→恢复"所需的状态权威已有可测落点；实测需要不成立的是"引入编排框架"本身，不是状态机能力。
- `synora_agentic_erp/agent/service.py`：analyze_run / plan_run 同步编排（无 checkpoint、无跨请求状态）。
- P3.2–P3.5 集成测试：app-test 59/59；真实 HTTP 冒烟：issue→analyze→plan 全链路 SUCCEEDED。
- ARCHITECTURE "Stateful Agent workflow"：`CONDITIONAL`，采用条件是实测需要成立——本 ADR 记录 Phase 3 该条件不成立，Phase 5 重新对照，Phase 6 写入门禁前完成取舍。
