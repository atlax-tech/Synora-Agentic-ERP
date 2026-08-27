# Execute — Step 005：实现幂等、响应丢失与对账

## 单一任务

把 Step 004 的 MR Draft writer 扩展为可证明的幂等与故障恢复闭环：same-digest replay、different-digest conflict、durable STARTED reservation、响应丢失恢复、`RECONCILIATION_REQUIRED`、reconciled success/failure 和 manual intervention；任何不确定结果都禁止盲重试。

## 先读

- Step 004 writer/transaction/Receipt 实现与真实 ERP 证据。
- `docs/PRD.md#57-f-007-receipt幂等与对账`、SC-004。
- `docs/SPEC.md#11-idempotency-and-reconciliation`、`#14-observability-and-audit`。
- `docs/PLAN.md#15-phase-6--受治理的第一批-erp-行动` P6.4。
- `synora_agentic_erp/agent/invocation.py` 的 Phase 5 read-only invocation ledger；只复用已证明适用的 canonical/CAS 思路，不把只读 checkpoint 记录当 write ledger。
- 编码前 `ponytail` full。

## 当前事实

- `CONFIRMED`：HTTP/client timeout 只说明未收到结果，不能证明 Frappe 未写。
- `CONFIRMED`：T1 durable reservation 与 T2 mutation+Receipt 是不同安全阶段；T1 后无 T2 终态必须对账。
- `CONFIRMED`：对账可以读 ERP 和更新 Synora 状态，不能再次调用 MR writer。

## 改动边界

- 允许：write reservation state/lease/attempt metadata；reconciliation service/read-only query/API；Receipt reconciliation states/links；fault injection seam limited to tests; MR writer transaction hardening；status/UI data contract（展示留 Step 006）；unit/Frappe/real HTTP/process tests；Phase 6 日志。
- 禁止：自动 retry write；用新 idempotency key 绕过旧 STARTED；把“查不到单据”立即等同失败；generic reconcile-any-DocType；客户端声明成功/失败；清理不确定业务单据；Runtime writer；PO/write scope扩张。

## 执行

1. Context Receipt 列出 T1/T2/fault points、每种期望 reservation/Run/Receipt/ERP 状态和零盲重试断言。
2. 明确定义 reservation 状态和唯一性，至少能表达 `STARTED`、verified `SUCCEEDED`、definite `FAILED`、`RECONCILIATION_REQUIRED`、reconciled success/failure、manual intervention；任何名称变化须保持 SPEC 语义和迁移兼容。
3. idempotency tuple 固定绑定 action type、run/action、target scope 和 approved payload digest；数据库 unique/CAS 防并发。调用方不能为相同 action 任意换 key。
4. replay 规则：
   - same key + same tuple/digest + verified success：当前身份/权限重检后返回原 Receipt/read-back；
   - same key + different digest/action/scope：409 conflict，零 writer call；
   - STARTED/uncertain：返回 reconciliation status，零 writer call；
   - definite failed：是否允许重新提议由新 action 决定，不能复活旧 approval。
5. 实现 read-only reconciliation：锁定 reservation/action；读取 Receipt/target name；按 expected DocType、idempotency evidence、业务 key 和批准 critical fields 查询授权范围内 ERP；记录查询快照和候选数量；分类：
   - verified Receipt/单一完全匹配 Draft → reconciled success；
   - 事务明确回滚、lease 结束且无候选、错误证据完整 → reconciled failure；
   - 多候选、字段不一致、请求仍可能执行、权限不足或证据缺失 → manual intervention。
6. lease/时间：只使用服务端 UTC；请求可能仍运行时不提前判失败。超出 lease 也只允许读和分类，不允许写 retry。
7. 故障注入矩阵至少包含：
   - T1 前失败：无 reservation、无 MR；
   - T1 后/T2 前 worker failure：STARTED→reconciliation，无 MR；
   - controller 中异常/T2 rollback：无 MR，明确或对账失败；
   - T2 commit 后/HTTP response 前断连：MR+Receipt 已存在，replay/status 找到同一结果；
   - 客户端 timeout 时 T2 仍运行：后续只 poll/reconcile，不 second write；
   - concurrent execute/reconcile：最多一份 MR，状态单调收敛。
8. 真实 HTTP/process 证据优先于只 mock exception：使用可控 test hook 或传输层丢响应，不在 production 入口暴露 fault 参数；至少做一次真实请求响应丢失/连接中断演练并核对 ERP。
9. Audit 记录 attempt/reconciliation/candidate/decision/correlation，但不记录 secret/capability/full payload；跨用户/公司不可查询对账详情。
10. UI/API contract 返回 typed `result_status`、`can_retry=false`、correlation、Receipt 或人工处理提示；不把 5xx/timeout 转成“请重试执行”。
11. targeted fault tests → real integration → independent Test/Review → wider checks；修复后更新开发日志并提交。

## 问题发现与修复

- STARTED reservation 不可见：检查 T1 是否真正独立提交、unique/CAS 和事务连接；修复前关闭 writer。
- response-loss 测试用异常导致整个 T2 回滚，未模拟“commit 后丢响应”：改在传输/客户端层丢弃已提交响应，分别保留 rollback 与 post-commit 两类用例。
- reconciliation 查到多个候选：不得按“最新一条”猜；进入 manual intervention 并保留候选摘要。
- 查不到候选就自动重试：删除 retry；只有新分析→新 action→新 approval 可发起新的逻辑动作。
- replay 未重检当前权限：补上 server-side current permission；无权时不泄露旧 Receipt/MR。
- 旧 lease 抢占仍运行请求：使用 owner token/revision/CAS 和明确 safe point；无法证明原请求终止时保持人工介入。

## 验证与证据

- Unit：tuple/digest、reservation state/CAS/lease、classification、replay/conflict。
- Frappe integration：T1/T2 transaction、rollback、Receipt、Run state、read-only candidate query、permissions。
- Real HTTP/process：post-commit response loss、timeout/in-flight、restart/crash safe point、concurrent execute/reconcile。
- Assertions：writer call count、MR count/names、reservation/Receipt/Run states、`can_retry=false`、audit correlation。
- 有限故障/安全用例 100% 通过；提供每个 fault point 原始证据。

## 提交纪律

建议：`feat(phase6): reconcile uncertain governed writes`。只提交恢复闭环，不提前添加 PO/UI。

## 最终报告

用大白话说明“没收到响应为什么不能重试”、系统如何查最终 ERP 事实；列出真实 fault injection、单据数量、命令/退出码、剩余 manual intervention 限制和 Step 006 前置。
