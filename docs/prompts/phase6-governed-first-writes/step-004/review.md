# Review — Step 004：审查 MR Draft 受控执行

## 审查输入

- 原始任务：P6.3 MR Draft proposal→confirmation→reservation→controller→read-back→Receipt。
- 约束：PRD F-006/F-007、SPEC 9–11、Step 001 mapping。
- 预期 diff：专用 MR writer、idempotency/receipt/audit/tests/log；没有其他 writes。
- 证据：最终 diff、独立真实 ERP/HTTP/concurrency 测试、MR/Receipt artifacts、ponytail-review。

## 审查维度

- writer 是否仅在 Frappe current-user 边界，是否使用标准 controller/permission/validation。
- 执行请求是否只能引用已批准 immutable action，不能替换 payload/actor/approval。
- write 前 reservation、recheck 和 state transition 是否同一安全事务；异常是否回滚或明确进入 uncertain。
- read-back 是否核对批准的 critical fields，Receipt 是否来自 ERP 事实。
- same/different digest、并发与重复点击是否最多一单。
- Runtime/model/generic write/PO/Submit 是否仍不可达。
- 错误、trace、audit 是否脱敏且权限过滤。
- 实现是否足够小，是否出现无需求的通用 writer/command bus。

## 判定

只返回 `PASS`、`CHANGES_REQUIRED` 或 `BLOCKED`。任何未经授权、绕过 controller/permission、重复写、伪造 Receipt、Runtime writer 或不确定结果盲重试均为 blocking。
