# Phase 10 Harness / README 文件级同步提案

提案 ID：`P10-HARNESS-CLOSE-20260910-v1`

生成时间：2026-09-10（Asia/Shanghai）

实现基线：`3512f9635d2dd04febce610f9ce9a2c00077f012`

固定上游：Frappe `6a329d068416768ec47ccd3326b9cc95a8d7bf99`；ERPNext `11e0ba0a1c45f217e2e73e885f699102d06da325`

## 提案目的

Phase 10 已在实现提交 `3512f96` 完成 P10.1–P10.9 代码、测试、真实开发 ERP 验收、故障恢复、浏览器证据和阶段报告。当前 Harness 仍保留 Phase 9 的管理指纹，因此只读 `detect_drift.py` 以退出码 `1` 报告以下真实 drift：

- `docs/PLAN.md`
- `synora_agentic_erp/agent/state_machine.py`
- `synora_agentic_erp/api.py`
- `synora_agentic_erp/synora_agentic_erp/page/runs/runs.js`

本提案给出最小、可复核的同步范围。它不直接修改 `.harness/`、README 或用户维护文件；只有 Harness 维护者/用户确认后，才能按本文件执行同步并复跑全部 Harness 检查。

## 文件边界

| 文件 | 当前归属 | 本提案动作 |
|---|---|---|
| `.harness/manifest.json` | harness-armor managed | 在确认后更新 Phase 10 已确认文档/源码/证据的 managed fingerprint、source-index fingerprint 和更新时间；不改规则或未决项语义 |
| `.harness/source-index.json` | harness-armor managed | 在确认后更新上述 drift 文件 SHA，并加入本阶段新增代码、测试、实验与验收证据的最小 source locator |
| `.harness/unresolved.json` | harness-armor managed | `NO_CHANGE`；Phase 10 没有证据支持关闭 model-selection、license、retrieval 或 frontend 之外的未决项 |
| `README.md` / `README.zh-CN.md` | 用户维护、公开事实需确认 | 仅在用户确认后更新 Phase 10 的公开状态、隔离开发 ERP 验收边界、无生产/银行转账声明；不写内部学习问答 |
| `docs/PLAN.md` | 用户当前未提交修改 | `EXCLUDE`；保留用户现有 Phase 13 文本修改，不能在同步时覆盖或混入本阶段提交 |
| `docs/DEVELOPMENT.md`、`docs/ROADMAP.md`、`docs/SPEC.md` 等权威文档 | harness-armor managed | 如维护者确认公开阶段状态需要同步，再按独立文档 diff 审批；本轮不伪造已同步状态 |

## `.harness/source-index.json` 建议增量

下面的记录是建议加入或更新的最小事实来源；`sha256` 为当前工作区字节哈希，执行同步前应重新计算并逐项核对。`locator` 只描述可定位事实，不授予代码执行权限。

