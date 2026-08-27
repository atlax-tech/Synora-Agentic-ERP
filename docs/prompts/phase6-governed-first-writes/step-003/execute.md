# Execute — Step 003：实现 Policy、审批与执行前重检门禁

## 单一任务

把 ProposedAction 从严格解析推进到可审查/批准，并实现 schema → identity → permission → deterministic checks → Workflow/policy → snapshot/expiry/digest 的固定评估顺序和执行前全量重检；本步骤仍不调用 MR/PO controller。

## 先读

- Step 001 mapping ADR/决策包与 Step 002 governance contract/state 实现及独立证据。
- `docs/SPEC.md#10-policy-and-approval-evaluation-order`、`#5-identity-authorization-and-trust`。
- `docs/PRD.md#55-f-005-policy--rbac--approval`、`#56-f-006-mr-draft--po-draft-受控执行`。
- `docs/ARCHITECTURE.md#Trust Boundaries`、`#Approval and Workflow Authority`。
- `synora_agentic_erp/gateway/security.py`、`agent/service.py`、`api.py` 和 Run visibility/permission tests。
- 编码前读取 `ponytail` full。

## 当前事实

- `CONFIRMED`：Frappe session、server-bound Run 和 current permission 是授权权威；客户端/Runtime 不得提交 actor/approver 作为事实。
- `CONFIRMED`：提议展示前和执行前是两次不同门禁；第二次必须重新读取身份、权限、policy、ERP current state、expiry、digest 和 idempotency。
- `CONFIRMED`：用户确认批准的是 proposal digest，不是自然语言摘要或可变表单。

## 改动边界

- 允许：governance policy/evaluator/approval service；精确的 proposal/decision read/confirm/decline/changes-requested API；snapshot adapter；相邻 DocType 字段/indices；unit/Frappe/HTTP tests；Phase 6 日志。
- 禁止：MR/PO insert/save、write registry、后台自动审批、接受客户端 actor/role/policy outcome、复用 checkpoint 作为审批事实、自动刷新 digest/expiry、审批后原地修改 payload、generic expression evaluator、`ignore_permissions` 绕过、上游/README/学习笔记/Harness 手工修改。

## 执行

1. Context Receipt 明确本步骤只到 `APPROVED`，ERP 单据数应保持 0。
2. 写评估顺序测试，确保任何早期门禁失败都不执行后续更敏感查询/逻辑，并返回稳定 typed category。
3. 实现 proposal evaluation：
   - strict schema/action/payload；
   - 从 session 解析 actor，从 Run 解析 initiator/scope；
   - 当前 User Permission、DocPerm、公司/仓库/目标对象 permission；
   - MR/PO 分别执行数量/UOM/金额、重复采购、Item/Supplier enabled、前置单据和业务一致性 checks；无法确定返回 `UNKNOWN/NEEDS_INPUT`，LLM 不计算；
   - 读取当前 active Workflow 与 Step 001 mapping，采用更严格规则；缺失/冲突/不可验证 fail closed；
   - 生成 snapshot ref、expiry、approval class、PolicyDecision 与 proposal digest，原子进入 `AWAITING_APPROVAL`。
4. 实现审批 API：通过 Frappe login session 取得 decision actor；验证可见性、effective permission、mapping、当前 action/digest/state/expiry；支持 allow、decline、changes requested，重复/并发决定 409 且不覆盖首个有效终态。
5. Draft 发起人确认仅在 Step 001 mapping 明确允许时成立；审批 actor 必须等于当前 session user，不能来自请求体。未来更严格 Workflow 发现时，发起人确认不能降级它。
6. 实现纯 deterministic `pre_execute_recheck(action_id, expected_digest, idempotency_key)`：重新加载并锁定 Run/Action/Approval，逐项重查身份绑定、当前 actor 权限、Workflow/policy、Item/Supplier/MR/PO state、quantity/money/duplicate、expiry、payload/proposal digest 和 idempotency reservation eligibility；返回 typed approved execution context 或具体 fail-closed reason，不执行 write。
7. TOCTOU：snapshot 关键字段或权限/Workflow 变化时将 action 原子转 `EXPIRED` 或保持冲突状态；旧 approval 不能迁移到新 action。重新分析必须产生新 action/digest。
8. 审计关联 run/action/policy/approval/correlation；只记录最小必要输入摘要和 digest，不存 secret、capability 或未经授权业务数据。
9. 增加真实 Frappe HTTP/permission tests：Buyer 正常确认、Viewer 拒绝、跨公司、审批后撤权、物料/供应商停用、已有 MR/PO 变化、过期、digest mismatch、并发 approve/decline、重复点击。
10. 加 architecture test：所有 Step 003 路径结束后 MR/PO 业务单据数不变，Runtime/model/browser 没有 writer。
11. targeted → increment checks → independent Test/Review；修复后更新 Phase 6 日志并小步提交。

## 问题发现与修复

- permission check 在 proposal 时通过、执行前失败：这是正确 fail-closed，不应缓存旧结果；补上 expired/stale UI reason。
- Workflow 条件难以安全解释：不要复制/执行任意表达式到 Runtime；在 Frappe 权威边界使用官方 permission/workflow 行为或判不可验证。
- duplicate check 因数据缺失当作 0：改为 UNKNOWN/NEEDS_INPUT，禁止批准。
- 并发 approve/decline 双成功：将 state/digest/version 放进同一锁/CAS 事务，第二个返回 conflict。
- 审批 API 接受 actor/role：删除字段并从 session 解析；添加伪造请求测试。
- precheck 自动修改 payload 以“适配最新状态”：禁止；Action 过期并重新提议。

## 验证与证据

- Unit：评估顺序、policy result、Workflow stricter-wins、snapshot/expiry/digest、precheck typed outcomes。
- Frappe integration/HTTP：权限、scope、审批状态、并发、撤权、漂移、审计过滤。
- Architecture：零 MR/PO 业务写、Runtime 无 writer、无 generic endpoint。
- Regression：Run state/visibility、Gateway security、Phase 5 resume/cancel/expiry targeted。
- 提供每个失败类别的原始命令/退出码和 ERP 单据 count-before/count-after。

## 提交纪律

建议：`feat(phase6): enforce governed approval gates`。仅提交本步骤的 policy/approval/precheck、测试和一轮开发日志。

## 最终报告

说明用户现在能审查/确认什么、为什么仍不会写 ERP、每个门禁如何失败、实际测试和 Step 004 前置；不得自行返回独立 `PASS`。
