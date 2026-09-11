# Phase 10 Harness / README 文件级同步提案（R10.5）

提案 ID：`P10-HARNESS-CLOSE-20260911-v2`

生成基线：`80abbbafba6ec4f443bab3f86f43356dee5bf9c0`

固定上游：Frappe `6a329d068416768ec47ccd3326b9cc95a8d7bf99`；ERPNext `11e0ba0a1c45f217e2e73e885f699102d06da325`

## 提案目的

R10.5 业务代码、真实 ERP/浏览器/故障证据和全量门禁已完成。只读 `detect_drift.py` 仍以退出码 `1` 报告真实 drift：用户维护的 `docs/PLAN.md`，以及 Harness source-index 中待刷新 fingerprint 的 8 个源码文件。本提案只给出文件级、可复核的同步范围，不直接修改 `.harness/`、README 或 `docs/PLAN.md`。

应用前必须获得用户/Harness 维护者对本文件范围的单独确认。确认后按当前最终字节重新计算 fingerprint；不能直接复制本表作为最终 managed 状态。

## 当前 drift 与精确 SHA-256

| 文件 | 当前 SHA-256 | 建议动作 | 归属 |
|---|---|---|---|
| `docs/PLAN.md` | `3885499552adeb01e2561e51af81fefd8ecfc7b1be3e7152bd08aedbd62874c0` | `EXCLUDE`；保留用户 Phase 13 修改 | 用户维护 |
| `services/agent_runtime/src/agent_runtime/app.py` | `c7e405406e2eaf5ed26ca1c294b73af87cb0e3e9d8d356ad3858550f8aa1cd2d` | source-index fingerprint 更新 | managed source |
| `services/agent_runtime/src/agent_runtime/workflow/contracts.py` | `a88cdb8a05a42643207ecc0893df5b38eb74780e7e1362b7b9c7a9889be3e649` | source-index fingerprint 更新 | managed source |
| `services/agent_runtime/src/agent_runtime/workflow/engine.py` | `aea8232933b85e78690964b1c6d84981241693e38b1db5de7a4e909ed4ea7949` | source-index fingerprint 更新 | managed source |
| `services/agent_runtime/src/agent_runtime/workflow/runtime.py` | `518a5262ebb0dc727c3a4cf32ae07a7e5051c41855f457610853998e0309e013` | source-index fingerprint 更新，保留 P2P 工具步骤 fail-closed 事实定位 | managed source |
| `synora_agentic_erp/agent/service.py` | `159347a2a454e19b5729dba82b18c9500db5fb2c7fe703c0b8eda03d2f847a02` | source-index fingerprint 更新 | managed source |
| `synora_agentic_erp/agent/state_machine.py` | `299541b7c158fcb0a11a296d7903fc13d45823bc8b0f011ceeb38df2ba97cb93` | source-index fingerprint 更新 | managed source |
| `synora_agentic_erp/api.py` | `9820bd536179f4ea74a4b39ac7253d782b1b24af558f8299e701eec1a63879ea` | source-index fingerprint 更新 | managed source |
| `synora_agentic_erp/synora_agentic_erp/page/runs/runs.js` | `9038f4218b099ef9594609b12bb6d1ff22a1ff6770410eba933bac63d3040b76` | source-index fingerprint 更新 | managed source |

这些 SHA 来自 R10.5 只读 drift 输出和同一工作树的程序化计算；应用同步前必须再次计算，不能手录覆盖差异。

## Managed 文件动作

| 文件 | 动作 | 说明 |
|---|---|---|
| `.harness/manifest.json` | `UPDATE_AFTER_APPROVAL` | 更新已确认 source/document fingerprint 和更新时间；不改规则或未决项语义 |
| `.harness/source-index.json` | `UPDATE_AFTER_APPROVAL` | 刷新上述 8 个源码 SHA，并保留已有最小 locator；需要时补充本阶段 acceptance artifact locator |
| `.harness/unresolved.json` | `NO_CHANGE` | Phase 10 没有证据支持关闭 model-selection、license、retrieval 或 frontend 未决项 |
| `docs/PLAN.md` | `EXCLUDE` | 保留用户当前未提交 Phase 13 修改，不加入同步提交 |
| `README.md` / `README.zh-CN.md` | `OPTIONAL_AFTER_PUBLIC-FACT_APPROVAL` | 只写有证据的开发验收边界，不写生产部署、银行转账、客户采用或 LAB_ONLY 性能 |
| `docs/DEVELOPMENT.md`、`docs/ROADMAP.md`、`docs/SPEC.md` 等权威文档 | `REVIEW_REQUIRED` | 只有维护者确认公开阶段状态需要同步时才产生独立 diff；不在本提案中假设已完成 |

