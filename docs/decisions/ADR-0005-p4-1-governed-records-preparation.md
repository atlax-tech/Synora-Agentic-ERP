# ADR-0005：P4.1 受治理记录的只读取证与设计准备（已被纠偏路线取代）

- 状态：`SUPERSEDED`（历史取证保留；由 2026-08-26 已批准纠偏路线取代，治理记录移至 Phase 6）
- 日期：2026-08-25
- 关联：`docs/PLAN.md` §13 P4.1、`docs/SPEC.md` §7.2–§7.4/§8.2/§10/§11、`docs/ARCHITECTURE.md` 的 Approval and Workflow Authority、`docs/ACCEPTANCE.md` 的首个受治理写入验收

## 1. 背景与范围

Phase 3 已完成只读 Run、确定性分析、计划和 FTS5 基线。原排期把治理记录准备放在 Phase 4；2026-08-26 纠偏后，Phase 4 只建立执行内核与评测，Phase 5 建立持久工作流，治理记录和首次写入统一移至 Phase 6。

本 ADR 只保留原 P4.1 的证据整理和设计准备：不创建 DocType，不增加写 API/写工具，不创建 MR/PO，不修改 ERPNext/Frappe 上游，不解析或批准 `approval-workflow-mapping`。任何写入实现都需要 Phase 6 的明确阶段指令和独立验收。

## 2. 已确认的外部事实

### 2.1 产品与契约事实

`docs/SPEC.md` 已规定：

- Proposed Action 必须包含 `schema_version`、`action_type`、`run_id`、`action_id`、typed payload、evidence、risk/approval、ERP state snapshot、idempotency 信息、expiry 和 digest；
- Approval Decision 必须绑定 action/proposal digest、审批人、决定、匹配的 Workflow/Policy、状态快照、过期信息、时间和理由；
- Execution Receipt 必须绑定 run/action/approval/idempotency/correlation 标识、payload digest、ERP DocType/name、核验字段、成功/失败类别、最终状态和对账链接；
- 合法状态为 `DRAFT → INVALID/POLICY_REJECTED/AWAITING_APPROVAL`、`AWAITING_APPROVAL → APPROVED/DECLINED/EXPIRED`、`APPROVED → EXECUTED/EXPIRED`；未知状态和非法转换必须 fail closed；
- 原 Phase 4 首批写入仅允许 MR Draft/PO Draft；该范围现移至 Phase 6，仍禁止任意 DocType、任意字段、SQL 或 MCP 写入。

### 2.2 固定 ERP 基线事实

`docs/erp-baselines/phase1-permission-workflow-baseline.md` 的固定取证显示：候选站点没有可供 Phase 4 直接依赖的 Workflow 记录；默认 Purchase User 权限包含 Purchase Order 的 create/submit 能力。这个观察不能替代产品要求的 Draft confirmation、独立审批和 Submit 分离，因此 `approval-workflow-mapping` 继续保持 `UNRESOLVED`，不能据此启用写入。

## 3. 只读取证结果

| 设计对象 | 当前证据 | P4.1 实现前必须补的证据 |
| --- | --- | --- |
| Proposed Action | SPEC 已定义字段、摘要和状态；Phase 3 只生成确定性计划，不生成可执行 action | Frappe DocType/schema、typed payload 版本、状态转换和持久化权限测试 |
| Approval Decision | SPEC 要求 digest、审批人、Workflow/Policy、快照和理由 | 固定 ERP Workflow/Role/Policy 映射、独立审批人和拒绝/修改/过期测试 |
| Execution Receipt | SPEC 要求最终 ERP 状态、失败类别、对账链接和 correlation | ERP controller 创建/读回、响应丢失、重复请求、对账和审计事件实测 |
| Digest | 架构要求在审批和执行前绑定不可变 payload/状态快照 | 规范化编码、版本兼容、digest 变化冲突和重算测试 |
| Workflow engine | ADR-0004 只关闭 Phase 3 的 LangGraph 采用；Phase 5 按纠偏路线重新对照 | interruption、resume、recovery 的实验；Phase 6 写入前完成安全门禁 |

## 4. P4.1 建议的记录契约

以下是待实现的最小设计，不是当前数据库 schema：

### 4.1 Proposed Action

```text
schema_version, action_id, run_id, action_type
typed_payload, evidence_refs, risk_level, approval_requirement
erp_state_snapshot, idempotency_key, expires_at, digest
state, created_by, created_at, updated_at
```

约束：`action_type` 由固定 allowlist 选择；payload 只允许对应 action schema；模型自然语言只能作为说明或 evidence，不能成为执行字段；snapshot 和 expiry 必须在执行前重检；digest 不包含不稳定的展示文本。

### 4.2 Approval Decision