| id | path | kind | status | sha256 | locator |
|---|---|---|---|---|---|
| `phase10-state-machine` | `synora_agentic_erp/agent/state_machine.py` | code | CONFIRMED | `b37d3642bf6bd6915293f603354627ea06538fa46b5f83ba16eb0ffebb43e970` | P10.5/P10.6 controlled cancellation, expiry and reconciliation transitions |
| `phase10-api` | `synora_agentic_erp/api.py` | code | CONFIRMED | `f8eb82cb636d18bef73b27836dd17c7c31598c625742797dcca576d9f164e796` | P2P chain projection plus resume/finalize/cancel Run endpoints; server-side Run authorization |
| `phase10-runs-ui` | `synora_agentic_erp/synora_agentic_erp/page/runs/runs.js` | code | CONFIRMED | `c67c2ca21f599af40ea882d78d66b6f7a17ec55815193abf0b26653dce57b63d` | Run page P2P chain, receipt/dependency states, recovery actions and accessible bilingual copy |
| `phase10-p2p-orchestration` | `synora_agentic_erp/governance/p2p_orchestration.py` | code | CONFIRMED | `f86a8ff82e0472e100ab3c9765c060873422577897d5322d7c56cd57aa692968` | durable PlanStep projection, dependency guards, reconciliation, reinvestigation and Run finalization |
| `phase10-p2p-execution` | `synora_agentic_erp/governance/p2p_execution.py` | code | CONFIRMED | `d541a547fdd43409af0a4346b582e1b693165efa51798d3e87be87c13b1d9618` | governed PO/PR/PI/Payment Entry create-submit-cancel execution and failure recovery |
| `phase10-execution` | `synora_agentic_erp/governance/execution.py` | code | CONFIRMED | `82d20fca50d7cc3e5d7275b2caad89b0b00943620ab8701451272597a495b59d` | typed action dispatch, reservation/receipt handling and restricted historical draft read-back |
| `phase10-contracts` | `synora_agentic_erp/governance/execution_contracts.py` | code | CONFIRMED | `0d9c35e5348eb98cbf3ae050ec5bd525737eeb5617af1433d1b77a6f758054` | P2P payload normalization, Decimal fields, receipt verification and docstatus policy |
| `phase10-plan-step-json` | `synora_agentic_erp/synora_agentic_erp/doctype/synora_p2p_plan_step/synora_p2p_plan_step.json` | code | CONFIRMED | `80d26e65dfe490d119ade20beb2b5764ebb28dde86916385a4a5cadec5cb7463` | persisted P2P PlanStep projection schema and read permission boundary |
| `phase10-plan-step-controller` | `synora_agentic_erp/synora_agentic_erp/doctype/synora_p2p_plan_step/synora_p2p_plan_step.py` | code | CONFIRMED | `eed29dd2cde295f80f5b7eb92ce3bf4db0796bbf3ef3863405df82eace0dd2b6` | service-only immutable projection controller |
| `phase10-e2e-tests` | `synora_agentic_erp/tests/test_phase10_e2e.py` | testing | CONFIRMED | `db9ab194ef23afb2a63b3ebcba914cf5cebe526fcfc5d4e0bc5c52dbaeeaec01` | three real Frappe tests for full P2P chain, response loss and missing Receipt recovery |
| `phase10-orchestration-tests` | `synora_agentic_erp/tests/test_phase10_orchestration.py` | testing | CONFIRMED | `7c11a4aed69de2133f54507f052ea068ea25c4259312315c88fbbb634f4eb559` | durable step status, dependency, resume, cancel and Run close assertions |
| `phase10-po-tests` | `synora_agentic_erp/tests/test_phase10_po_submit.py` | testing | CONFIRMED | `329aa44a63fae105cd18177b32269debb3734789d1e67befd9c8bcf9e7bb33f0` | PO submit independent approval, permission, workflow, idempotency and drift cases |
| `phase10-receipt-tests` | `synora_agentic_erp/tests/test_phase10_purchase_receipt.py` | testing | CONFIRMED | `2b3e1b8c7ab41f444d34fb50d506bb3e5ccb90b9e7fbbc9e1dcbc25bbc890311` | partial receipt, source-line quantity, UOM/warehouse and concurrency cases |
| `phase10-invoice-tests` | `synora_agentic_erp/tests/test_phase10_purchase_invoice.py` | testing | CONFIRMED | `96b389b549884ae3d74805cf70e14b0e2d35e4fd3734418bb27548593dd16e05` | partial invoice, tax/accounting, duplicate, outstanding and drift cases |
| `phase10-payment-tests` | `synora_agentic_erp/tests/test_phase10_payment_entry.py` | testing | CONFIRMED | `1ae3144fdc2ab6118e3dde207dd7844a25d5a39e90bafdee353f5075dc473801` | partial settlement, allocation, supplier/company/account and duplicate cases |
| `phase10-cancellation-tests` | `synora_agentic_erp/tests/test_phase10_cancellation.py` | testing | CONFIRMED | `99bb470d092c3c4b54d990ba79d5cd4ded9f91751dc1841f57cfbfdcc36c0106` | governed cancellation and downstream dependency/recovery cases |
| `phase10-lab` | `labs/p2p_orchestration/phase10_comparison.py` | code | CONFIRMED | `162e4c450a4958445317fa030b13bf19d3ffe53c34cdbf59c14b5b791fd4df5b` | LAB_ONLY deterministic single-agent/fixed-workflow/multi-agent comparison; no ERP capability |
| `phase10-lab-runner` | `services/agent_runtime/scripts/phase10_orchestration.py` | code | CONFIRMED | `4f9561ef92b5a4d14e0b0e58ecb438d3445e389c906a55bb7197c8984ec8c120` | reproducible P10.7 artifact generator and adoption decision |
| `phase10-lab-tests` | `tests/test_phase10_orchestration_lab.py` | testing | CONFIRMED | `18070001836c7f19be8dced26e366bfc9c90c62edfca4c1fd2e0d2f250e0a581` | event reorder/duplicate/restart and untrusted authorization-hint checks |
| `phase10-adoption-card` | `docs/adoption-cards/20260910-Phase10-p2p-orchestration.md` | development | CONFIRMED | `caf08123c26f8663d1ecfb0dbf5f911eb154989a225d80a7bd8b6ef3ec2b14ed` | fixed Workflow retained as business baseline; multi-agent remains lab-only |
| `phase10-e2e-artifact` | `output/phase10/phase10-p2p-e2e-recorded-v1.json` | acceptance | CONFIRMED | `4ea7000683ab81e45e34dcd19c9d12402723e4bd84f527221e4343f3a54d7514` | isolated dev.localhost real P2P result, 15 actions, Paid invoices and fault cases |
| `phase10-lab-artifact` | `output/phase10/phase10-p2p-orchestration-recorded-v1.json` | acceptance | CONFIRMED | `14c12d98cc7dd902ed093a11fd4f2e7aa3b22419103aac690dcc7c01f4224543` | same dataset/event matrix metrics and adoption decision |
| `phase10-browser-artifact` | `output/playwright/phase10-p2p-e2e-browser-acceptance.json` | acceptance | CONFIRMED | `dffdfec8ffbab84c1593466eb132f979c27c24ff936666ab2848acc021c675a7` | headed real-login Runs page evidence for SUCCEEDED and RECONCILIATION_REQUIRED |

