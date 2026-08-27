# Review — Step 003：审查 Policy、审批与执行前重检

## 审查输入

- 原始任务：P6.2 固定评估顺序、明确审批和 pre-execute recheck；不写 ERP。
- 约束：`docs/SPEC.md#10-policy-and-approval-evaluation-order`、Step 001 mapping。
- 预期 diff：policy/approval/precheck/API/tests/log；无 writer。
- 证据：最终 diff、独立测试、permission/Workflow/TOCTOU/并发结果、单据计数。

## 审查维度

- actor/initiator/approver 是否全由 server/session/Run 解析。
- mapping 是否机械执行且 stricter Workflow wins；缺失/冲突是否 fail closed。
- quantity/money/UOM/duplicate 等是否 deterministic，缺数据是否 UNKNOWN 而非 0。
- proposal 与 execution 是否分别重检；approval 是否绑定不可变 digest。
- state drift/permission revoke/concurrency 是否不会偷偷刷新批准或继续执行。
- audit/error 是否脱敏并权限过滤。
- 是否提前出现 MR/PO write、generic endpoint 或过度抽象。

## 判定

只返回 `PASS`、`CHANGES_REQUIRED` 或 `BLOCKED`，逐项给文件/测试/严重度。任何身份可伪造、Workflow 可绕过、precheck 不重查或实际写入均为 blocking。
