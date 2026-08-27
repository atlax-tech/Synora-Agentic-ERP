# Phase 5 Adoption Card · 持久工作流与 Plan-and-Execute

状态：`ADOPTED / 主线保留手写引擎；阶段出口 PASS`
日期：2026-08-27

## Problem

一次性只读 Agent 不能跨 Runtime 重启继续，也不能安全地等待用户澄清。Phase 5 需要把计划、步骤、观察摘要、revision、deadline 和中断原因变成可验证的非权威编排状态，同时保证 Frappe 仍掌握 Run、权限、capability、取消/过期和 ERP 事实。

## Minimal Lab

- `services/agent_runtime/src/agent_runtime/workflow/contracts.py`：严格的版本化 PlanStep、DAG、状态、澄清和 replan 契约。
- `services/agent_runtime/src/agent_runtime/workflow/checkpoint.py`：SQLite WAL、foreign keys、busy timeout、0600 文件权限、CAS 和 lease；只保存 JSON 安全的编排数据。
- `synora_agentic_erp/agent/invocation.py` 与 `Synora Workflow Tool Invocation`：以确定性 invocation id 和 canonical args digest 防止 completed 工具重放，并把不确定的 `STARTED` 窗口留给人工恢复。
- `PLAN_EXECUTE` 通过 Frappe `analyze_run`/`resume_run`/`get_run_workflow`/`cancel_run` 进入 Runtime；Run 保持 `ANALYZING` 直到工作流完成或进入受控终态。

## Alternatives

| 方案 | 当前结论 | 原因 |
| --- | --- | --- |
| 手写 Plan-and-Execute | 保留业务主线 | 状态和失败边界透明，依赖最小，能直接映射 Synora typed contract；SQLite 只声明单实例。 |
| 固定 Workflow | 确定性下限 | 用于同任务质量、工具次数和 Trace 对照，不承担澄清/重规划灵活性。 |
| ReAct 子图 | 对照 | 仍受 typed allowlist、预算、revision 和 Gateway 约束，不改变 ERP 权威。 |
| LangGraph | `LAB_ONLY` | Python 3.14 Spike 通过，但尚无安全/恢复/运维支配优势；interrupt resume 可能从节点开头重跑，外部调用必须幂等。 |
| n8n | `LAB_ONLY` | 固定 arm64 digest 已完成 import/execute/audit；官方 audit 对允许的 loopback HTTP Request 报告通用风险提示，因此不进入业务主线。 |

## Evidence

- P5-G01～G10 固定场景覆盖依赖顺序、澄清、崩溃、工具失败、并发 resume、取消、过期、未知 checkpoint 版本、completed 不重放和恶意输入。
- Runtime workflow/contract/engine 定向测试最近为 `24 passed, 2 warnings`；Frappe app-test/integration 最近为 `86 tests OK`。
- 临时 Python 3.14 LangGraph Spike：`langgraph==1.2.11`、`langgraph-checkpoint-sqlite==3.1.1` 的 import/interrupt/resume/SQLite 通过；项目依赖仍是可选 `workflow-lab`。
- 离线同任务对照已运行 Fixed Workflow、ReAct 子图和 Plan-and-Execute，三者都使用相同 recorded Observation，结果为 `SUCCEEDED`、2 次工具调用、同一摘要 digest；LangGraph/n8n 未纳入该三模式 comparison，也未被填成成功行。
- 真实 Frappe 只读工具、调用账本、权限隔离、取消/过期顺序和 Trace 保护已由 integration 覆盖；浏览器已形成创建、中断、恢复、取消、过期、Runtime unavailable 和跨用户隔离证据，真实 Runtime 重启恢复也已形成；独立对抗审查第一轮 `CHANGES_REQUIRED` 的终态陈旧 checkpoint 问题已修复，第二轮 `PASS`；n8n 固定 digest 的 import/execute/audit 已形成 LAB_ONLY 证据，audit 的 HTTP Request 通用风险提示原样保留并未进入主线。
- Harness managed/source fingerprint drift 已按文件级 proposal 同步；本次收口没有新增业务代码变动，因此沿用最终独立对抗审查第二轮 `PASS`，不重复调用审查角色。

## Adoption decision

当前业务路径继续使用手写引擎，`DETERMINISTIC` 仍为默认；`PLAN_EXECUTE` 只在用户显式选择时可用。LangGraph 不进入 Runtime 生产依赖，n8n 不进入业务主线。只有在同一输入、同一 recorded/真实观察、同一安全矩阵下，框架在恢复覆盖、维护代码或运维步骤至少一项明确更优且不降低安全、权限、Trace 和不重放结果，才重新评估。

## Real-world Use

组织可以用 `PLAN_EXECUTE` 做跨请求、只读的采购调查：页面展示等待澄清的步骤，用户回答后以当前 revision 恢复；Runtime 重启或取消不会把 checkpoint 当成 ERP 事实，也不会自动重放有不确定结果的工具。任何 ERP 写入仍必须等待 Phase 6 approval-workflow-mapping 门禁。

## Interview Answer

我把长期工作流拆成两种权威：Frappe 管 Run、身份、权限、capability 和 ERP 事实，Runtime SQLite 只管可恢复的编排位置。每个只读调用用 canonical 参数生成稳定 invocation id；已完成调用返回经过权限重检的缓存，只有 `STARTED` 的不确定窗口会中断并要求人工检查，绝不把“看起来完成”写成 ERP 事实。框架选择以同任务安全和恢复证据为准，所以当前保留透明的手写基线，LangGraph 与 n8n 都留在实验边界。