## 建议加入/确认的来源定位

- P2P 业务：`synora_agentic_erp/governance/p2p_execution.py`、`synora_agentic_erp/governance/p2p_orchestration.py`、`synora_agentic_erp/governance/contracts.py`。
- Runtime 边界：`services/agent_runtime/src/agent_runtime/workflow/contracts.py`、`engine.py`、`runtime.py`；P2P 请求不能执行普通 tool step。
- 页面事实：`synora_agentic_erp/synora_agentic_erp/page/runs/runs.js`；目标版本、依赖、Receipt、恢复和双语状态展示。
- 验收证据：`output/phase10/phase10-r104-real-fault-matrix-v1.json`、`phase10-r104-browser-acceptance-v2.json`、`phase10-p2p-orchestration-real-glm-v1.json` 和本阶段 R10.5 报告。
- 受保护同步本身不改变旧 artifact；R10.4 artifact 的真实源绑定仍为基线 `4381d47` 加当时工作树 diff，R10.5 报告对此有明确说明。

## README 最小公开事实草稿（待单独确认）

英文：

> Phase 10 is `COMPLETED / PASS` on the pinned isolated development ERP, with governed PO→Purchase Receipt→Purchase Invoice→Payment Entry lifecycle, partial processing, cancellation/recovery and real-login Run-page evidence. This is development acceptance evidence only; no production deployment, bank transfer or customer adoption is claimed.

中文：

> Phase 10 已在固定隔离开发 ERP 完成 `COMPLETED / PASS`：PO→收货→发票→Payment Entry 受治理闭环、部分处理、取消/恢复及真实登录 Run 页面证据均已记录。本状态只代表开发验收，不代表生产部署、银行转账或客户采用。

不公开测试账号、完整单据敏感字段、Cookie、Token、Provider raw response；不把 LAB_ONLY 的延迟/token 写成生产性能。

## 应用后复验

维护者确认并应用后，必须在最终同步工作树运行：

```bash
python3 .agents/skills/harness-check/scripts/validate_manifest.py .
python3 .agents/skills/harness-build/scripts/validate_harness_structure.py .
python3 .agents/skills/harness-check/scripts/check_references.py .
python3 .agents/skills/harness-check/scripts/detect_drift.py .
git diff --check
```

预期结构、manifest、references 和 `git diff --check` 退出码为 `0`，`detect_drift.py` 的 drift 为空；若 `docs/PLAN.md` 仍存在用户未提交修改，必须单独标记，不得隐藏在同步提交中。

当前状态：业务实现和 R10.5 门禁已通过；独立对抗审查两轮均为 `PASS`；受保护同步已获用户对本文件级范围的授权，待按本提案应用。本提案不授权 `docs/PLAN.md`、README 或 `REVIEW_REQUIRED` 权威文档之外的范围。

## R10.6 实际应用记录（2026-09-11）

用户随后明确要求继续执行 Phase 10 收口，并授权按上述文件级提案完成 R10.6 受保护同步。结合该继续指令，原提案中“待确认”的 README、权威阶段文档和 `docs/PLAN.md` 状态同步已在本阶段范围内执行：`docs/PLAN.md` 仅更新 Phase 10 状态和区块，用户已提交的 Phase 13 原文保持不变；README 只写固定隔离开发 ERP 的已验证事实；`.harness/unresolved.json` 保持 `NO_CHANGE`。

最终字节指纹由 `.harness/source-index.json` 和 `.harness/manifest.json` 程序化刷新，未手录复制提案中的旧 SHA。最终状态、命令退出码、引用计数、drift、health 和工作树复验见 `output/phase10/phase10-final-manifest-final-80abbba.json`；历史 R10.5 报告和本提案的原始范围说明均保留，不被改写成新的业务证据。
