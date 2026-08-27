# Test — Step 001：独立验证 approval-workflow-mapping

独立验证取证和用户决策是否足以安全解除写入前门禁。不要信任 Execute 自述，不要修决策包、脚本或 Harness。

## 需求来源

- `.harness/unresolved.json#approval-workflow-mapping` — 未经证据和用户决定不得解决。
- `docs/PRD.md#55-f-005-policy--rbac--approval` — Draft 确认基线和更严格 Workflow。
- `docs/SPEC.md#10-policy-and-approval-evaluation-order`、`#18-unresolved-decisions` — 缺失/冲突 fail closed。
- `docs/PLAN.md#8-必须停止并交给用户的情况` — 权限/Workflow 决策必须停下。

## 行为矩阵

- 正常：固定 SHA 与 site 一致；probe 稳定列出 MR/PO Workflow、DocPerm、测试用户 effective permission；用户批准的 mapping 与证据一致。
- 错误：没有用户批准、只有 Phase 1 旧观察、证据缺字段、Workflow 条件未解析、mapping 把默认 DocPerm 当审批授权；必须判 `FAIL`。
- 边界：runtime 有自定义 DocPerm/User Permission、active Workflow、条件表达式、多个角色/permlevel、无权测试用户、源 SHA 漂移；必须保留差异或 fail closed。

## 测试范围

- Unit：probe 输出 schema、稳定排序、脱敏、未知字段拒绝、无写 API 静态检查。
- Integration：独立运行固定 SHA/clean 断言和 read-only probe；将结果与实际 `frappe.get_all`/`has_permission` 抽样对照。
- Architecture：确认 diff 没有 MR/PO insert/save/submit、写 endpoint、registry risk 放宽、上游或 site 配置改动。
- Harness：若 unresolved 状态变化，验证 resolution 引用真实 ADR/决策包，manifest/drift/reference/structure 全部通过。
- Manual：检查用户批准的是一个精确选项而非“继续”“可以”等模糊授权；检查后续遇到更严格 Workflow 的行为明确为 fail closed。

## 失败证据

保留 probe 原始输出、SHA、上游 status、有效用户权限抽样、diff、命令与退出码。发现 site 被写时立即停止并报告具体对象，不自行清理。

## 判定

仅当证据可复跑、用户批准明确、mapping 能机械执行且当前仍没有写能力时返回 `PASS`；否则返回 `FAIL` 或 `BLOCKED`。
