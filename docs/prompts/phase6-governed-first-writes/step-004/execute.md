# Execute — Step 004：交付受控 MR Draft 闭环

## 单一任务

在 Frappe 权威边界内，将一个已批准且重检通过的 `CREATE_MR_DRAFT` action 原子地预留、通过标准 Material Request controller 创建 Draft、read-back 关键字段并生成 ExecutionReceipt；无权限、未审批、过期、漂移或重复请求不得创建第二份单据。

## 先读

- Steps 001–003 的实现、测试和独立 `PASS`。
- `docs/PRD.md#56-f-006-mr-draft--po-draft-受控执行`、`#57-f-007-receipt幂等与对账`。
- `docs/SPEC.md#9-tool-gateway-specification`、`#10-policy-and-approval-evaluation-order`、`#111-idempotency`。
- `docs/ACCEPTANCE.md#First Governed-Write Acceptance`。
- 固定 ERPNext `material_request.json`/`material_request.py` 与官方 MR tests；先用固定上游证据确认真实必填字段、权限和 Draft 行为。
- 现有 Gateway registry/security/audit、Run service/state machine、Phase 1 seed/P2P scripts。
- 编码前 `ponytail` full。

## 当前事实

- `CONFIRMED`：目标是 Material Request `docstatus=0`，不是 Submit。
- `CONFIRMED`：Runtime/模型只能形成 proposal，不持有 writer；最终动作由登录用户在 Frappe 侧触发。
- `CONFIRMED`：reservation 必须先成为可恢复事实；真正的 MR mutation、read-back、ledger result 和 Receipt 必须在后续受控事务中共同收敛。HTTP 200 不是业务成功证据。

## 改动边界

- 允许：Frappe-side MR typed payload/executor；专用 `DRAFT_WRITE` registry/service/API；idempotency reservation/result fields；MR read-back verifier；audit/Receipt/Run transition；isolated P6 seed/E2E/fault-injection tests；Phase 6 日志。
- 禁止：Runtime ERP credential 或写 endpoint；generic DocType write/REST/SQL；PO/Submit/Receipt/Invoice/Payment；`ignore_permissions=True` 业务写；直接 DB insert MR；修改 ERPNext controller；让模型/客户端提交 actor/policy outcome/verified fields；批量 action；自动 retry uncertain write；宽泛 site reset/cleanup。

## 执行

1. Context Receipt 指定测试 company/user/action/idempotency key、预期 MR count 变化 `+1`、事务和清理边界。
2. 从固定上游源码/官方测试/真实 console 取证 MR Draft 最小合法 payload，固化 typed schema，例如 purchase request type、company、schedule date、items(item_code, qty, uom/stock_uom, warehouse, schedule_date)；以实际固定版本为准，不猜字段。
3. 只从已批准 ProposedAction 构造 controller payload；执行 API 请求只接收 `action_id`、`expected_proposal_digest`、`idempotency_key`、`correlation_id` 等标识，不接收可替换业务 payload、actor、approval outcome 或 target name。
4. 在同一受控 Frappe execution request 中使用两个明确事务阶段，不能把网络响应当事务边界：
   - 锁定 Run/Action/Approval/idempotency reservation；
   - 调 Step 003 pre-execute recheck；
   - T1：以 action type + company/scope + payload digest 绑定 idempotency key，原子持久化 `STARTED` reservation，并将 Run/Action 转入执行态；T1 成功后即使 worker 消失也留下可对账事实；
   - T2：用标准 `frappe.get_doc(typed_payload).insert()` 或固定版本等价 controller 路径，以当前 session 用户权限执行，不使用 `ignore_permissions`；
   - T2 内读取创建后的 MR，断言 `docstatus=0`、company/request type/items/qty/UOM/warehouse/date 等关键字段与批准 payload 一致；
   - T2 内共同保存 target DocType/name、verified fields、ERP response category、success Receipt、reservation result，Action→EXECUTED、Run→SUCCEEDED；业务写与 success facts 要么共同提交，要么共同回滚；
   - T2 的明确异常回滚业务写，随后用独立受控收尾事务把 reservation/Run 标成明确失败；无法证明回滚或最终结果时保留 `STARTED` 并转 `RECONCILIATION_REQUIRED`，绝不再次写。
5. 同 digest replay：在当前身份/权限重检后返回现有 verified Receipt/MR，不再调用 controller。异 digest 或 action/scope 不同：409 conflict，零写。
6. 记录 audit correlation：run→action→approval→idempotency→MR→receipt；错误只返回稳定分类/correlation，真实 traceback 进受控运维日志并脱敏。
7. 建立 Phase 6 专用幂等测试数据。每个真实测试先记录目标 DocType count/business key，结束后验证精确单据；清理只按明确 Phase 6 标识和已核验 name，禁止全表删除或 site reset。
8. 覆盖正常、permission denied、未审批、expired、digest mismatch、state drift、Item disabled、warehouse/company mismatch、duplicate open MR、ERP ValidationError、same digest replay、different digest conflict、并发双执行。
9. 真实 Frappe HTTP E2E 使用登录态 Buyer 完成 proposal→confirmation→execute→read-back/receipt；Viewer/跨公司路径 403/typed deny；断言最终 ERP 状态而非响应文案。
10. 运行 targeted、受影响 wider checks、`ponytail-review` 和独立 Test/Review；修复后更新日志并提交。

## 问题发现与修复

- MR 已创建但 success Receipt 缺失：先核对同事务是否提交；绝不重试写，转 Step 005 reconciliation 设计并保留 reservation/action/ERP 状态。
- controller 自动填充字段导致 read-back 差异：区分允许的 ERP derived fields 与必须匹配的 approved critical fields；差异规则需 typed、可测试，不能全量宽松比较。
- permission test 通过但实际 insert 403：检查当前 session、DocPerm/User Permission、company/warehouse link 权限；不 `ignore_permissions`。
- 同 key 仍产生两单：检查 reservation unique/CAS 与事务锁；在修复前关闭执行 endpoint，盘点并精确标记测试重复单。
- 业务异常被统一成 success/500：建立 INPUT/PERMISSION/POLICY/STALE/VALIDATION/CONFLICT/UNCERTAIN/INTERNAL 分类；明确失败不得留下 success Receipt。
- Runtime/native tool calling 能看见 write tool：registry/risk allowlist 分层错误，立即下线并增加架构测试。

## 验证与证据

- Unit：MR payload、read-back comparison、error mapping、idempotency tuple。
- Frappe integration：真实 controller/permission/validation/transaction/Receipt/state/audit。
- HTTP/E2E：Buyer 正常与 replay；Viewer/cross-company/expired/drift/disabled item 拒绝。
- Concurrency：同 key 双请求最多一次 controller write；结果均指向同一 verified MR 或一个冲突。
- Architecture/security：Runtime 无 writer、generic write 不可达、日志脱敏。
- 提供 MR names、count-before/after、关键 read-back、Receipt ids、命令/退出码和精确清理证据。

## 提交纪律

建议：`feat(phase6): create material request drafts safely`。一个 MR 用户结果提交；Step 005 故障恢复不提前混入。

## 最终报告

说明解决的业务问题、用户看到的 MR Draft/Receipt、数据流、三个关键文件、手工验证、实际命令/退出码和仍未覆盖的 response-loss/reconciliation 风险。不得声称 PO 或 Phase 6 已完成。
