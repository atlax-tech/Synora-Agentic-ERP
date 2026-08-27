# Review — Step 001：审查 approval-workflow-mapping 门禁

## 审查输入

- 原始任务：Phase 6 P6.1 的固定 Workflow/Role/Permission 取证与 mapping 决策，不开放写工具。
- 约束：`docs/PLAN.md#15-phase-6--受治理的第一批-erp-行动`、`.harness/unresolved.json#approval-workflow-mapping`。
- 预期边界：只读 probe、决策包/ADR、必要基线/Harness 同步和 Phase 6 日志；没有业务写代码或 site 配置变化。
- 证据：最终 diff、独立 Test 输出、用户批准记录、上游 SHA/clean、probe artifacts、Harness checks。

## 审查维度

- mapping 是否区分源码事实、当前 site 观察、产品策略和企业未决项。
- 用户批准是否具体到 action、actor/effective permission、Workflow 优先级、确认/审批 class、expiry/recheck 和冲突行为。
- 是否错误硬编码角色、复制 ERP permission/Workflow，或把“无 Workflow”推广成通用事实。
- probe 是否真的只读、稳定、脱敏，不修改上游/site，不需要过宽权限。
- Harness resolution 是否有证据且没有手工破坏 managed state。
- 是否在门禁解除前新增任何写能力、按钮或“已实现”声明。
- 严重度：blocking/high/medium/low；任何未经批准 mapping 或潜在写能力均为 blocking。

## 判定

返回且只返回一个结论：`PASS`、`CHANGES_REQUIRED` 或 `BLOCKED`。每个问题引用文件和运行证据；不要修改实现，也不要用 Execute 的结论代替检查。
