# ERP 权限与审批基线 (curated 摘要)
source_type: baseline
revision: v1
permission_scope: internal
---

## 默认 DocPerm (候选 site 只读查询)
- Purchase User: MR/PO/PR 全权 (含 submit/cancel), PI 只读
- Purchase Manager: MR 全权, PO 全权
- Stock User: MR/PR 全权, PO 只读
- Accounts User: PI 全权 (delete=0), PR 只读
- Auditor: PI 只读

## 审批基线 (Synora 策略, 更严格者优先)
- MR Draft / PO Draft: 发起人显式确认即可执行
- PO Submit / Purchase Receipt / Purchase Invoice / Payment 写操作: 必须由不同于发起人的有权审批人授权
- 规则缺失、冲突或无法验证时 fail closed

## Workflow
- 候选 site 未启用任何 Workflow (frappe.get_all("Workflow") 为空)
- 企业 Workflow / 多级审批 / 金额阈值属未决项, 最迟 Phase 4 启用写入前由用户决定

来源: docs/erp-baselines/phase1-permission-workflow-baseline.md
