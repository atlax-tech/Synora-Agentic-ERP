# Test — Step 004：独立验证 MR Draft 受控写入

以真实 ERP 最终状态为准，独立验证一次且仅一次的 MR Draft 创建。不要修实现或清理失败证据。

## 需求来源

- `docs/PRD.md#56-f-006-mr-draft--po-draft-受控执行`。
- `docs/SPEC.md#111-idempotency`。
- `docs/ACCEPTANCE.md#First Governed-Write Acceptance`。

## 行为矩阵

- 正常：有效 proposal + 当前有权发起人确认 + precheck 通过，只创建一个 `docstatus=0` MR；read-back critical fields 与批准 payload 一致；Receipt/audit 全关联。
- 错误：无权限/跨公司/未审批/过期/digest mismatch/state drift/disabled item/invalid UOM/warehouse/duplicate risk/ERP validation 明确拒绝，MR count 不变，无 success Receipt。
- 边界：same key same digest replay、same key different digest、并发双请求、请求重复点击、事务中 read-back mismatch、异常序列化和日志敏感字段。

## 测试范围

- Unit：payload/digest/idempotency/read-back/error categories。
- Integration：固定 ERP controller、当前 session permission、事务 rollback、unique/CAS、Run/Action/Receipt/Audit。
- Real HTTP：登录态 Buyer 与 Viewer；断言 MR name/docstatus/items/company 和文档数量。
- Concurrency：两个真实请求的 controller write count 与最终 MR count。
- Architecture：Runtime/native Agent tool specs 没有 writer；generic DocType/API/SQL 不可达；PO/Submit 仍禁用。
- Manual：在 ERP Desk 打开创建的 MR Draft，与 Proposal/Receipt 逐字段核对；确认没有 Submit。

## 失败证据

记录 run/action/approval/idempotency/correlation/receipt ids、MR count-before/after、所有可能 target names、DB transaction outcome、命令/退出码。结果不确定时禁止重跑写测试。

## 判定

有限安全集 100% 通过、真实 ERP 只产生预期一个 Draft、replay 不重复且所有失败零写时为 `PASS`；否则 `FAIL/BLOCKED`。
