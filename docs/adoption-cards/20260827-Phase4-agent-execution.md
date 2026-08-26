# Phase 4 Adoption Card · 有界 Agent 执行内核与原生 Tool Calling

状态：`CONDITIONAL / DETERMINISTIC 默认`  
日期：2026-08-27

## Problem

Phase 3 的确定性采购分析可以基于固定流程读取库存、需求、物料申请和采购订单，但不能在第一次观察后动态选择下一类只读事实。Phase 4 评估一个有界的 Agent 探索层，目标是改善只读事实的探索路径；它不负责数量、金额、风险、状态机、权限或 ERP 写入。

## Preconditions

- Run 必须由当前 Frappe 用户创建，且状态、scope、capability 和 state version 在每次 Gateway 调用重新校验。
- 只允许六类现有只读工具；provider function schema、参数和 Observation 都必须通过严格 typed contract。
- 付费调用前必须有完整、有限且可证明的 pricing 配置；单次输出 512、累计输出 3072、6 步、180 秒、50,000 micro-USD 上限仍有效。
- 真实 BYOK provider、usage/cost 和浏览器新鲜验收尚未在本阶段重新配置/执行，因此不能把条件式 Agent 能力表述为默认生产采用。

## Minimal Lab

- `labs/agent_patterns/` 中的 Direct、bounded ReAct、Plan-and-Solve、Reflection、MiniStepAgent 使用 recorded adapter 和统一 `RunResult`。
- P4-G01 验证第一次 Observation 后选择第二个不同工具；P4-G02 验证完全相同调用在第二次前停止；其余六个 golden case 覆盖未知工具、非法参数、工具失败、无进展、输出预算和恶意 Observation。
- Assignment 1–3 的用户完成事实保留在 Phase 4 开发日志；本阶段后续由 Agent 接手安全关键路径，不新增 Assignment。

## Alternatives

- `DETERMINISTIC`：当前业务默认路径，结果稳定、成本可预测，继续负责最终业务结论。
- bounded ReAct/MiniStepAgent：仓库内可复跑的手写对照，适合学习和后续恢复/工作流实验，但不直接进入业务 provider 路径。
- Plan-and-Solve、Reflection、smolagents：保留为 lab-only 对照；它们没有足够的真实净收益或恢复证据，不进入 Phase 4 业务 Runtime。

## Evidence

- Runtime 全量 pytest：`173 passed`；仓库全量 pytest：`198 passed`。
- Frappe Bench 迁移后 app-test：`72 tests OK`；真实 ERP P4-G01 scripted Runtime 链路证明了第一条 Observation 后的第二个不同只读工具和确定性收口。
- P4-G01–P4-G08 native recorded matrix 与独立安全门禁通过；Trace 保存 action/observation/guard/stop/usage 的有界脱敏证据。
- ruff format/lint、mypy（47 source files）、compileall 和 Harness manifest/reference/structure 通过；Harness drift/health 仍是待管理流程处理的只读结果。
- 未完成证据：真实 BYOK native Tool Calling、实际 provider usage/cost、迁移后新鲜浏览器登录验收；没有读取 `.env*`，CI 不访问付费网络。

## Decision

- 业务默认继续使用 `DETERMINISTIC`。
- 原生 Tool Calling 仅作为用户显式选择的 `AGENT` 条件式能力；在真实 BYOK 成功、P4-G01 真实模型链路、八个安全 case 100%、权限/预算无回归并记录成本前，不扩大默认采用范围。
- smolagents、Plan-and-Solve、Reflection 标记 `LAB_ONLY`；没有证据支持的收益不进入业务 Runtime。

## Real-world Use

当只读采购调查需要根据当前 ERP 事实选择下一种查询，且组织已配置受控 BYOK、可审计 pricing、稳定的 Gateway 权限和人工核验流程时，可以显式启用 Agent 探索。无论探索是否成功，确定性分析/计划仍是业务结果来源；写入、审批、恢复和长期工作流留给后续阶段。

## Interview Answer

我把 Agent 限制成只读探索器：它只能从当前 Run 的六个 typed 工具中选动作，每一步有重复、无进展、频率、token、成本、时间和取消守卫，Observation 用 bounded summary 加 SHA-256 digest 进入 Trace。探索结束后，采购数量、风险和计划仍由确定性代码计算。当前证据证明了 recorded matrix、真实 ERP Gateway 链路和安全回退；因为真实 BYOK 成本证据尚未配置，所以我不会声称 Agent 已默认生产采用。