```text
schema_version, decision_id, action_id, action_digest
actor, decision, reason, matched_workflow, matched_policy
state_snapshot, expires_at, decided_at, correlation_id
```

`decision` 只能来自 `ALLOW/DECLINE/REQUEST_CHANGES` 等固定枚举；审批人必须由 ERP 当前登录身份和独立审批规则解析，不能信任 Runtime 自报身份；digest、快照、审批人或过期任一不匹配都拒绝继续。

### 4.3 Execution Receipt

```text
schema_version, receipt_id, run_id, action_id, approval_id
idempotency_key, action_digest, actor, erp_doctype, erp_name
verified_fields, outcome, failure_category, reconciliation_ref
started_at, completed_at, correlation_id
```

Receipt 是执行事实的摘要，不是“API 返回 200”的别名。ERP 返回不确定、超时或读回不一致时，结果必须进入 `RECONCILIATION_REQUIRED`，禁止无证据自动重试。

## 5. Digest 与门禁设计

1. 使用带版本的 canonical JSON：固定字段顺序、UTF-8、无空白、Decimal/日期/UUID 采用已批准字符串格式；拒绝 NaN、无限值、未知字段和重复键。
2. digest 输入至少包括 `schema_version`、`action_type`、typed payload、目标 scope、ERP state snapshot、expiry 和 idempotency 语义；展示文案、模型解释、创建时间等不稳定字段不得改变业务 digest。
3. 使用 SHA-256 或项目已批准的等价密码摘要；存储 digest，不存储 secret；审批、执行、Receipt 均记录所绑定 digest。
4. 执行顺序固定为：schema → identity → permission → deterministic checks → Workflow/Policy → risk/snapshot/expiry/digest → idempotency → ERP controller → read-back → Receipt/reconciliation。
5. 每个门禁都必须在执行前重新读取当前事实；任一未知、冲突、权限缺失、状态漂移或摘要不一致都 fail closed。

### 5.1 模型调用成本与并发门禁

- Phase 3 当前已实现单请求 `max_tokens`、completion/reasoning usage 校验、超预算 fail closed、实际拒绝用量证据，以及同一 Run 的数据库行锁；这些是请求级安全边界，不是账户计费上限。
- P3.5 真实验收只产生一次受控的 BYOK 调用；不同 Run 之间仍没有账户级频率、并发或日额度账本。为避免把该风险带入写入阶段，Phase 6 写入启用前必须先确定并取证：`initiator + provider + model` 配额键、最大 in-flight 数、时间窗 token 额度、调用前预算预留/调用后 usage 结算、usage 缺失时的冻结/人工对账，以及超额审计事件。
- 该门禁应由受治理的 Synora 记录或部署级限流实现，并在多 worker/进程下保持一致；不能依赖单进程 semaphore 或客户端自报额度。本 ADR 只固定设计要求，不实现额度账本或限流器。

## 6. P4.1 验收门禁

只有下面证据齐全后，才可以把本 ADR 从 `PROPOSED` 变成已批准并实现：

- Proposed Action、Approval Decision、Execution Receipt 的 schema/DocType 与版本化 typed contract 已评审；
- 合法/非法/未知状态转换、digest 变化、过期、重复和跨用户访问均有独立测试；
- `approval-workflow-mapping` 已针对固定 Frappe/ERPNext 基线完成 Role、Permission、Workflow、Policy 和 SoD 取证；
- 首个 MR Draft 的 controller、读回、idempotency、失败分类、响应丢失和 reconciliation 方案已获明确批准；
- 真实 ERP 测试仍只写 Synora 自有治理记录或明确受控的 Draft fixture，绝不修改上游核心、绕过权限或直写 ERP 数据库；
- Harness、开发日志和 README 的阶段口径与实际实现一致。

## 7. 风险与未决项

- 当前没有解决 `approval-workflow-mapping`，因此本 ADR 不能作为 Phase 6 写入授权。
- Runtime token 只保护本地 sidecar 调用，不替代 Frappe 登录、ERP 权限或审批；生产部署还需要受管网络、密钥轮换和调用额度设计。
- Provider 的 reasoning token、usage 缺失和服务商忽略请求预算只能通过 fail-closed 降低风险，不能追回已产生的费用；账户级额度、频率和并发门禁已在 §5.1 固定为 Phase 6 写入启用前的设计/取证门槛，当前未实现。
- Phase 5 按纠偏路线重新进行 workflow-engine 对照；本 ADR 不预埋 LangGraph/Temporal 依赖。

## 8. 结论

原 P4.1 的只读取证和治理设计保留为历史基线；Phase 4 尚未启动，也没有任何 ERP 写入实现。按纠偏路线，Phase 5 先完成持久工作流对照，Phase 6 再批准治理实现范围并完成 `approval-workflow-mapping`。
