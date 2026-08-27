# Review — Step 002：审查治理记录设计与实现

## 审查输入

- 原始任务：P6.1 governance records、digest、状态转换，不执行 ERP write。
- 约束：`docs/SPEC.md#7-canonical-contract-concepts`、`#82-proposed-action`、Step 001 mapping。
- 预期 diff：governance contracts/services/DocTypes/tests/日志；无真实 MR/PO 写入口。
- 证据：最终 diff、独立测试、migrate/permission/concurrency 输出、ponytail-review。

## 审查维度

- typed payload 是否按 action 区分，unknown 字段/版本是否 fail closed。
- digest 是否保护真正被审内容，canonicalization 是否跨运行稳定。
- Action 状态是否只有 deterministic service 转换并抵抗重复/并发。
- actor 是否来自 Frappe session/Run，而非 Runtime/客户端字段。
- DocType permission、immutability、unique/index 与事务是否足以支撑后续写门禁。
- success Receipt 是否可能在没有 read-back 证据时伪造。
- 是否引入 generic repository/event bus/审批框架等无实证复杂度。
- 是否误开放任何 ERP 写工具或声称能力已完成。

## 判定

返回且只返回 `PASS`、`CHANGES_REQUIRED` 或 `BLOCKED`；每个改动要求引用具体文件/测试/风险严重度。Review 不修代码。
