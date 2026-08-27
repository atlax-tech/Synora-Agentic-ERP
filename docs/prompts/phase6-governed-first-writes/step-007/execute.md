# Execute — Step 007：完成 Phase 6 出口门禁

## 单一任务

在 Steps 001–006 全部通过后，以最终代码和真实 ERP/HTTP/browser/fault evidence 完成 Phase 6 全量验证、9 维 Rubric、风险登记、复杂度/Harness 检查和最终独立对抗审查；只有最终 verdict `PASS` 才更新阶段状态并提交出口记录，然后停止，不进入 Phase 7。

## 先读

- `docs/PLAN.md#46-阶段评估-rubric-与风险标准`、`#47-阶段出口自动门禁与导师交付`、`#15-phase-6--受治理的第一批-erp-行动`。
- PRD F-004–F-008、第一受控写入验收；SPEC 7–11/14/16/Phase 6 gate；DESIGN acceptance；ACCEPTANCE governed-write/release。
- Phase 6 唯一开发日志、所有本阶段 commits/diffs、真实 artifacts 和独立步骤 verdicts。
- 固定上游 SHA/clean evidence、Step 001 mapping ADR/decision、所有 P2/P3 open risks。
- 调用前完整读取 `ponytail-audit`、`ponytail-debt`、`harness-check`；Harness 事实需同步时先读取 `harness-update` 并给文件级 proposal。

## 用户协作覆盖

- 本阶段没有 Assignment；不得补写或追认 Assignment。
- 阶段出口本轮不自动生成、提问或写入学习问答。Phase 6 `PASS` 后停止并等待用户主动触发答疑；触发后才读取 `docs/learning-notes/README.md`，逐题记录真实问答。
- 普通开发对话不进入开发日志；日志只写提交/出口所需真实结果、测试、失败修复、风险和成本。

## 改动边界

- 允许：修复出口检查发现的 Phase 6 代码/测试问题（每个安全修复小步提交）；最终更新 Phase 6 开发日志、必要的 PLAN/SPEC/DEVELOPMENT/ACCEPTANCE 已实现状态或命令证据；权威事实变化时通过 harness-update 同步 managed fingerprints；用户确认公开事实后才更新 README。
- 禁止：Phase 7 代码；PO Submit/后续 P2P；降低验收阈值；删除失败证据；把 lab/mock 当真实 ERP；先写 `PASS` 再审查；未授权 README/学习笔记；推送/Tag/发布/部署。

## 执行

1. 最终 Context Receipt：列出 HEAD/phase commits、工作区、需求矩阵、环境、全量命令、real artifacts、风险和审查输入。
2. 逐项 trace F-004–F-008：requirement → contract/design → code → unit/integration/E2E/browser/fault evidence → acceptance。缺一项标 gap，不能用文件存在替代。
3. 核对真实业务链至少各一次：
   - Buyer goal → deterministic investigation → typed MR proposal → explicit confirmation → precheck → one MR Draft → read-back/Receipt；
   - 同路径至 one PO Draft；
   - 无权限、未审批、过期、状态/权限/价格漂移、same/different digest、响应丢失、并发和 manual intervention。
4. 在每个真实场景断言最终 ERP DocType/name/docstatus/critical fields、单据数量、Run/Action/Approval/Reservation/Receipt/Reconciliation/Audit 关联和无多余 writes。
5. 运行全量静态/自动化：
   - `make format-check`
   - `make lint`
   - `make type`
   - `make unit`
   - `make integration`
   - Phase 6 专用 real HTTP/E2E/fault/process scripts
   - 登录态 browser acceptance
   - 固定 Frappe/ERPNext SHA 与 upstream clean
   - `git diff --check`
6. 安全/架构全检：Runtime 无 DB/ERP internal import/credential/writer；unknown/generic/Submit/后续 writes 不可达；权限/Workflow/digest/TOCTOU/idempotency/secret/XSS/SSRF/traceback/cookie/header/cancellation/timeout/cleanup 全部按影响覆盖。有限安全集要求 100%。
7. 运行 ponytail-audit 和 ponytail-debt，记录可以删除的复杂度与明确延期；不得为了得分自动删安全门禁。对每个发现标 blocking/修复/P2/P3。
8. 对 D1–D9 逐维 0–4 打分并链接证据；D1/D2/D3/D5/D7/D8 不低于 3，适用维度平均 ≥3.0，无 P0/P1。P2 需 owner、下一阶段门禁、复验命令；P3 入 backlog。
9. 更新风险表的实际 likelihood/impact/等级/处置；任何 unauthorized write、duplicate write、secret leak、unreviewed Workflow 或 audit distortion 为 P0/P1，先停止/隔离/修复。
10. 如检查失败：保留原始证据，按 PROMPT_INDEX §5 分类；只修真实根因；先重跑失败检查，再跑受影响 wider checks。安全修复每个可独立回滚结果一个 commit，commit 前日志更新。
11. 最终 diff 和全量证据稳定后，生成阶段报告草稿，明确未运行项、环境限制、P2/P3、测试数据清理/保留、无生产/客户/部署声明。
12. 自动交给独立对抗 Review：输入原始需求、Step 001 mapping、最终 diff、全部命令/exit codes、ERP artifacts、browser/fault evidence、Rubric/风险/报告草稿，不把 Execute 辩护当证据。
13. Review `CHANGES_REQUIRED`：回 Execute 修复/复验后最多再审一轮；第三次仍失败或 `BLOCKED`，阶段状态为 `BLOCKED`，不写通过报告。
14. Review `PASS` 后才运行 harness-check；若真实事实/命令/状态使 managed source drift，调用 harness-update 先提 proposal、再同步，并复跑 manifest/drift/reference/structure/`git diff --check`。
15. 在 Phase 6 开发日志顶部写最终出口轮次，记录真实 PASS、风险、成本、独立 verdict、未运行项；必要权威状态同步后提交 `docs(phase6): close governed write exit gate`。
16. 最终检查工作区与提交范围，交付 Phase 6 报告并停止。仅告知“答疑等待用户触发”，不生成学习笔记，不开始 Phase 7。

## 问题发现与修复

- 全绿但缺真实 ERP/response-loss/browser：判 evidence gap，不得用 unit test 补写 PASS。
- 审查发现 P1：先隔离 write endpoint/capability，再修复；保留失败和受影响单据盘点。
- Harness drift 只是旧 fingerprint：按 harness-update proposal 同步；若语义冲突，停止交用户。
- README 想更新但用户未确认公开事实：跳过并记录未更新原因，不阻塞代码阶段事实。
- 测试清理会删除不确定/用户数据：停止清理，只按已核验 Phase 6 test names 处理。
- 阶段报告文档明显多于业务代码：说明出口证据必要性；不扩写无事实内容。

## 验证与证据

完整保存命令、退出码、测试数、环境、ERP names/states/counts、Receipt/reconciliation ids、browser/fault artifacts、upstream SHAs/status、independent verdict、Harness outputs。未运行检查必须显式列出。

## 提交纪律

修复使用小 Conventional Commits；最终状态/Harness/日志用一个出口 docs commit。每个 commit 前更新同一 Phase 6 日志；不 squash、不改历史、不推送。

## 最终报告

先用大白话回答业务问题、用户可见结果、数据流、三个关键文件、手工验证；再报告 Phase 6 verdict、commits、命令/退出码、Rubric、风险、限制、独立审查和 Harness。明确停止在 Phase 6，答疑待用户触发。
