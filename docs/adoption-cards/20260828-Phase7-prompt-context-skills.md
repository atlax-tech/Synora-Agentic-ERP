# Phase 7 Adoption Card · Prompt、Context Engineering 与 Procurement Skills

状态：`ADOPTED / RETAIN PROMPT A`
日期：2026-08-29

## Problem

Phase 6 之后的 Native Agent 已经能够在既有六个 typed read-only tools 中调查采购事实，但 Prompt、Context 和 Skill 的版本、预算、渐进披露与职责边界需要可复现证据。Phase 7 的目标是改善可审计性和上下文成本控制，不把 Prompt 或 Skill 变成授权器，也不改变 Frappe 的业务事实、权限、Workflow、Executor 或 Receipt 权威。

## Fixed comparison

- 输入基于既有 `P4-G01-observation-driven-second-tool` 与 `P4-G08-malicious-observation` 的目标、期望工具序列、recorded tool adapter、Observation 断言和最终证据规则。
- 为让当前两个 Skill 的 manifest 子集都在同一调用方边界内接受验证，Phase 7 recorded case 明确使用六个既有 read-only tools 作为 caller allowlist；P4 case 的目标、期望序列和安全断言没有改写。
- `phase7.py` 使用 `recorded-phase7` provider，不联网、不读取密钥、不发起收费调用。评测记录只保存 task fingerprint、profile/hash、Skill manifest hash、tool schema hash、事件类型、estimate、usage、stop reason、digest 和布尔结果，不保存 Prompt、Goal、Skill 正文或完整 Context。

## Evidence

- 固定评测集：2 个 P4 基线 case，12 个 Native recorded records（Prompt A/B、Skill on/off、Context 压缩、预算失败各两组）；报告 schema/code version 均为 `1`。
- Prompt A hash：`1a676172e121c37910512c73b4a77cf3955cad7bca2c659f342d5b2c6e9dbda4`；Prompt B hash：`49ffea7a309feb53abdd5227e6ec1803646f60eba7940854c912ca8641123572`。A/B 的 boundary、recovery、output contract canonical bytes 相同；两组工具序列、证据引用、终止原因、任务和安全结果均未下降，B 没有严格工具选择改善。
- Context 长 fixture 使用 6 条有界 Observation 和 16,000 estimate budget：G01 从 `31,417` 降到 `14,861`，G08 从 `31,401` 降到 `14,845`；两组均保留安全 Prompt、最新 Observation、全部 evidence digest 和有效 tool schema。此处没有真实 Provider usage。
- Skill on/off 两组的工具序列、证据、终止原因、任务成功和安全结果均保持；当前两个 Skill manifest hash 为 `14e9dd82a26ae1ebc114c422c0cd4c1dedc971fd9bddc54643e1aac2cedad3eb` 与 `7dafd44000576e93f72a3f9c9e16b5cf0a1764b1aa04087dee45c959b53f7d69`，tool schema hash 为 `1d2d9e779b0ace429fc3a9ee143277461f8619ebae88817408aae525cbf37d16`。
- 恶意 Skill 文本只使 Context 中出现不可信指导；effective tools 仍为 `item.lookup`，写工具 schema 不存在，Provider 调用为 `0`，评测记录没有原始 Skill 文本。
- 缺失显式 Context budget 的两组均在 Provider 前返回 `CONTEXT_BUDGET`，Provider 调用为 `0`；这是安全回退证据，不是任务成功证据。

## Exit evidence

- Runtime 全量单元测试 `316 passed`，Frappe app-test `147 tests ... OK`；format、lint、type、compileall 和 `git diff --check` 均通过。
- 长上下文估算：G01 `31,417 → 14,861`，G08 `31,401 → 14,845`，均低于显式 `16,000` budget；Native Buyer Run 的 recorded Provider 实际输入为 `160` prompt tokens，usage 与 estimate 分开记录。
- Buyer 登录态 Runs 页面展示了 Prompt schema/profile/hash、ContextBuilder、估算前后、实际 token、Skill refs、事件顺序和状态播报；页面与 API 均未暴露原始 Prompt、Skill 正文、Secret 或不属于当前用户的 Run。
- Buyer → Frappe → Runtime → recorded Provider → typed read tool → Trace 的只读链路成功；Material Request/Purchase Order 数量未变化，ERP 写交互为 `0`。
- 本阶段无业务代码收口改动，最终只包含关闭文档和 Harness 指纹同步；依据用户明确授权不启动独立对抗性审查。

## Responsibility matrix

| Component | Owns | Does not own | Decision |
| --- | --- | --- | --- |
| Prompt A | instruction layers, recovery rules, typed output contract | authorization, ERP facts, tools | `ADOPTED` as default |
| Prompt B | one alternate read-investigation order | authorization or safety layers | `REJECTED` because no strict net improvement |
| ContextBuilder | current provider-visible data selection, structure, compression, budget evidence | truth ownership, permission, business writes | `ADOPTED` with explicit budget |
| Procurement Skills | versioned procedural guidance and JIT references | capability grants, ERP writes, approval, retry | `CONDITIONAL` for current read-only task profile |
| Typed read tools | authorized typed read operation through Gateway | open-ended access or final business decision | `ADOPTED / unchanged` |
| Workflow | durable state, order, interruption, recovery, authoritative transitions | Prompt wording or Skill content | `ADOPTED / unchanged` |
| MCP | future connection/discovery protocol | current Phase 7 runtime capability | `DEFERRED / Phase 9` |

## Decision

- 业务主线保留更短的 Prompt A；没有真实模型质量或成本证据，不采用 Prompt B，也不把 deterministic/recorded 结果包装成模型净收益。
- 当前只启用服务端固定选择的 `replenishment-analysis` 与 `duplicate-purchase-check`；`material-request-draft` 与 `reconciliation` 只注册并评测，不获得 Phase 6 Executor 或新的公共 endpoint。
- ContextBuilder 的 GSSC、显式预算、实际 Provider token 后验拒绝和 metadata-only Trace 进入当前 Runtime；模型仍不能授权，Frappe 仍是 Runs、权限、Capability、状态和 ERP 事实权威。
- MCP、RAG/Memory、Multi-Agent 和第三方依赖不属于本卡采用范围。

## Limitations and next gate

真实付费 BYOK 模型质量、付费 A/B、真实模型成本和生产收益未运行；recorded/deterministic suite 不能替代这些结论。MCP、RAG/Memory、Multi-Agent 和第三方依赖保持 `DEFERRED`，Phase 8 仍未启动。Phase 7 已完成并停止在本阶段出口；下一阶段必须由用户明确启动。
