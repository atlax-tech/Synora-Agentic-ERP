# Execute — Step 001：解决 approval-workflow-mapping 门禁

## 单一任务

在固定 Frappe/ERPNext 版本和隔离开发 site 上重新取证 MR/PO Draft 的 Role、DocPerm、User Permission 与 Workflow 实况，形成一个不含写能力的 Phase 6 审批映射决策包；只在用户明确批准具体 mapping 后把未决项标为已解决。本步骤不得实现、注册或暴露 ERP 写工具。

## 先读

- `docs/PLAN.md#7-未决项路由`、`#8-必须停止并交给用户的情况`、`#15-phase-6--受治理的第一批-erp-行动` — 顺序、停止条件和 P6.1 来源。
- `.harness/unresolved.json#approval-workflow-mapping` — 当前未决问题，不能由 Agent 猜测。
- `docs/PRD.md#55-f-005-policy--rbac--approval` — Draft 发起人显式确认与更严格 Workflow 优先。
- `docs/ARCHITECTURE.md#Approval and Workflow Authority` — Frappe/ERPNext 是身份、权限和 Workflow 权威。
- `docs/SPEC.md#10-policy-and-approval-evaluation-order` — 评估顺序与缺失/冲突 fail closed。
- `docs/erp-baselines/phase1-permission-workflow-baseline.md`、`docs/source-maps/phase1-p2p-source-map.md` — 固定版本证据与现有无 Workflow 观察。
- `docs/decisions/ADR-0002-frozen-baseline-pair.md` — 上游 SHA。
- `docs/development-log/README.md` — commit 前 Phase 6 日志规则。

## 当前事实

- `CONFIRMED`：Frappe `6a329d...bf99`、ERPNext `11e0ba...325` 为固定基线。
- `CONFIRMED`：Phase 1 观察到当前 site 没有 Workflow；`Purchase User` 对 MR/PO 有创建能力，但默认 DocPerm 不是 Synora 审批授权。
- `CONFIRMED`：MR Draft/PO Draft 测试基线允许发起人显式确认；更严格 ERP Workflow 必须优先。
- `UNRESOLVED`：企业是否启用 Workflow、是否需要多级审批/金额阈值、是否按角色名还是有效 permission 映射，尚未获用户批准。

## 改动边界

- 允许：新增只读 Phase 6 取证脚本/测试于 `env/dev/p6/`；新增一个 Phase 6 mapping 决策包或 ADR；必要时精确更新权限基线/source map；用户批准后通过 `harness-update` 同步 `.harness/unresolved.json` 和指纹；commit 前新增/更新唯一的 Phase 6 开发日志。
- 禁止：修改 ERPNext/Frappe 上游；修改 site Workflow/Role/DocPerm/User Permission；创建 MR/PO；新增写 endpoint、写 registry/tool、Approval/Receipt 执行代码；手工编辑 `.harness/`；修改 `.env*`、README 或学习笔记；把“当前无 Workflow”写成企业通用事实。

## 执行

1. 输出 Context Receipt，列出本步只读性质、真实 site、固定 SHA、预期查询和零业务写入断言。
2. 复核工作区、上游 SHA 与上游 clean 状态；不一致即停止。
3. 使用 read-only bench 查询并保存结构化证据：
   - `Workflow` 中 MR/PO 的 active 状态、document type、workflow state field、states、transitions、allowed role、condition；
   - MR/PO 的标准/自定义 DocPerm，至少含 read/create/write/submit/cancel/amend、permlevel；
   - Phase 6 测试用户的 roles、User Permission、company/warehouse scope；
   - `frappe.has_permission` 对 MR/PO 的 read/create 结果；
   - 任何 Server Script、custom permission hook 或配置性限制；若无法完整枚举则标 `UNRESOLVED`。
4. 只读取证脚本必须固定 schema、稳定排序、脱敏用户/凭证、无 `insert/save/submit/delete/set_value/db.sql` 写路径，并能重复运行得到同一语义结果。
5. 将证据分成 `[S]` 固定源码/官方测试、`[R]` 当前运行观察、`[P]` 已批准产品策略、`[U]` 用户未决，禁止混写。
6. 形成三种真实选项及影响，不把偏好包装成事实：
   - A（建议候选）：开发基线保持无 ERP Workflow；MR/PO Draft 由 Run 发起人显式确认；每次按当前 session、Run initiator、company/warehouse scope、effective DocPerm/User Permission 重检并记录匹配角色；未来发现更严格 active Workflow 时自动 fail closed，待映射后才能执行。
   - B：用户授权在隔离 site 配置明确 MR/PO Workflow，再以其 states/transitions/roles 为更严格规则；需单独的配置、回滚和集成证据。
   - C：将 Draft 提升为独立审批人或多级/金额阈值；这会改变当前 PRD/SPEC 基线，必须先走产品需求批准，不能在本步骤直接实现。
7. 向用户只提出一个准确决策问题，附推荐项、证据、风险和可回滚性；用户未批准时将本步骤记为 `BLOCKED_BEFORE_WRITE` 并停止。
8. 用户批准后，以 ADR/决策包固化：适用环境、action、actor、required effective permission、Workflow 优先级、confirmation/approval class、expiry/recheck、冲突策略、版本/hash、测试矩阵和撤销方式。
9. 必要时调用 `harness-update`，把 `approval-workflow-mapping` 从 `UNRESOLVED` 改为带证据的 `RESOLVED`；先给文件级 proposal，不手改 `.harness`。
10. 运行 targeted docs/script tests、Harness drift/reference 检查和 `git diff --check`；按开发日志规范记录真实证据后，用一个 docs/test Conventional Commit 提交。

## 问题发现与修复

- 若运行证据仍是无 Workflow：只能证明当前固定 site；决策包必须保留 enterprise override，不能删去 Workflow 检查。
- 若发现 active Workflow：完整取证每个 MR/PO state/transition/role/condition；条件无法确定性解释时 fail closed，并把准确条件交用户决定。
- 若 DocPerm JSON 与 runtime 查询不同：优先 runtime 当前有效配置，但记录差异来源；检查 Custom DocPerm/User Permission，不擅自修 site。
- 若脚本触发写入或依赖管理权限才能窥见不应访问的数据：删除该路径，改为最小权限只读查询，并加架构测试。
- 若用户选择 C：停止 Phase 6 实现，先走 PRD/SPEC 产品变更流程；不能以 ADR 替代需求批准。

## 验证与证据

- 运行固定 SHA/clean 检查、只读 mapping probe、probe 单测、`git diff --check`。
- 若 Harness 发生更新，运行 manifest、drift、reference 和 structure checks。
- 提供：取证输出摘要、原始 artifact 路径、用户批准原文的精确结论（不复制无关对话）、文件 diff、命令与退出码、未运行项、后续代码边界。

## 提交纪律

本步骤只允许一个 mapping 结果提交；用户批准前不提交“已解决”状态。建议提交：`docs(phase6): resolve approval workflow mapping`。不推送。

## 最终报告

报告已确认 mapping、仍未决内容、是否允许进入 Step 002、真实命令/退出码、上游/site 是否被改变、风险和下一步恢复点。不得返回阶段 `PASS`。
