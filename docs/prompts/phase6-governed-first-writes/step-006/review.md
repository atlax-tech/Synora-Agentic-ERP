# Review — Step 006：审查 PO Draft 与 UI

## 审查输入

- 原始任务：P6.5 PO Draft + proposal/approval/Receipt/reconciliation UI。
- 约束：PRD F-004–F-008、DESIGN high-risk/accessibility、SPEC 9–11。
- 预期 diff：专用 PO handler/preview/recheck、UI/API serializers、tests/log；无 Submit。
- 证据：最终 diff、独立 real ERP/HTTP/browser 输出、PO/Receipt artifacts、ponytail-review。

## 审查维度

- supplier/price/amount/currency/UOM/schedule 是否确定性且在批准 digest 内，执行前是否重检。
- PO writer 是否与 MR 一样受 permission/Workflow/idempotency/read-back/reconciliation 保护。
- 是否错误假设 Draft MR 可转换 PO，或绕过官方 controller。
- UI 是否显示真实后果/证据/风险/snapshot/expiry，且 server facts 决定状态。
- 权限过滤、escaping、重复提交、键盘/focus/aria、错误和对账文案是否安全。
- PO Submit/后续 P2P/generic/Runtime writer 是否全部不可达。
- 复用是否只抽取已被 MR+PO 证明的安全 primitive，无推测性框架。

## 判定

只返回 `PASS`、`CHANGES_REQUIRED` 或 `BLOCKED`。金额/供应商漂移仍执行、误导成功、越权/XSS、重复 PO 或 Submit 可达均为 blocking。
