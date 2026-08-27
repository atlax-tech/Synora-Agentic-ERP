# Review — Step 007：Phase 6 最终独立对抗审查

## 审查输入

- 原始任务：完整 Phase 6 受治理 MR/PO Draft 首次写入闭环。
- 约束：AGENTS、PLAN、PRD F-004–F-008、ARCHITECTURE trust/approval、DESIGN high-risk、SPEC 7–11/14/16/17、ACCEPTANCE。
- 预期范围：Steps 001–006 全部 commits 与出口状态/Harness diff；无 Phase 7/Submit/后续 P2P。
- 必需证据：用户批准 mapping、最终 diff、全量 checks、真实 ERP/HTTP/browser/fault artifacts、upstream clean、Rubric/风险、阶段报告草稿、独立 Test verdict。

## 对抗问题

- 能否绕过 UI 直接调用 execute？能否伪造 actor/approval/digest/payload/idempotency key？
- permission/Workflow/Item/Supplier/price/amount/duplicate 在 approval 后变化时是否仍能写？
- same/different digest、并发、response loss、worker crash 是否可能产生第二单或错误成功？
- Receipt 是否来自 ERP read-back，是否能在无 target/verified fields 时伪造？
- Runtime/model/retrieval/ERP malicious text 是否能扩展 writer allowlist或注入 HTML/log？
- 跨用户/公司能否看到 proposal/Receipt/reconciliation/audit？
- PO Submit、后续 P2P、generic REST/SQL/MCP 是否真的不可达？
- 测试是否断言最终 ERP 状态，fault 是否是真实 post-commit response loss，browser 是否登录态实测？
- 开发日志、PLAN/Harness/README 声明是否不超过证据，上游是否零改动？
- 代码是否存在可删除的通用框架、重复安全逻辑或无法运维的事务/lease 设计？

## 严重度与门禁

- blocking/P0：未经授权写、Secret/跨租户泄漏、generic/Submit 可达、不可审计真实伤害。
- blocking/P1：TOCTOU、重复写、盲重试、Workflow 绕过、伪造 Receipt、真实故障/浏览器证据缺失。
- P2：受限但影响扩展/运维/证据可信度，必须有 owner/下一门禁/复验。
- P3：低风险改进，进入 backlog。

## 判定

返回且只返回 `PASS`、`CHANGES_REQUIRED` 或 `BLOCKED`。每个发现引用文件、测试或 artifact；不修改代码，不接受 Execute 的 Rubric/报告作为唯一证据。只有 `PASS` 才允许 Phase 6 出口报告。
