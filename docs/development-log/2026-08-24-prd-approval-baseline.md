# 2026-08-24 PRD Approval Baseline

## 完成内容

- 使用 `prd-writer` 的增量融合模式，将用户确认的审批策略写入 `docs/PRD.md`。
- 测试基线允许 MR Draft 和 PO Draft 由发起人显式确认；PO Submit、Receipt、Invoice、Payment 强制独立审批人。
- 明确继承 ERPNext Workflow，企业规则更严格时采用更严格规则；审批规则无法验证时 fail closed。
- 保留具体 Role、Permission Matrix、多级审批和金额阈值为后续 ERP baseline 的待确认项。

## 验证结果

- 审批规则同时更新了 F-005 功能描述、边界条件和待确认问题。
- 没有降低 PO Submit 及后续完整 P2P 写操作的职责分离要求。
- 没有虚构 ERPNext Role 名称、金额阈值或多级审批配置。

## 人工验收步骤

1. 打开 `docs/PRD.md` 的 F-005，确认 Draft 与 Submit/后续操作采用不同审批基线。
2. 确认实际企业 Workflow 更严格时以更严格规则为准。
3. 检查第 12 节，确认具体 ERP 权限映射仍需基线验证。
