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
- 固定 `dev.localhost` 的 Step 001 只读取证显示 MR/PO 没有 active Workflow；这不是企业通用事实。
- ADR-0007 将固定开发 site 的 MR/PO Draft 映射为当前 session 且等于 Run initiator 的显式确认；有效 DocPerm/User Permission、scope、snapshot、expiry、digest 和 controller 依赖必须每次重检。
- 任何企业 active Workflow、多级审批、金额阈值或冲突配置都优先于该确认；未映射/不可解释时 fail closed，并要求新 mapping 版本。

来源: docs/erp-baselines/phase1-permission-workflow-baseline.md
