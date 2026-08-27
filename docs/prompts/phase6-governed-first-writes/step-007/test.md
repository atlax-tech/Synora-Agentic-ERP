# Test — Step 007：独立 Phase 6 出口测试

独立复跑关键证据，不信任阶段报告或 Execute 汇总。不要修代码、补日志或改 Harness。

## 需求来源

- PLAN Phase 6 出口、§4.6/4.7。
- PRD 第一受控写入验收、SPEC Phase 6 gate、ACCEPTANCE governed-write/release。

## 行为矩阵

- 正常：MR 与 PO 各一条真实 goal→proposal→confirmation→write→read-back/Receipt，全关联且 docstatus=0。
- 错误：invalid/unauthorized/unapproved/stale/drift/different digest/disabled object/validation 全部零写且有 typed/audit evidence。
- 恢复：same digest replay、post-commit response loss、T1/T2 crash、concurrent requests、manual intervention；无 blind retry，无重复单。
- UI：权限、后果、证据、snapshot/expiry、拒绝/修改/失败/对账、keyboard/focus/aria/XSS。

## 测试范围

- 全量 format/lint/type/unit/app-test。
- Phase 6 real HTTP/E2E/fault/process and browser acceptance。
- 固定上游 SHA/clean、Runtime boundary、write allowlist、Submit/后续写不可达。
- Harness manifest/drift/reference/structure 在最终状态通过。
- 证据抽查：随机选 MR/PO/失败/对账 case，从 ERP 最终文档反向追到 Receipt/action/run/audit。

## 失败证据

保留所有命令/exit code、ERP 单据盘点、日志/截图/trace、diff/HEAD。不得清理不确定数据或修改任何结果。

## 判定

只有所有有限安全场景 100%、真实链路可复跑、上游干净、无 P0/P1、Harness 健康时 `PASS`；否则 `FAIL/BLOCKED`。
