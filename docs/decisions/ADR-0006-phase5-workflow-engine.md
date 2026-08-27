# ADR-0006：Phase 5 持久工作流引擎与对照边界

- 状态：`ADOPTED / Phase 5 出口 PASS（手写引擎主线）`
- 日期：2026-08-27
- 关联：`docs/PLAN.md` Phase 5、`docs/SPEC.md` §8.1/Phase 5、`docs/ARCHITECTURE.md` Agent Execution Modes、ADR-0004

## 背景

Phase 5 要把一次性只读 Agent 执行变成可暂停、可恢复、可取消、可过期和可调试的长任务。Runtime checkpoint 只能表达编排进度，不能替代 Frappe Run、权限、capability、Gateway 账本或 ERPNext 事实。恢复还必须避免把已完成工具重新送入 ERP Handler。

## 决策

1. 业务主线采用手写、逐步推进的 Plan-and-Execute 引擎。`WorkflowState`、`WorkflowResult`、DAG 校验、revision CAS、lease、SQLite WAL checkpoint 和 Frappe invocation ledger 是公共安全边界；固定 Workflow 与 ReAct 子图使用同一协议做对照。
2. `PLAN_EXECUTE` 是显式、非默认、只读模式；`DETERMINISTIC` 仍是默认模式，既有 `AGENT` 不删除。Phase 5 不增加任何 ERP 写工具，也不创建 ProposedAction、Approval、Draft 或 Receipt。
3. LangGraph 只保留为 `workflow-lab` 可选依赖和严格的实验适配器。Python 3.14 的临时 import/interrupt/resume/SQLite Spike 已通过，但当前没有足以证明业务主线支配优势的同任务安全、恢复、运维和真实集成证据，因此不进入 Runtime 生产依赖。
4. n8n 只作为 `LAB_ONLY` 低代码对照，限定 Manual Trigger、Set、If 和 loopback recorded Gateway；不允许 capability、ERP 凭证、生产数据、任意外网、数据库、文件系统或 Execute Command。
5. SQLite checkpoint 仅声明开发和已验证单实例 Runtime 边界。生产多实例存储、扩展和 retention 不在本 ADR 中臆测，留作后续有 owner 的架构门禁。

## 后果

- Frappe 继续是 Run 生命周期、用户权限、24 小时 workflow deadline、5 分钟 capability、取消/过期和授权的唯一权威；Runtime 只能保存非权威 checkpoint 并通过 typed Gateway 读 ERP。
- invocation id 由 `run + plan_version + step + tool/version + canonical args digest` 确定性生成。completed 结果可在重新检查 capability、权限和 scope 后返回缓存；`STARTED` 无结果时只报告不确定窗口，不自动重放。
- 运行时和页面能够展示步骤、revision、澄清、停止原因、crash-recovered 标记和 Trace 摘要，但不展示 Prompt、隐藏思维链、capability、Cookie 或原始敏感 ERP 响应。
- 因此本 ADR 记录的是当前可采用的边界，并作为 Phase 5 `PASS` 的引擎取舍依据；它不授权 Phase 6 提前开放 ERP 写入。

## 已确认的证据

- `services/agent_runtime/src/agent_runtime/workflow/` 提供严格契约、手写引擎、SQLite checkpoint、Runtime start/resume/cancel/status 和可选 LangGraph 适配器。
- P5-G01～G10 固定数据集、手写 Fixed/ReAct/Plan-and-Execute 同任务对照和 Runtime API 定向测试已运行；最近一次 Runtime workflow/contract/engine 定向结果为 `24 passed, 2 warnings`，Frappe integration 为 `86 tests OK`。
- Frappe invocation ledger 的完成缓存、`STARTED` 不重放、参数 digest 冲突、PLAN_EXECUTE deadline 和跨用户 workflow status 保护由 `synora_agentic_erp/tests/test_workflow_run.py` 覆盖。
- LangGraph `1.2.11` 与 `langgraph-checkpoint-sqlite 3.1.1` 在临时 Python 3.14 环境通过 import/interrupt/resume/SQLite Spike；依赖仍锁在 `workflow-lab`，没有接入业务 Runtime。
- 真实浏览器已完成 PLAN_EXECUTE 创建、clarification 中断、页面重载、恢复、取消、过期、Runtime unavailable 和不同用户隔离；真实 Runtime 停止后使用同一 SQLite checkpoint 恢复同一 Run，Revision 3 保留并完成到 Revision 6。取消/失败终态的陈旧 checkpoint 遮蔽回归随后由 86 个 Frappe 测试覆盖。

## 已关闭的出口门禁与保留限制

- n8n LAB_ONLY 证据已形成：固定 arm64 digest 的 `docker pull`、CLI `import:workflow`、只读 `execute` 和官方全类别 `audit` 均退出码 0；最终节点返回 `safe_result=recorded read succeeded`。官方 audit 原样报告允许的 `n8n-nodes-base.httpRequest` 为通用风险节点，并显示 hardened 实例设置；该风险被保留为低代码取舍证据，n8n 不进入业务 Runtime。
- 独立对抗审查已在最终 diff 和全量证据后完成两轮：第一轮 `CHANGES_REQUIRED`，修复终态陈旧 checkpoint 后第二轮 `PASS`；审查角色未修改代码。
- Harness managed/source fingerprint drift 已按 `P5-HARNESS-CLOSE-20260827-v1` 的文件级 proposal 完成同步；同步只更新已审查文档的阶段状态、工作流决策登记、source index 和基线指纹，没有修改业务代码、README、`.env*`、上游或学习笔记正文。

上述出口证据和 Harness 基线已闭环，Phase 5 以 `PASS` 收口；不得因此进入 Phase 6，下一阶段仍必须先完成 `approval-workflow-mapping`。

## 复验门禁

后续若业务代码、工作流契约、n8n digest 或安全边界发生变化，必须重新复核 n8n、浏览器、阶段命令、`ponytail-audit`/`ponytail-debt`/`harness-check` 和独立审查；本次仅是 Harness/阶段文档收口且无业务代码变动，沿用最终独立审查第二轮 `PASS`，不重复调用审查角色。