阶段开发日志属于 managed 文件，应由 Harness 维护者在收口应用时按当前最终字节重新计算 fingerprint；为避免提案与日志互相引用造成循环哈希，本提案不内嵌日志自身 SHA。

截图的目标哈希也应作为 acceptance artifact 记录：

- `output/playwright/phase10-p2p-e2e-a1d259b2.png` → `9c92f55324ba2ea01f7fcf9b80797c27101bdd9a1814c338dfcdde2ceb02e0c8`
- `output/playwright/phase10-p2p-unknown-9ce932fa.png` → `4314b9a56a18ea0ab240c1d01433fd2cd741fcae6549ed8ccd40b28d4b2fd73c`

## README 最小公开事实 diff（建议，待确认）

英文与中文 README 的 Phase 10 状态行可以分别更新为同义表述：

> Phase 10 is `COMPLETED / PASS` on the pinned isolated development ERP, with governed PO→Purchase Receipt→Purchase Invoice→Payment Entry lifecycle, partial processing, cancellation/recovery and real-login Run-page evidence. This is development acceptance evidence only; no production deployment, bank transfer or customer adoption is claimed.

> Phase 10 已在固定隔离开发 ERP 完成 `COMPLETED / PASS`：PO→收货→发票→Payment Entry 受治理闭环、部分处理、取消/恢复及真实登录 Run 页面证据均已记录。本状态只代表开发验收，不代表生产部署、银行转账或客户采用。

同时将 Phase 10 checklist 从 `[ ]` 改为 `[x]`，并将“Phase 10 仍 separately staged”段落改成上述有界事实。不得把实验 test double 指标写成生产性能，不得公开测试账号、单据完整敏感字段、Cookie、Token 或供应商响应。

## 应用前后验证

维护者批准并应用后，必须在最终收口提交上重新计算所有哈希并依次运行：

```bash
python3 .agents/skills/harness-check/scripts/validate_manifest.py .
python3 .agents/skills/harness-build/scripts/validate_harness_structure.py .
python3 .agents/skills/harness-check/scripts/check_references.py .
python3 .agents/skills/harness-check/scripts/detect_drift.py .
git diff --check
```

预期：四项 Harness 检查与 `git diff --check` 均退出 `0`，`detect_drift.py` 的 drift 为空；`.harness/unresolved.json` 的未决项数量和状态不因本阶段被虚构改变。若 `docs/PLAN.md` 仍有用户未提交修改，应在复验结果中单独标记并通过用户维护流程处理，不得把它隐藏在同步提交中。

当前阶段在同步前的状态是：业务实现与独立审查通过，受保护文件同步等待确认；本提案不构成已应用的 Harness 或 README 变更。
